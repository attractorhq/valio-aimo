"""T97/T102: the customer's own committed orders come off the stock pool of a new offer, explain the held
quantity with the earlier proposal ID, and keep REST `/v0.1/offers` and `/v0.1/agent/run` in exact agreement
within the same session. Synthetic records only (patch P7)."""

from decimal import Decimal

from synthetic.demo_client import DemoTestClient as TestClient
from test_agent_run import NOW, SyntheticAgentProvider, _build_synthetic_catalog

from app.api import state as state_module
from app.api.state import GatewayState, SandboxRegistry, fresh_sandbox
from app.contracts import GetOfferRequest, OfferItem, ReasonCode, RequestItem, StructuredRequest
from app.gateway.proposals import ProposalGateway, create_proposal
from app.main import app
from app.policy import DEMO_MANDATES, M1, M2, MandateBook
from tests.test_proposals_gateway import GLUTEN_FREE, MONDAY, PRODUCTS, MutableSyntheticProvider, stock_row


def _offer(gateway: ProposalGateway, offer_id: str, kg: str, customer=M2):
    return gateway.get_offer(
        GetOfferRequest(
            customerId=customer.customerId,
            requestedDate=MONDAY,
            items=[OfferItem(itemId="I1", productId="SYN-PASTA", quantity=Decimal(kg), unit="kg")],
        ),
        offer_id=offer_id,
    ).offer


def _order(gateway: ProposalGateway, offer_id: str, kg: str, customer=M2):
    offer = _offer(gateway, offer_id, kg, customer)
    request = StructuredRequest(
        requestText=f"{kg} kg gluten-free pasta.",
        requestedDate=MONDAY,
        items=[RequestItem(itemId="I1", what="pasta", quantity=Decimal(kg), unit="kg", requirements=[GLUTEN_FREE])],
    )
    return create_proposal(gateway, customer.customerId, offer.offerId, offer.offerVersion, request)


def _fact(line, name: str):
    return next((f for f in line.facts if f.fact == name), None)


def test_a_second_order_only_gets_what_the_first_order_left_and_cites_that_proposal():
    gateway = ProposalGateway(
        products=PRODUCTS,
        mandates=MandateBook(DEMO_MANDATES),
        provider=MutableSyntheticProvider(),  # 19 bags of 5 kg (= 95 kg) in stock
    )
    first_offer = _offer(gateway, "OF-1", "60")
    # Fresh session before any confirmed order: raw stock 19 bags, no held-stock fact.
    assert _fact(first_offer.lines[0], "stock.quantity").value == "19"
    assert _fact(first_offer.lines[0], "stock.heldByYourOrders") is None

    first = create_proposal(
        gateway,
        M2.customerId,
        first_offer.offerId,
        first_offer.offerVersion,
        StructuredRequest(
            requestText="60 kg gluten-free pasta.",
            requestedDate=MONDAY,
            items=[RequestItem(itemId="I1", what="pasta", quantity=Decimal("60"), unit="kg", requirements=[GLUTEN_FREE])],
        ),
    )
    assert first.state == "CONFIRMED_SIMULATED"
    assert first.lines[0].quantitySalesUnits == Decimal(12)
    assert gateway.committed_units(M2.customerId) == {("SYN-PASTA", MONDAY): Decimal(12)}

    second = _offer(gateway, "OF-2", "60")
    (line,) = second.lines
    expected_note = (
        f"35 kg left for you: 95 kg in stock, 60 kg already in your earlier order today ({first.proposalId})"
    )
    assert line.quantitySalesUnits == Decimal(7)  # 19 − 12 = 7 bags = 35 kg
    assert line.deliveredQuantity.value == Decimal(35)
    stock_fact = _fact(line, "stock.quantity")
    held_fact = _fact(line, "stock.heldByYourOrders")
    assert stock_fact is not None and stock_fact.value == "7"
    assert stock_fact.display == expected_note
    assert held_fact is not None and held_fact.display == expected_note
    assert held_fact.value["proposalIds"] == [first.proposalId]
    assert held_fact.value["availableQuantity"] == "35 kg"
    assert held_fact.value["rawStockQuantity"] == "95 kg"
    assert held_fact.value["heldQuantity"] == "60 kg"


def test_the_held_stock_makes_the_second_order_a_shortfall_with_explained_reason():
    gateway = ProposalGateway(
        products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES), provider=MutableSyntheticProvider()
    )
    first = _order(gateway, "OF-1", "60")
    second = _order(gateway, "OF-2", "60")
    assert second.decision == "ASK"
    assert ReasonCode.PARTIAL_AVAILABILITY in second.reasons
    expected_note = (
        f"35 kg left for you: 95 kg in stock, 60 kg already in your earlier order today ({first.proposalId})"
    )
    assert expected_note in second.reasonDetails[ReasonCode.PARTIAL_AVAILABILITY]


def test_another_customer_holds_nothing():
    gateway = ProposalGateway(
        products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES), provider=MutableSyntheticProvider()
    )
    gateway.provider.stocks["SYN-PASTA"] = stock_row("SYN-PASTA", 19, sales_unit="PSS")
    _order(gateway, "OF-1", "60")
    hotel_offer = _offer(gateway, "OF-HOTEL", "60", customer=M1)
    assert hotel_offer.lines[0].quantitySalesUnits == Decimal(12)
    assert _fact(hotel_offer.lines[0], "stock.quantity").value == "19"
    assert _fact(hotel_offer.lines[0], "stock.heldByYourOrders") is None


def test_the_same_day_total_is_in_the_api_reason_detail():
    """T97: an OVER_AUTO_LIMIT caused by the same-day total says so in the proposal itself, not only in the UI."""
    gateway = ProposalGateway(
        products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES), provider=MutableSyntheticProvider()
    )
    gateway.provider.stocks["SYN-PASTA"] = stock_row("SYN-PASTA", 100, sales_unit="PSS")
    first = _order(gateway, "OF-1", "65")
    assert first.decision == "AUTO" and first.reasonDetails == {}
    second = _order(gateway, "OF-2", "65")
    assert second.reasons == [ReasonCode.OVER_AUTO_LIMIT]
    assert second.reasonDetails[ReasonCode.OVER_AUTO_LIMIT] == (
        "€297.96 already committed for delivery on Mon 16 Nov; with this order €595.92 > €500.00 auto-approve limit"
    )
    assert "reasonDetails" in second.model_dump(mode="json")


def test_rest_offer_and_agent_run_agree_for_same_session_and_fresh_session_shows_raw_stock(monkeypatch):
    """T102: two orders in one session -> second line + reason state held quantity and first proposal ID;
    REST `/v0.1/offers` in the same session reports the exact same available quantity, while a fresh session
    shows raw stock."""
    # Narrow synthetic catalogue to the single gluten-free macaroni 474235 (19 bags × 5 kg = 95 kg in stock)
    # so there is no second pasta product to top up the shortfall.
    all_products = _build_synthetic_catalog()
    products = {pid: p for pid, p in all_products.items() if pid == "474235"}
    resources = GatewayState(
        ProposalGateway(products=products, provider=SyntheticAgentProvider()),
        list(products.values()),
        "RECORDED",
        clock=lambda: NOW,
    )
    registry = SandboxRegistry(lambda sandbox_id: fresh_sandbox(resources, sandbox_id))
    monkeypatch.setattr(state_module, "_state", resources)
    monkeypatch.setattr(state_module, "_registry", registry)
    app.dependency_overrides.clear()

    try:
        with (
            TestClient(app, headers={"X-Demo-Sandbox": "buyer-session"}) as same_session,
            TestClient(app, headers={"X-Demo-Sandbox": "fresh-session"}) as fresh_session,
        ):
            # 1. First order of 60 kg (12 bags, €275.04 <= €500) confirms automatically.
            run1 = same_session.post(
                "/v0.1/agent/run",
                json={
                    "customerId": M2.customerId,
                    "requestText": "We need 60 kg of gluten-free macaroni by Monday.",
                },
            )
            assert run1.status_code == 200, run1.text
            prop1 = run1.json()["proposal"]
            assert prop1["state"] == "CONFIRMED_SIMULATED"
            assert prop1["lines"][0]["deliveredQuantity"] == {"value": "60", "unit": "kg"}
            first_pid = prop1["proposalId"]

            expected_note = (
                f"35 kg left for you: 95 kg in stock, 60 kg already in your earlier order today ({first_pid})"
            )

            # 2. REST POST /v0.1/offers in the SAME session reports the exact deducted stock (7 bags = 35 kg).
            rest_offer = same_session.post(
                "/v0.1/offers",
                json={
                    "customerId": M2.customerId,
                    "requestedDate": MONDAY.isoformat(),
                    "items": [{"itemId": "I1", "productId": "474235", "quantity": "60", "unit": "kg"}],
                },
            )
            assert rest_offer.status_code == 200, rest_offer.text
            rest_line = rest_offer.json()["lines"][0]
            assert rest_line["quantitySalesUnits"] == "7"
            assert rest_line["deliveredQuantity"] == {"value": "35", "unit": "kg"}
            rest_stock = next(f for f in rest_line["facts"] if f["fact"] == "stock.quantity")
            rest_held = next(f for f in rest_line["facts"] if f["fact"] == "stock.heldByYourOrders")
            assert rest_stock["value"] == "7"
            assert rest_stock["display"] == expected_note
            assert rest_held["display"] == expected_note
            assert rest_held["value"]["proposalIds"] == [first_pid]

            # 3. Second agent run in the SAME session agrees with REST and explains held stock + first_pid.
            run2 = same_session.post(
                "/v0.1/agent/run",
                json={
                    "customerId": M2.customerId,
                    "requestText": "We need 60 kg of gluten-free macaroni by Monday.",
                },
            )
            assert run2.status_code == 200, run2.text
            body2 = run2.json()
            prop2 = body2["proposal"]
            assert prop2["decision"] == "ASK"
            assert "PARTIAL_AVAILABILITY" in prop2["reasons"]
            line2 = prop2["lines"][0]
            assert line2["quantitySalesUnits"] == rest_line["quantitySalesUnits"] == "7"
            assert line2["deliveredQuantity"] == rest_line["deliveredQuantity"] == {"value": "35", "unit": "kg"}
            line2_stock = next(f for f in line2["facts"] if f["fact"] == "stock.quantity")
            assert line2_stock["value"] == "7"
            assert line2_stock["display"] == expected_note
            assert expected_note in prop2["reasonDetails"]["PARTIAL_AVAILABILITY"]
            assert expected_note in body2["explanation"]["text"]
            partial_chip = next(c for c in body2["explanation"]["reasonChips"] if c["code"] == "PARTIAL_AVAILABILITY")
            assert expected_note in partial_chip["message"]

            # 4. A fresh session with no earlier orders shows the raw stock (19 bags = 95 kg) and offers all 60 kg.
            fresh_offer = fresh_session.post(
                "/v0.1/offers",
                json={
                    "customerId": M2.customerId,
                    "requestedDate": MONDAY.isoformat(),
                    "items": [{"itemId": "I1", "productId": "474235", "quantity": "60", "unit": "kg"}],
                },
            )
            assert fresh_offer.status_code == 200, fresh_offer.text
            fresh_line = fresh_offer.json()["lines"][0]
            assert fresh_line["quantitySalesUnits"] == "12"
            assert fresh_line["deliveredQuantity"] == {"value": "60", "unit": "kg"}
            fresh_stock = next(f for f in fresh_line["facts"] if f["fact"] == "stock.quantity")
            assert fresh_stock["value"] == "19"
            assert not any(f["fact"] == "stock.heldByYourOrders" for f in fresh_line["facts"])
    finally:
        app.dependency_overrides.clear()
