"""T106: a multi-line request where one line has no compliant candidate offers the deliverable lines on their own,
as a proposal a person signs off (PARTIAL_AVAILABILITY, never AUTO). The dropped line is never ordered."""

from datetime import datetime
from decimal import Decimal

import pytest
from test_agent_run import NOW
from test_agent_run import synthetic_api as synthetic_api

from app.contracts import ReasonCode, RequestItem, StructuredRequest
from app.policy import M1
from app.policy.decision import decide

TEXT = "We need 50 litres of gluten-free oat drink and 30 kg of gluten-free imaginary flounder by 20.11."


def run(client, text, *, customer=M1, view=None):
    url = "/v0.1/agent/run" + (f"?view={view}" if view else "")
    response = client.post(url, json={"customerId": customer.customerId, "requestText": text})
    assert response.status_code == 200, response.text
    return response.json()


def test_deliverable_line_becomes_its_own_proposal_for_a_person(synthetic_api):
    client, state = synthetic_api
    result = run(client, TEXT)
    proposal = result["proposal"]
    assert proposal is not None
    assert proposal["decision"] == "ASK"
    assert proposal["state"] == "PENDING_APPROVAL"
    assert ReasonCode.PARTIAL_AVAILABILITY in proposal["reasons"]
    # Only the oat drink line is ordered; the flounder line is not in the request or the lines.
    assert [item["what"] for item in proposal["request"]["items"]] == ["oat drink"]
    assert {line["itemId"] for line in proposal["lines"]} == {"I1"}
    assert proposal["notOrdered"] == [{
        "itemId": "I2", "what": "imaginary flounder", "quantity": "30", "unit": "kg",
        "reason": "no gluten-free imaginary flounder was found",
    }]
    sentence = (
        "Approving means oat drink only; imaginary flounder is not ordered: "
        "no gluten-free imaginary flounder was found."
    )
    assert proposal["reasonDetails"][ReasonCode.PARTIAL_AVAILABILITY].startswith(sentence)
    assert "—" not in sentence

    # The approver sees the same plain sentence.
    view = state.gateway.get_approver_view(proposal["proposalId"])
    assert view.approvalStatement is not None and view.approvalStatement.startswith(sentence)

    # The buyer's result keeps the dropped line with its reason, as in a no-order result.
    last = result["steps"][-1]
    assert last["name"] == "APPROVAL" and last["status"] == "waiting"
    assert [entry["itemId"] for entry in last["noOrder"]["items"]] == ["I2"]

    # Approving orders the oat drink only.
    confirmed = state.gateway.decide_approval(
        proposal["proposalId"], proposal["offerVersion"], view.approvalToken, "APPROVE", now=NOW,
    )
    assert confirmed.state == "CONFIRMED_SIMULATED"
    assert {line.itemId for line in confirmed.lines} == {"I1"}
    assert [item.itemId for item in confirmed.notOrdered] == ["I2"]


def test_compact_view_exposes_proposal_and_not_ordered_lines(synthetic_api):
    client, _ = synthetic_api
    compact = run(client, TEXT, view="compact")
    assert compact["proposalId"] and compact["state"] == "PENDING_APPROVAL"
    assert [entry["itemId"] for entry in compact["notOrdered"]] == ["I2"]
    assert compact["notOrdered"][0]["reason"] == "no gluten-free imaginary flounder was found"


def test_gateway_never_auto_approves_a_version_with_dropped_lines(synthetic_api):
    client, _ = synthetic_api
    # 5 l of oat drink alone is well under M1's auto-approve limit; with a dropped line a person still signs off.
    alone = run(client, "We need 5 litres of oat drink by 20.11.")["proposal"]
    assert alone["decision"] == "AUTO"
    proposal = run(client, "We need 5 litres of oat drink and 2 kg of imaginary flounder by 20.11.")["proposal"]
    assert proposal["decision"] == "ASK"
    assert proposal["state"] == "PENDING_APPROVAL"
    assert proposal["reasons"] == [ReasonCode.PARTIAL_AVAILABILITY]
    assert proposal["notOrdered"][0]["reason"] == "no matching imaginary flounder was found"


def test_no_deliverable_line_keeps_the_no_order_result(synthetic_api):
    client, _ = synthetic_api
    result = run(client, "I need 10 litres of lactose-free milk and 20 kg of imaginary flounder today.", customer=M1)
    assert result["offer"] is result["proposal"] is None
    details = result["steps"][-1]["noOrder"]
    assert [entry["itemId"] for entry in details["items"]] == ["I1", "I2"]
    assert details["earliestDelivery"] is None


def test_policy_counts_a_dropped_line_as_partial_availability():
    request = StructuredRequest(
        requestText="synthetic", requestedDate="2026-11-16",
        items=[RequestItem(itemId="I1", what="oat drink", quantity=Decimal(5), unit="l")],
    )
    when = datetime.fromisoformat("2026-11-15T10:00:00+02:00")
    decision = decide(mandate=M1, request=request, lines=[], now=when, not_ordered_items=["I2"])
    assert any(f.code == ReasonCode.PARTIAL_AVAILABILITY and f.effect == "ASK" for f in decision.findings)
    with pytest.raises(ValueError):
        decide(mandate=M1, request=request, lines=[], now=when, not_ordered_items=["I1"])


def _cutoff_passed_except(provider_cls, keep: set[str]):
    class CutoffProvider(provider_cls):
        def _stock_row(self, code, requested_date):
            row = super()._stock_row(code, requested_date)
            if code not in keep:
                row["closingTime"] = "2026-11-12T14:00:00+02:00"  # before NOW for every other product
            return row

    return CutoffProvider()


def test_line_failing_at_the_offer_stage_is_dropped_and_the_rest_reoffered(synthetic_api):
    from test_agent_run import SyntheticAgentProvider

    client, state = synthetic_api
    oat = run(client, "We need 50 litres of gluten-free oat drink by 20.11.")["proposal"]
    oat_ids = {line["productId"] for line in oat["lines"]}
    state.gateway.provider = _cutoff_passed_except(SyntheticAgentProvider, oat_ids)

    text = "We need 50 litres of gluten-free oat drink and 10 litres of lactose-free milk by 20.11."
    result = run(client, text)
    proposal = result["proposal"]
    assert proposal is not None
    assert proposal["state"] == "PENDING_APPROVAL"
    assert proposal["reasons"][0] == ReasonCode.PARTIAL_AVAILABILITY
    assert {line["productId"] for line in proposal["lines"]} <= oat_ids
    assert [item["what"] for item in proposal["request"]["items"]] == ["oat drink"]
    # A fresh offer for the remaining line only, decided from scratch.
    assert proposal["offerId"] != oat["offerId"]
    assert all(line["itemId"] == "I1" for line in result["offer"]["lines"])
    assert [entry["itemId"] for entry in proposal["notOrdered"]] == ["I2"]
    assert proposal["notOrdered"][0]["reason"] == "order cut-off passed for every candidate for Fri 20 Nov"
    assert proposal["reasonDetails"][ReasonCode.PARTIAL_AVAILABILITY].startswith(
        "Approving means oat drink only; milk is not ordered: order cut-off passed for every candidate for Fri 20 Nov."
    )
    assert [entry["itemId"] for entry in result["steps"][-1]["noOrder"]["items"]] == ["I2"]


def test_offer_stage_failure_on_every_line_keeps_the_no_order_result(synthetic_api):
    from test_agent_run import SyntheticAgentProvider

    client, state = synthetic_api
    state.gateway.provider = _cutoff_passed_except(SyntheticAgentProvider, set())
    result = run(client, "We need 50 litres of gluten-free oat drink and 10 litres of lactose-free milk by 20.11.")
    assert result["proposal"] is None
    assert [entry["itemId"] for entry in result["steps"][-1]["noOrder"]["items"]] == ["I1", "I2"]
