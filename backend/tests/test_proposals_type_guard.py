"""T96: buyer product types are enforced on the server using hand-made synthetic records only."""

from dataclasses import replace

import pytest
from test_api import api as api
from test_api import item, offer_and_propose
from test_proposals_gateway import _decl, price_row, stock_row, syn_product

from app.contracts import ReasonCode, RequestItem
from app.policy import M1, M2, evaluate_line
from tests.synthetic.policy import MONDAY, NOW, line


def add_product(api, *, name, category, allergens=()):
    _, provider, state = api
    product = syn_product("SYN-TYPE", name=name, category=category, content=("1", "kg"), allergens=allergens)
    state.gateway.products[product.productId] = product
    provider.prices[product.productId] = price_row(product.productId, "1.00")
    provider.stocks[product.productId] = stock_row(product.productId, 100)
    return product


@pytest.mark.parametrize("what", ["grated cheese", "juustoraaste", "riven ost"])
def test_cabbage_for_grated_cheese_is_excluded_over_public_api(api, what):
    client, provider, state = api
    product = add_product(api, name="Synthetic grated cabbage", category="17400", allergens=(_decl("X99"),))
    _, created = offer_and_propose(
        client, M2.customerId, [("I1", product.productId, "1", "kg")],
        "One kilogram of grated cheese by Monday.", [item("I1", what, "1", "kg")],
    )
    assert created.status_code == 422, created.text
    error = created.json()["error"]
    assert error["code"] == "NO_ORDER"
    excluded = error["details"]["excluded"]
    assert any(entry["productId"] == product.productId and "UNIT_MISMATCH" in entry["reasons"] for entry in excluded)
    assert "Unrelated product type" in str(excluded)
    assert not state.gateway._proposals
    assert len(provider.calls) == 1  # No confirmation lookup is attempted.


@pytest.mark.parametrize("what", ["oat yogurt", "kaurajogurtti", "havreyoghurt"])
def test_soy_for_oat_yogurt_requires_approval_and_discloses_soy(api, what):
    client, provider, _ = api
    product = add_product(api, name="Synthetic soy yogurt", category="10413", allergens=(_decl("AY", "CONTAINS"),))
    _, created = offer_and_propose(
        client, M2.customerId, [("I1", product.productId, "1", "kg")],
        "One kilogram of oat yogurt by Monday.", [item("I1", what, "1", "kg")],
    )
    assert created.status_code == 201, created.text
    proposal = created.json()
    assert (proposal["decision"], proposal["state"], proposal["reasons"]) == (
        "ASK", "PENDING_APPROVAL", ["AGENT_SUBSTITUTE"],
    )
    selected = proposal["lines"][0]
    assert selected["substitutionSource"] == "AGENT"
    assert selected["reasons"] == ["AGENT_SUBSTITUTE"]
    disclosure = next(fact for fact in selected["facts"] if fact["fact"] == "substitute.allergen.AY")
    assert disclosure["display"] == "Contains soy"
    assert disclosure["level"] == product.allergens[0].evidence.level
    assert disclosure["source"] == product.allergens[0].evidence.source.model_dump(mode="json")
    assert "soy-based, not oat-based" in str(selected["facts"])
    assert len(provider.calls) == 1
    # A human may approve this exact, fully evidenced trade-off; re-check must not loop into supersession.
    view = client.client.get(f"/v0.1/approver/proposals/{proposal['proposalId']}").json()
    assert "Contains soy" in str(view["proposal"]["lines"])
    approved = client.client.post(f"/v0.1/approver/proposals/{proposal['proposalId']}/decision", json={
        "offerVersion": proposal["offerVersion"], "approvalToken": view["approvalToken"], "action": "APPROVE",
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "CONFIRMED_SIMULATED"
    assert len(provider.calls) == 2


@pytest.mark.parametrize("name,category,what", [
    ("Synthetic Edam 17 % slices", "10631", "cheese"),
    ("Synthetic oat yogurt", "10413", "oat yogurt"),
    ("Synthetic soy-free oat yogurt", "10413", "oat yogurt"),
    ("Synthetic soijaton kaurajogurtti", "10413", "kaurajogurtti"),
    ("Synthetic soy yogurt", "10413", "plant-based yogurt"),
    ("Synthetic grated Emmental", "10631", "grated cheese"),
    ("Synthetic penne", "21380", "pasta"),
    ("Synthetic Greek style", "10412", "yogurt"),
    ("Synthetic soy yogurt", "10413", "oat yogurt or soy yogurt"),
])
def test_same_type_and_explicit_alternatives_remain_exact(api, name, category, what):
    client, _, _ = api
    product = add_product(api, name=name, category=category, allergens=(_decl("X99"),))
    _, created = offer_and_propose(
        client, M2.customerId, [("I1", product.productId, "1", "kg")],
        "One kilogram by Monday.", [item("I1", what, "1", "kg")],
    )
    assert created.status_code == 201, created.text
    assert created.json()["decision"] == "AUTO"
    assert created.json()["state"] == "CONFIRMED_SIMULATED"
    assert created.json()["reasons"] == []


@pytest.mark.parametrize("name,category,what", [
    ("Synthetic cooking cream", "10401", "whipping cream"),
    ("Synthetic Edam", "10631", "Emmental cheese"),
    ("Synthetic penne", "21380", "macaroni"),
    ("Synthetic cheese slices", "10631", "grated cheese"),
    ("Synthetic soy yogurt", "10413", "yogurt"),
    ("Synthetic oat and soy yogurt", "10413", "oat yogurt"),
    ("Synthetic dairy yogurt", "10412", "plant-based yogurt"),
])
def test_related_type_or_form_change_is_ask(api, name, category, what):
    client, _, _ = api
    product = add_product(api, name=name, category=category, allergens=(_decl("X99"),))
    _, created = offer_and_propose(
        client, M2.customerId, [("I1", product.productId, "1", "kg")],
        "One kilogram by Monday.", [item("I1", what, "1", "kg")],
    )
    assert created.status_code == 201, created.text
    assert created.json()["decision"] == "ASK"
    assert created.json()["reasons"] == ["AGENT_SUBSTITUTE"]


def test_a_type_changing_valio_substitute_cannot_bypass_the_agent_substitute_gate(api):
    _, _, state = api
    product = add_product(api, name="Synthetic soy yogurt", category="10413", allergens=(_decl("AY", "CONTAINS"),))
    requested = item("I1", "oat yogurt", "10", "kg")
    verdict = evaluate_line(
        replace(line(prod=product, unit="kg", substitution="VALIO"), check_requested_type=True),
        item=RequestItem.model_validate(requested), mandate=M1, requested_date=MONDAY, now=NOW,
    )
    assert verdict.reasons == (ReasonCode.AGENT_SUBSTITUTE,)
    assert verdict.line.substitution == "AGENT"
    assert "Contains soy" in verdict.findings[0].detail
    assert state.gateway.mandates.get(M1.customerId).autoAcceptValioSubstitutes


def test_soy_avoidance_cannot_be_waived_by_substitution_approval(api):
    client, _, state = api
    product = add_product(api, name="Synthetic soy yogurt", category="10413", allergens=(_decl("AY", "CONTAINS"),))
    _, created = offer_and_propose(
        client, M2.customerId, [("I1", product.productId, "1", "kg")], "Oat yogurt without soy.",
        [item("I1", "oat yogurt", "1", "kg", [{"kind": "AVOID_ALLERGEN", "code": "AY"}])],
    )
    assert created.status_code == 422, created.text
    assert "ALLERGEN_CONTAINS" in created.text
    assert not state.gateway._proposals


@pytest.mark.parametrize("change", ["base", "allergen", "unrelated"])
def test_changed_type_or_allergen_disclosure_voids_previous_approval(api, change):
    client, _, state = api
    product = add_product(api, name="Synthetic soy yogurt", category="10413", allergens=(_decl("AY", "CONTAINS"),))
    _, created = offer_and_propose(
        client, M2.customerId, [("I1", product.productId, "1", "kg")], "One kilogram of oat yogurt.",
        [item("I1", "oat yogurt", "1", "kg")],
    )
    assert created.status_code == 201, created.text
    proposal = created.json()
    view = client.client.get(f"/v0.1/approver/proposals/{proposal['proposalId']}").json()
    if change == "base":
        updated = product.model_copy(update={"name": "Synthetic almond yogurt", "allergens": [_decl("SA", "CONTAINS")]})
    elif change == "allergen":
        updated = product.model_copy(update={"allergens": [*product.allergens, _decl("AE", "CONTAINS")]})
    else:
        updated = product.model_copy(update={"name": "Synthetic grated cabbage", "category": "17400"})
    state.gateway.products[product.productId] = updated
    approved = client.client.post(f"/v0.1/approver/proposals/{proposal['proposalId']}/decision", json={
        "offerVersion": proposal["offerVersion"], "approvalToken": view["approvalToken"], "action": "APPROVE",
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "SUPERSEDED"
    assert not state.gateway.approvals.is_approval_valid(proposal["proposalId"])
    replacement = client.get(f"/v0.1/proposals/{approved.json()['supersededBy']}").json()
    assert replacement["state"] != "CONFIRMED_SIMULATED"
    if change != "unrelated":
        assert replacement["state"] == "PENDING_APPROVAL"
        assert f"Contains {'almonds' if change == 'base' else 'eggs'}" in str(replacement["lines"])


@pytest.mark.parametrize("what", [" ", "gluten-free", "or similar"])
def test_unspecified_product_type_is_structured_no_order_not_a_server_error(api, what):
    client, _, _ = api
    product = add_product(api, name="Synthetic grated cabbage", category="17400", allergens=(_decl("X99"),))
    _, created = offer_and_propose(
        client, M2.customerId, [("I1", product.productId, "1", "kg")], "One kilogram by Monday.",
        [item("I1", what, "1", "kg")],
    )
    assert created.status_code == 422
    assert created.json()["error"]["code"] == "NO_ORDER"
