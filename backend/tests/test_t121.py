"""T121 (judge run 20261115-084227): real quantities and shortfalls, unit and claim wording, signed audit entries.
Synthetic hand-made records only (patch P7)."""

from decimal import Decimal

from test_api import api, scenario_b  # noqa: F401
from test_partial_fill import _decide

from app.agent.explain import _partial_fills, no_order_item_reason
from app.api.agent_routes import _ordered_products
from app.contracts import ExcludedProduct, ReasonCode, RequestItem, Requirement
from tests.test_partial_fill import GLUTEN_FREE

AM = Requirement(kind="AVOID_ALLERGEN", code="AM")


def _penne(kg: str) -> RequestItem:
    return RequestItem(itemId="I1", what="penne", quantity=Decimal(kg), unit="kg", requirements=[GLUTEN_FREE])


def test_a_partial_split_states_the_total_and_the_shortfall_as_data():
    # 30 + 20 bags of 5 kg = 250 kg of the 400 kg asked.
    proposal = _decide(_penne("400"), {"SYN-PENNE-A": 30, "SYN-PENNE-B": 20}, ["SYN-PENNE-A", "SYN-PENNE-B"])
    assert ReasonCode.PARTIAL_AVAILABILITY in proposal.reasons
    (short,) = proposal.shortfalls
    assert (short.itemId, short.quantity, short.unit) == ("I1", Decimal(150), "kg")
    assert short.reason.startswith("only 250 kg of the 400 kg can be delivered for Mon 16 Nov")
    fills = _partial_fills(proposal)
    assert fills["available"] == "250 kg from 2 compliant products"
    assert fills["rest"] == "150 kg is not available."
    assert _ordered_products(proposal.lines).startswith("250 kg in total · ")
    assert _ordered_products(proposal.lines).endswith(" and 1 more")


def test_a_full_split_has_no_shortfall():
    proposal = _decide(_penne("300"), {"SYN-PENNE-A": 30, "SYN-PENNE-B": 30}, ["SYN-PENNE-A", "SYN-PENNE-B"])
    assert proposal.shortfalls == []
    assert _ordered_products(proposal.lines).startswith("300 kg in total · ")


def _excluded(code: ReasonCode) -> ExcludedProduct:
    return ExcludedProduct(productId="SYN-X", name="Synthetic X", itemId="I1", reasons=[code])


def test_the_no_order_reason_names_the_other_unit_and_the_missing_declaration():
    why = no_order_item_reason(has_candidates=True, deadline_label="Mon 16 Nov", delivery_label=None,
                               delivered=False, excluded=[_excluded(ReasonCode.UNIT_MISMATCH)], requirements=[],
                               unit="kg")
    assert why.endswith("1 matching product is sold in litres: if you meant litres, edit the quantity.")
    why = no_order_item_reason(has_candidates=True, deadline_label="Mon 16 Nov", delivery_label=None,
                               delivered=False, excluded=[_excluded(ReasonCode.ALLERGEN_UNKNOWN)],
                               requirements=[GLUTEN_FREE, AM], unit="l")
    assert "milk-free not declared" in why


def test_every_audit_entry_is_signed_and_an_empty_scope_is_explained(api):  # noqa: F811
    client, _, state = api
    _, proposal = scenario_b(client)
    entries = state.gateway.get_audit_trail(proposal_id=proposal.json()["proposalId"]).entries
    assert {e.event for e in entries} >= {"OFFER_CREATED", "PROPOSAL_CREATED", "POLICY_DECIDED"}
    assert all(e.kid and e.signature for e in entries)
    assert client.get("/v0.1/audit/verify").json()["verified"] is True
    from fastapi.testclient import TestClient

    from app.main import app
    from app.policy import M1
    state.gateway.log._entries.clear()  # a cold session for this buyer
    with TestClient(app) as raw:
        body = raw.get("/v0.1/audit/verify", headers={"Authorization": f"Bearer demo-agent-{M1.customerId}"}).json()
    assert body["chainLength"] == 0 and "scope=all" in body["reason"]
