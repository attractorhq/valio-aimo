"""T77: material variant choices the buyer did not state become editable assumptions; an item is never filled
with two different use variants (e.g. fresh + frozen). Synthetic hand-made records only (patch P7)."""

from decimal import Decimal

from test_proposals_gateway import MONDAY, NOW, MutableSyntheticProvider, price_row, stock_row

from app.agent.plan import (
    CandidateObservation,
    build_shortlist_offer_items,
    keep_chosen_variants,
    rank_candidates,
    variant_choices,
)
from app.catalog.normalise import normalise_product
from app.contracts import GetOfferRequest, ReasonCode, RequestItem, StructuredRequest
from app.gateway.proposals import ProposalGateway
from app.policy import DEMO_MANDATES, M1, MandateBook
from tests.synthetic.catalog_records import synthetic_product_record


def _product(product_id: str, en: str, fi: str, *, category: str, unit: str = "kg", size: float = 1.0,
             claims: list[str] | None = None, labels: list[str] | None = None):
    measure = {"netWeight": {"unit": "KGM", "value": size}} if unit == "kg" else {
        "volume": {"unit": "LTR", "value": size}}
    rec = synthetic_product_record(
        product_id=product_id, category=category, names={"en": en, "fi": fi},
        allergens=[("AM", "CONTAINS")], claims=claims or [], labels=labels or [],
        unit_conversions=[{"synkkaPackagingType": "BASE_UNIT_OR_EACH", **measure}],
    )
    return normalise_product(rec, reliability=1.0, co2=1.0)


def _obs(code: str, price: str = "10.00", amount: int = 1000) -> CandidateObservation:
    return CandidateObservation(
        product_id=code, unit_price_excl_vat=Decimal(price), comparison_price=Decimal(price), comparison_unit="kg",
        vat_rate=Decimal("14"), stock_tier="EXACT", available_quantity=Decimal(amount), delivery_date=MONDAY,
        closing_time=None, reasons=(), facts=(),
    )


UNSALTED = _product("SYN-BUT-U", "Synthetic butter 500 g unsalted", "Synteettinen voi 500 g suolaamaton",
                    category="10500", size=0.5)
SALTED = _product("SYN-BUT-S", "Synthetic butter 500 g salted", "Synteettinen voi 500 g normaalisuolainen",
                  category="10500", size=0.5, claims=["FREE_FROM_LACTOSE"])
FRESH = _product("SYN-SAL-F", "Synthetic salmon fillet C 10 kg", "Synteettinen lohifilee C 10 kg",
                 category="18701", size=10)
FROZEN = _product("SYN-SAL-Z", "Synthetic salmon fillet C 10 kg frozen", "Synteettinen lohifilee pakaste",
                  category="18701", size=10)
COOKING = _product("SYN-CR-C", "Synthetic cooking cream 15 % 1 l", "Synteettinen ruokakerma 15 % 1 l",
                   category="10401", unit="l")
WHIPPING = _product("SYN-CR-W", "Synthetic whipping cream 1 l", "Synteettinen vispikerma 1 l",
                    category="10401", unit="l")
PRODUCTS = {p.productId: p for p in (UNSALTED, SALTED, FRESH, FROZEN, COOKING, WHIPPING)}


def _ranked(item, products, prices):
    return rank_candidates(products, item=item, mandate=M1, requested_date=MONDAY,
                           observations={p.productId: _obs(p.productId, prices[p.productId]) for p in products})


def test_butter_with_both_salt_variants_gets_an_editable_salt_chip():
    item = RequestItem(itemId="I1", what="butter", quantity=Decimal(12), unit="kg")
    ranked = _ranked(item, [UNSALTED, SALTED], {"SYN-BUT-U": "4.00", "SYN-BUT-S": "4.10"})
    choices = variant_choices(item, ranked, PRODUCTS)
    chips = [c.chip for c in choices]
    assert "Salt: unsalted" in chips
    # T117: lactose is never stated as an assumption the buyer did not make.
    assert not any(chip.startswith(("Lactose", "Organic")) for chip in chips)
    salt = next(c for c in choices if c.label == "Salt")
    assert salt.restricts and salt.others == ["salted"]
    assert [rc.productId for rc in keep_chosen_variants(ranked, choices, PRODUCTS)] == ["SYN-BUT-U"]


def test_a_stated_variant_is_not_asked_again():
    item = RequestItem(itemId="I1", what="salted butter", quantity=Decimal(12), unit="kg")
    ranked = _ranked(item, [UNSALTED, SALTED], {"SYN-BUT-U": "4.00", "SYN-BUT-S": "4.10"})
    assert not any(c.label == "Salt" for c in variant_choices(item, ranked, PRODUCTS))


def test_generic_cream_names_the_chosen_cream_type():
    item = RequestItem(itemId="I1", what="cream", quantity=Decimal(60), unit="l")
    ranked = _ranked(item, [COOKING, WHIPPING], {"SYN-CR-C": "2.00", "SYN-CR-W": "3.00"})
    choice = next(c for c in variant_choices(item, ranked, PRODUCTS) if c.label == "Cream type")
    assert (choice.chip, choice.others) == ("Cream type: cooking cream", ["whipping cream"])


def test_an_unknown_value_is_said_to_be_unknown():
    plain = _product("SYN-BUT-P", "Synthetic butter 500 g", "Synteettinen voi 500 g", category="10500", size=0.5)
    products = {**PRODUCTS, plain.productId: plain}
    item = RequestItem(itemId="I1", what="butter", quantity=Decimal(12), unit="kg")
    ranked = _ranked(item, [plain, UNSALTED, SALTED], {"SYN-BUT-P": "3.00", "SYN-BUT-U": "4.00", "SYN-BUT-S": "4.10"})
    assert "Salt: unknown" in [c.chip for c in variant_choices(item, ranked, products)]


def test_pack_size_chip_only_for_a_fivefold_spread():
    big = _product("SYN-BUT-B", "Synthetic butter 5 kg unsalted", "Synteettinen voi 5 kg suolaamaton",
                   category="10500", size=5)
    products = {**PRODUCTS, big.productId: big}
    item = RequestItem(itemId="I1", what="unsalted butter", quantity=Decimal(10), unit="kg")
    ranked = _ranked(item, [UNSALTED, big], {"SYN-BUT-U": "4.00", "SYN-BUT-B": "4.50"})
    choice = next(c for c in variant_choices(item, ranked, products) if c.label == "Pack size")
    assert (choice.chip, choice.others) == ("Pack size: 500 g", ["5 kg"])
    two = _product("SYN-BUT-2", "Synthetic butter 2 kg unsalted", "Synteettinen voi 2 kg suolaamaton",
                   category="10500", size=2)
    ranked = _ranked(item, [UNSALTED, two], {"SYN-BUT-U": "4.00", "SYN-BUT-2": "4.50"})
    assert not any(c.label == "Pack size" for c in variant_choices(item, ranked, {**PRODUCTS, "SYN-BUT-2": two}))


def test_fresh_and_frozen_are_never_mixed_and_the_shortfall_is_not_auto():
    item = RequestItem(itemId="I1", what="salmon fillet", quantity=Decimal(100), unit="kg")
    ranked = _ranked(item, [FRESH, FROZEN], {"SYN-SAL-F": "100.00", "SYN-SAL-Z": "120.00"})
    choices = variant_choices(item, ranked, PRODUCTS)
    assert "Fresh or frozen: fresh" in [c.chip for c in choices]
    offer_items = build_shortlist_offer_items(item, ranked_candidates=keep_chosen_variants(ranked, choices, PRODUCTS))
    assert [o.productId for o in offer_items] == ["SYN-SAL-F"]

    # Fresh stock covers half: the item stays fresh only and the proposal asks a human (never AUTO).
    provider = MutableSyntheticProvider()
    for code, amount in (("SYN-SAL-F", 5), ("SYN-SAL-Z", 100)):
        provider.prices[code] = price_row(code, "100.00")
        provider.stocks[code] = stock_row(code, amount)
    gateway = ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer = gateway.get_offer(GetOfferRequest(customerId=M1.customerId, requestedDate=MONDAY, items=offer_items)).offer
    request = StructuredRequest(requestText="100 kg salmon fillet by Monday", requestedDate=MONDAY, items=[item],
                                assumptions=[c.chip for c in choices])
    proposal = gateway.create_proposal(M1.customerId, offer.offerId, offer.offerVersion, request, now=NOW)
    assert [ln.productId for ln in proposal.lines] == ["SYN-SAL-F"]
    assert proposal.decision == "ASK" and ReasonCode.PARTIAL_AVAILABILITY in proposal.reasons
    assert "Fresh or frozen: fresh" in proposal.request.assumptions


def test_a_single_candidate_has_no_choice_to_state():
    item = RequestItem(itemId="I1", what="butter", quantity=Decimal(12), unit="kg")
    assert variant_choices(item, _ranked(item, [UNSALTED], {"SYN-BUT-U": "4.00"}), PRODUCTS) == []
