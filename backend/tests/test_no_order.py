from datetime import date
from decimal import Decimal

from test_agent_run import HEL, MONDAY, NOW, SyntheticAgentProvider
from test_agent_run import synthetic_api as synthetic_api

from app.contracts import ContentPerSalesUnit, ReasonCode
from app.policy import M1, M2


def run(client, text, *, customer=M1, lookup_date=None):
    body = {"customerId": customer.customerId, "requestText": text, "lookupDate": lookup_date}
    response = client.post("/v0.1/agent/run", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_late_delivery_requires_a_retry_then_human_approval(synthetic_api):
    client, state = synthetic_api
    text = "I need 10 litres of lactose-free milk today."
    first = run(client, text)
    assert first["offer"] is first["proposal"] is None
    assert [step["name"] for step in first["steps"]] == ["REQUEST", "LOOKUP", "APPROVAL"]
    details = first["steps"][-1]["noOrder"]
    assert "lactose-free" in details["items"][0]["searched"]
    assert "after your Fri 13 Nov deadline" in details["items"][0]["why"]
    assert details["earliestDelivery"] == MONDAY.isoformat()
    assert state.gateway.committed_total(M1.customerId, MONDAY) == 0

    second = run(client, text, lookup_date=details["earliestDelivery"])
    proposal = second["proposal"]
    assert proposal["request"]["requestText"] == text
    assert proposal["requestedDate"] == "2026-11-13"
    assert proposal["lines"][0]["deliveryDate"] == MONDAY.isoformat()
    assert proposal["decision"] == "ASK"
    assert proposal["state"] == "PENDING_APPROVAL"
    assert ReasonCode.DATE_LATER in proposal["reasons"]


def test_passed_cutoff_only_suggests_a_fresh_deliverable_date(synthetic_api):
    client, state = synthetic_api
    tuesday = date(2026, 11, 17)

    class DateAwareProvider(SyntheticAgentProvider):
        def _stock_row(self, code, requested_date):
            row = super()._stock_row(code, requested_date)
            row["closingTime"] = f"2026-11-12T14:00:00{HEL}"
            for line in row["scheduleLines"]:
                if requested_date == tuesday:
                    row["closingTime"] = f"2026-11-16T17:00:00{HEL}"
                    line["availableDate"] = tuesday.isoformat()
            return row

    state.gateway.provider = DateAwareProvider()
    text = "I need 10 litres of lactose-free milk today."
    first = run(client, text)
    assert first["proposal"] is None
    assert first["steps"][-1]["noOrder"]["earliestDelivery"] == tuesday.isoformat()
    retry = run(client, text, lookup_date=tuesday.isoformat())
    assert retry["proposal"]["state"] == "PENDING_APPROVAL"
    assert retry["proposal"]["reasons"] == [ReasonCode.DATE_LATER]
    proposal = retry["proposal"]
    approval = state.gateway.get_approver_view(proposal["proposalId"])
    confirmed = state.gateway.decide_approval(
        proposal["proposalId"], proposal["offerVersion"], approval.approvalToken, "APPROVE", now=NOW,
    )
    assert confirmed.state == "CONFIRMED_SIMULATED"
    assert confirmed.lines[0].deliveryDate == tuesday


def test_no_order_suggestions_keep_hard_requirements(synthetic_api):
    client, state = synthetic_api
    result = run(client, "I need 5 kg of imaginary gluten-free flounder today.")
    details = result["steps"][-1]["noOrder"]
    assert "gluten-free" in details["items"][0]["searched"]
    assert details["earliestDelivery"] is None
    assert details["items"][0]["closestMatches"] == []

    # Retain a literal match after dropping one unknown name token. A second record
    # has the same name and sizing but lacks the claim and must stay out.
    source = state.catalog[0]
    safe = source.model_copy(update={
        "name": "Synthetic flounder",
        "names": {"en": "Synthetic flounder"},
        "claims": ["FREE_FROM_GLUTEN"],
        "contentPerSalesUnit": ContentPerSalesUnit(value=Decimal(1), unit="kg", level="DECLARED"),
    })
    unsafe = safe.model_copy(update={"productId": "SYN-UNPROVEN", "claims": []})
    state.catalog = [safe, unsafe]
    result = run(client, "I need 5 kg of imaginary gluten-free flounder today.")
    assert result["proposal"] is None
    assert result["steps"][-1]["noOrder"]["items"][0]["closestMatches"] == [
        {"productId": safe.productId, "name": safe.name},
    ]


def test_unmatched_line_does_not_create_a_partial_order(synthetic_api):
    client, _ = synthetic_api
    result = run(client, "I need 10 litres of lactose-free milk and 20 kg of imaginary flounder today.")
    assert result["offer"] is result["proposal"] is None
    details = result["steps"][-1]["noOrder"]
    assert details["earliestDelivery"] is None
    assert "after your Fri 13 Nov deadline" in details["items"][0]["why"]
    assert "No eligible products matched" in details["items"][1]["why"]


def test_no_order_explains_hard_requirement_failures(synthetic_api):
    client, _ = synthetic_api
    result = run(client, "I need 10 litres of gluten-free milk today.", customer=M2)
    details = result["steps"][-1]["noOrder"]
    assert "gluten-free" in details["items"][0]["searched"]
    assert "claim missing" in details["items"][0]["why"].lower()
    assert "availability was found by the deadline" not in details["items"][0]["why"].lower()
    assert details["earliestDelivery"] is None


def test_friday_request_delivered_monday_auto_confirms_with_early_note_and_no_date_later(synthetic_api):
    client, _ = synthetic_api
    result = run(client, "Tarvitaan 50 litraa gluteenitonta kauramaitoa 20.11. mennessä", customer=M1)
    proposal = result["proposal"]
    assert proposal is not None
    assert proposal["decision"] == "AUTO"
    assert proposal["state"] == "CONFIRMED_SIMULATED"
    assert ReasonCode.DATE_LATER not in proposal["reasons"]
    assert ReasonCode.DATE_LATER not in result["reasons"]
    early_note = "Arrives Mon 16 Nov, 4 days before your Fri 20 Nov"
    line_facts = {f["fact"]: f for f in proposal["lines"][0]["facts"]}
    assert line_facts["delivery.early"]["display"] == early_note
    assert early_note in result["explanation"]["text"]


def test_failed_gluten_free_milk_free_pasta_line_never_says_availability_found_by_deadline(synthetic_api):
    client, _ = synthetic_api
    # 474235 has 19 bags × 5 kg = 95 kg in synthetic_api; asking for 1000 kg cannot be fulfilled.
    result = run(client, "We need 1000 kg of gluten-free and milk-free pasta by Monday.", customer=M2)
    assert result["proposal"] is None
    details = result["steps"][-1]["noOrder"]
    why = details["items"][0]["why"]
    assert "Availability was found by the deadline" not in why
    assert "gluten-free" in details["items"][0]["searched"] and "milk-free" in details["items"][0]["searched"]
    assert "Exclusions include" in why or "Cannot fulfil" in why or "could not be offered" in why
