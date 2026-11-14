"""T97: the customer's own committed orders come off the stock pool of a new offer, so a second order the same day
never quotes stock the first order already holds. Synthetic records only (patch P7)."""

from decimal import Decimal

from app.contracts import GetOfferRequest, OfferItem, ReasonCode, RequestItem, StructuredRequest
from app.gateway.proposals import ProposalGateway, create_proposal
from app.policy import DEMO_MANDATES, M1, M2, MandateBook
from tests.test_proposals_gateway import GLUTEN_FREE, MONDAY, PRODUCTS, MutableSyntheticProvider, stock_row


def _offer(gateway: ProposalGateway, offer_id: str, kg: str, customer=M2):
    return gateway.get_offer(
        GetOfferRequest(customerId=customer.customerId, requestedDate=MONDAY,
                        items=[OfferItem(itemId="I1", productId="SYN-PASTA", quantity=Decimal(kg), unit="kg")]),
        offer_id=offer_id,
    ).offer


def _order(gateway: ProposalGateway, offer_id: str, kg: str, customer=M2):
    offer = _offer(gateway, offer_id, kg, customer)
    request = StructuredRequest(
        requestText=f"{kg} kg gluten-free pasta.", requestedDate=MONDAY,
        items=[RequestItem(itemId="I1", what="pasta", quantity=Decimal(kg), unit="kg", requirements=[GLUTEN_FREE])],
    )
    return create_proposal(gateway, customer.customerId, offer.offerId, offer.offerVersion, request)


def _stock_display(offer) -> str:
    return next(f.display for f in offer.lines[0].facts if f.fact == "stock.quantity")


def test_a_second_order_only_gets_what_the_first_order_left():
    gateway = ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES),
                              provider=MutableSyntheticProvider())  # 19 bags of 5 kg in stock
    first = _order(gateway, "OF-1", "60")
    assert first.lines[0].quantitySalesUnits == Decimal(12)
    assert gateway.committed_units(M2.customerId) == {("SYN-PASTA", MONDAY): Decimal(12)}

    second = _offer(gateway, "OF-2", "60")
    (line,) = second.lines
    assert line.quantitySalesUnits == Decimal(7)  # 19 − 12, never 12 against "19 in stock"
    assert "12 already in your orders, 7 left" in _stock_display(second)


def test_the_held_stock_makes_the_second_order_a_shortfall_not_an_auto():
    gateway = ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES),
                              provider=MutableSyntheticProvider())
    _order(gateway, "OF-1", "60")
    second = _order(gateway, "OF-2", "60")
    assert second.decision != "AUTO"
    assert ReasonCode.NO_CANDIDATE in second.reasons or ReasonCode.PARTIAL_AVAILABILITY in second.reasons


def test_another_customer_holds_nothing():
    gateway = ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES),
                              provider=MutableSyntheticProvider())
    gateway.provider.stocks["SYN-PASTA"] = stock_row("SYN-PASTA", 19, sales_unit="PSS")
    _order(gateway, "OF-1", "60")
    assert _offer(gateway, "OF-HOTEL", "60", customer=M1).lines[0].quantitySalesUnits == Decimal(12)


def test_the_same_day_total_is_in_the_api_reason_detail():
    """T97: an OVER_AUTO_LIMIT caused by the same-day total says so in the proposal itself, not only in the UI."""
    gateway = ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES),
                              provider=MutableSyntheticProvider())
    gateway.provider.stocks["SYN-PASTA"] = stock_row("SYN-PASTA", 100, sales_unit="PSS")
    first = _order(gateway, "OF-1", "65")
    assert first.decision == "AUTO" and first.reasonDetails == {}
    second = _order(gateway, "OF-2", "65")
    assert second.reasons == [ReasonCode.OVER_AUTO_LIMIT]
    assert second.reasonDetails[ReasonCode.OVER_AUTO_LIMIT] == (
        "€297.96 already committed for delivery on Mon 16 Nov; with this order €595.92 > €500.00 auto-approve limit"
    )
    assert "reasonDetails" in second.model_dump(mode="json")
