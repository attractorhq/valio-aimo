"""T41: the trust card, the decision text and the decision log show the requirement evidence the policy used.

The policy reads every allergen declaration (including GS1 `nonAllergen` FREE_FROM). Product facts only
carried the `allergen` group, so the card showed "No egg: Unknown" while the footer said "proven". The
gateway now attaches one requirement fact per hard requirement, built from the policy's own evaluation.
"""

from decimal import Decimal

import pytest

from app.agent.explain import _is_proven, requirement_evidence, template_explanation
from app.catalog.normalise import normalise_product
from app.contracts import GetOfferRequest, OfferItem, RequestItem, Requirement, StructuredRequest
from app.gateway.offers import requirement_facts, with_requirement_facts
from app.gateway.proposals import ProposalGateway, create_proposal, get_audit_trail
from app.policy import M1, M2, allergen_status, check_requirements
from tests.synthetic.catalog_records import synthetic_product_record
from tests.synthetic.policy import product
from tests.test_proposals_gateway import (
    MONDAY,
    MutableSyntheticProvider,
    _decl,
    price_row,
    stock_row,
    syn_product,
)

AE = Requirement(kind="AVOID_ALLERGEN", code="AE")
AM = Requirement(kind="AVOID_ALLERGEN", code="AM")
AN = Requirement(kind="AVOID_ALLERGEN", code="AN")
GLUTEN_FREE = Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")
STANDARD = M1
STRICT = M2


# --------------------------------------------------------------------------- the reported cause


def test_non_allergen_free_from_is_evidence_for_the_requirement():
    """Real-shaped record: egg and milk free declared only in the GS1 nonAllergen group."""
    record = synthetic_product_record(
        product_id="SYN-MAYO",
        names={"en": "Synthetic egg-free mayonnaise 1 kg", "fi": "Synteettinen munaton majoneesi 1 kg"},
        category="21380",
        allergens=[("AY", "CONTAINS")],
        non_allergens=[("AE", "FREE_FROM"), ("AM", "FREE_FROM")],
        key_ingredients={"en": "rapeseed oil, water, soy protein"},
    )
    mayo = normalise_product(record)
    # The cause: the policy reads FREE_FROM, but no allergen.AE fact reached the line facts.
    assert allergen_status(mayo, "AE") == "FREE_FROM"
    assert not any(f.fact == "allergen.AE" for f in mayo.facts)

    egg, milk = requirement_facts(mayo, [AE, AM], STRICT)
    assert (egg.fact, egg.value, egg.level, egg.display) == ("allergen.AE", "FREE_FROM", "DECLARED", "Free from eggs")
    assert egg.source.field.endswith("[nonAllergen]")
    assert (milk.fact, milk.value, milk.level) == ("allergen.AM", "FREE_FROM", "DECLARED")
    assert check_requirements(mayo, [AE, AM], STRICT) == ()


def test_requirement_facts_replace_a_stale_fact_with_the_same_key():
    p = product(allergens={"AE": "FREE_FROM"})
    stale = requirement_facts(product(allergens={"AE": "CONTAINS"}), [AE], STANDARD)[0]
    facts = with_requirement_facts([stale], p, [AE], STANDARD)
    assert [(f.fact, f.value) for f in facts] == [("allergen.AE", "FREE_FROM")]


def test_mandate_forbidden_allergens_are_evidenced_too():
    mandate = STANDARD.model_copy(update={"forbiddenAllergens": ["AP"]})
    facts = requirement_facts(product(allergens={"AE": "FREE_FROM", "AP": "FREE_FROM"}), [AE], mandate)
    assert [(f.fact, f.value) for f in facts] == [("allergen.AE", "FREE_FROM"), ("allergen.AP", "FREE_FROM")]


def test_claims_from_a_packaging_label_and_missing_claims():
    vegan = Requirement(kind="CLAIM", code="VEGAN")
    labelled = requirement_facts(product(labels=("VEGAN",)), [vegan], STANDARD)[0]
    assert (labelled.fact, labelled.value, labelled.method) == ("claim.VEGAN", True, "label:VEGAN")
    missing = requirement_facts(product(), [GLUTEN_FREE], STANDARD)[0]
    assert (missing.fact, missing.value, missing.level) == ("claim.FREE_FROM_GLUTEN", False, "UNKNOWN")


# --------------------------------------------------------------------------- same data as the policy

CASES = {
    "free-from": ({"AE": "FREE_FROM"}, None),
    "contains": ({"AE": "CONTAINS"}, None),
    "may-contain": ({"AE": "MAY_CONTAIN"}, None),
    "undeclared": ({"AE": "UNDECLARED"}, None),
    "not-listed": ({"AM": "CONTAINS"}, None),
    "no-declaration": ({}, False),
    "x99-eu14": ({"X99": "FREE_FROM"}, None),
    "subcode-of-free-group": ({"AN": "FREE_FROM"}, None),
    "lactose-implies-milk": ({"ML": "CONTAINS"}, None),
}


@pytest.mark.parametrize("mandate", [STANDARD, STRICT], ids=["standard", "strict"])
@pytest.mark.parametrize("requirement", [AE, AM, AN, Requirement(kind="AVOID_ALLERGEN", code="UH")],
                         ids=["AE", "AM", "AN", "UH-hazelnut"])
@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
def test_evidence_matches_the_policy_decision(case, requirement, mandate):
    allergens, declared = CASES[case]
    p = product(allergens=allergens, declared=declared)
    (fact,) = requirement_facts(p, [requirement], mandate)
    status = allergen_status(p, requirement.code)
    assert fact.fact == f"allergen.{requirement.code}"
    assert fact.value == (None if status == "UNKNOWN" else status)
    findings = check_requirements(p, [requirement], mandate)
    if not findings:
        # A line the policy keeps: the card and the decision text can only say "proven".
        assert _is_proven(requirement, fact)
    if not _is_proven(requirement, fact):
        # Evidence that does not prove the requirement always comes with an excluding finding.
        assert findings


def test_x99_never_proves_a_non_eu14_code():
    p = product(allergens={"X99": "FREE_FROM"})
    (fact,) = requirement_facts(p, [Requirement(kind="AVOID_ALLERGEN", code="ZZ")], STANDARD)
    assert fact.level == "UNKNOWN" and fact.value is None


# --------------------------------------------------------------------------- gateway: card, log and text


def _gateway_with(product_obj):
    provider = MutableSyntheticProvider()
    provider.prices[product_obj.productId] = price_row(product_obj.productId, 22.92)
    provider.stocks[product_obj.productId] = stock_row(product_obj.productId, 50, sales_unit="PSS")
    return ProposalGateway(products={product_obj.productId: product_obj}, provider=provider)


def test_proposal_lines_and_the_policy_log_carry_the_requirement_facts():
    pasta = syn_product(
        "SYN-GF-PASTA",
        sales_unit="PSS",
        content=("5", "kg"),
        method="unitConversions.BASE_UNIT_OR_EACH.netWeight x units.PSS.sizeInBaseUnits",
        claims=("FREE_FROM_GLUTEN",),
        allergens=(_decl("AM"), _decl("AN"), _decl("AE")),
    )
    assert not any(f.fact.startswith("allergen.") for f in pasta.facts)  # what the card used to see
    gateway = _gateway_with(pasta)
    offer_req = GetOfferRequest(
        customerId=STRICT.customerId, requestedDate=MONDAY,
        items=[OfferItem(itemId="I1", productId=pasta.productId, quantity=Decimal("20"), unit="kg")],
    )
    request = StructuredRequest(
        requestText="20 kg gluten-free, milk-free pasta, one pupil has a nut allergy, by Monday.",
        requestedDate=MONDAY,
        items=[RequestItem(itemId="I1", what="pasta", quantity=Decimal("20"), unit="kg",
                           requirements=[GLUTEN_FREE, AM, AN])],
    )
    offer = gateway.get_offer(offer_req).offer
    proposal = create_proposal(gateway, STRICT.customerId, offer.offerId, offer.offerVersion, request)
    assert proposal.decision in ("AUTO", "ASK")

    (line,) = proposal.lines
    evaluated = {f.fact: f for f in line.facts}
    assert evaluated["claim.FREE_FROM_GLUTEN"].value is True
    for code in ("AM", "AN"):
        assert (evaluated[f"allergen.{code}"].value, evaluated[f"allergen.{code}"].level) == ("FREE_FROM", "DECLARED")
    assert len([f for f in line.facts if f.fact == "allergen.AM"]) == 1

    trail = get_audit_trail(gateway, proposal_id=proposal.proposalId)
    (decided,) = [e for e in trail.entries if e.event == "POLICY_DECIDED"]
    logged = {f.fact for f in decided.facts}
    assert {"claim.FREE_FROM_GLUTEN", "allergen.AM", "allergen.AN"} <= logged

    evidence = requirement_evidence(proposal, STRICT)
    assert evidence and all(e["proven"] for e in evidence)
    text = template_explanation(proposal=proposal, mandate=STRICT)
    assert "not proven" not in text
    assert "gluten-free" in text and "milk-free" in text

    # The text follows the evidence: an Unknown fact on the line can never be called proven.
    unknown = evaluated["allergen.AN"].model_copy(update={"value": None, "level": "UNKNOWN", "display": "Unknown"})
    tampered = proposal.model_copy(update={"lines": [line.model_copy(update={
        "facts": [unknown if f.fact == "allergen.AN" else f for f in line.facts]})]})
    assert not all(e["proven"] for e in requirement_evidence(tampered, STRICT))
    tampered_text = template_explanation(proposal=tampered, mandate=STRICT)
    assert "Every hard requirement" not in tampered_text and "All hard requirements" not in tampered_text
    assert "not proven" in tampered_text
