"""T97: when the available stock is a token fraction of the request (under 10 %), the agent says "cannot fulfil" with
options instead of queueing a partial proposal for a human. Synthetic records only (patch P7)."""

from datetime import date
from decimal import Decimal

from test_agent_run import synthetic_api  # noqa: F401  (pytest fixture)

from app.agent.run import AgentRunResult, cannot_fulfil_items
from app.contracts import (
    ContentPerSalesUnit,
    OfferLine,
    Quantity,
    RequestItem,
    StructuredRequest,
)
from app.policy import M2

MONDAY = date(2026, 11, 16)


def _line(item_id: str, kg: str) -> OfferLine:
    return OfferLine(
        lineId="L1", itemId=item_id, productId="SYN-1", name="Synthetic salmon fillet", salesUnit="KG",
        salesUnitLabel="kg", contentPerSalesUnit=ContentPerSalesUnit(value=Decimal(1), unit="kg", level="VERIFIED"),
        quantitySalesUnits=Decimal(kg), deliveredQuantity=Quantity(value=Decimal(kg), unit="kg"),
        unitPriceExclVat=Decimal("14.11"), lineTotalExclVat=Decimal("564.40"), vatRate=Decimal("14"),
        deliveryDate=MONDAY, closingTime=None, facts=[],
    )


def _request(kg: str) -> StructuredRequest:
    return StructuredRequest(requestText=f"{kg} kg salmon fillet", requestedDate=MONDAY,
                             items=[RequestItem(itemId="I1", what="salmon fillet", quantity=Decimal(kg), unit="kg")])


def test_a_token_fraction_cannot_be_fulfilled():
    (why,) = cannot_fulfil_items(_request("50000"), [_line("I1", "40")]).values()
    assert why.startswith("Cannot fulfil: only 40 kg of the 50,000 kg you asked for is available for Mon 16 Nov (0.08 %).")
    assert "Options:" in why


def test_a_real_partial_and_a_full_order_are_left_alone():
    assert cannot_fulfil_items(_request("100"), [_line("I1", "40")]) == {}  # 40 %: an approvable partial
    assert cannot_fulfil_items(_request("400"), [_line("I1", "40")]) == {}  # exactly 10 %
    assert cannot_fulfil_items(_request("40"), [_line("I1", "40")]) == {}
    assert cannot_fulfil_items(_request("40"), []) == {}  # nothing at all is the existing no-candidate path


def test_the_agent_run_stops_with_cannot_fulfil_and_creates_no_proposal(synthetic_api):  # noqa: F811
    client, state = synthetic_api
    resp = client.post("/v0.1/agent/run", json={
        "customerId": M2.customerId,
        "requestText": "We need 5000 kg of gluten-free macaroni by Monday.",  # 19 bags × 5 kg = 95 kg in stock
    })
    assert resp.status_code == 200, resp.text
    result = AgentRunResult.model_validate(resp.json())
    assert result.proposal is None
    approval = result.steps[-1]
    assert approval.summary == "Cannot fulfil this request"
    (item,) = approval.noOrder.items
    assert item.why.startswith("Cannot fulfil: only 95 kg of the 5,000 kg you asked for")
    assert state.gateway.committed_total(M2.customerId, MONDAY) == 0
