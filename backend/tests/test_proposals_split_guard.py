"""T62: the split-order guard. The auto-approve and hard limits apply to what a customer already has committed
for the same delivery date plus the new order, so a need split into small orders can never pass unnoticed."""

from datetime import timedelta
from decimal import Decimal

from app.agent.explain import template_explanation
from app.api.state import GatewayState, fresh_sandbox, reset_sandbox
from app.contracts import GetOfferRequest, OfferItem, ReasonCode, RequestItem, StructuredRequest
from app.gateway.proposals import ProposalGateway, create_proposal, decide_approval, get_audit_trail
from app.policy import DEMO_MANDATES, M1, M2, MandateBook, decide
from app.policy.limits import delivery_dates, format_days
from tests.synthetic.policy import NOW, line, product, request
from tests.synthetic.policy import item as policy_item
from tests.test_proposals_gateway import (
    GLUTEN_FREE,
    LACTOSE_FREE,
    MONDAY,
    PRODUCTS,
    MutableSyntheticProvider,
    price_row,
    stock_row,
)

TUESDAY = MONDAY + timedelta(days=1)


# --------------------------------------------------------------------------- policy: the committed input


def _decide(total: str, committed: str):
    declared = product(allergens={"AE": "FREE_FROM"})  # M2 is STRICT: a declaration is needed
    return decide(
        mandate=M2,
        request=request(policy_item(quantity="10", unit="kg")),
        lines=[line(prod=declared, qty="10", unit="kg", total=total)],
        now=NOW,
        committed_excl_vat=Decimal(committed),
    )


def test_nothing_committed_keeps_the_single_order_rule():
    assert _decide("300.00", "0").outcome == "AUTO"
    over = _decide("520.00", "0")
    assert over.outcome == "ASK" and over.reasons == (ReasonCode.OVER_AUTO_LIMIT,)
    assert over.findings[-1].detail is None


def test_committed_same_day_orders_count_against_the_auto_approve_limit():
    decision = _decide("300.00", "300.00")
    assert decision.outcome == "ASK"
    assert decision.reasons == (ReasonCode.OVER_AUTO_LIMIT,)
    (finding,) = [f for f in decision.findings if f.code == ReasonCode.OVER_AUTO_LIMIT]
    assert finding.detail == (
        "€300.00 already committed for delivery on Mon 16 Nov; with this order €600.00 > €500.00 auto-approve limit"
    )
    assert decision.committed_excl_vat == Decimal("300.00")


def test_committed_same_day_orders_count_against_the_hard_limit():
    decision = _decide("300.00", "2900.00")
    assert decision.outcome == "BLOCK"
    assert ReasonCode.OVER_HARD_LIMIT in decision.reasons
    (finding,) = [f for f in decision.findings if f.code == ReasonCode.OVER_HARD_LIMIT]
    assert finding.detail == (
        "€2,900.00 already committed for delivery on Mon 16 Nov; with this order €3,200.00 > €3,000.00 hard limit"
    )


def test_staying_within_the_limit_with_committed_orders_is_still_auto():
    assert _decide("100.00", "300.00").outcome == "AUTO"


# --------------------------------------------------------------------------- gateway: real proposals


def _gateway():
    # Enough stock for every order: since T97 the customer's own committed bags come off the stock pool.
    provider = MutableSyntheticProvider()
    provider.stocks["SYN-PASTA"] = stock_row("SYN-PASTA", 100, sales_unit="PSS")
    return ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES), provider=provider)


def _order(gateway: ProposalGateway, offer_id: str, *, kg: str = "65", day=MONDAY):
    """Gluten-free pasta for M2 at €22.92 per 5 kg bag: 65 kg = 13 bags = €297.96 (alone under €500)."""
    offer_req = GetOfferRequest(
        customerId=M2.customerId, requestedDate=day,
        items=[OfferItem(itemId="I1", productId="SYN-PASTA", quantity=Decimal(kg), unit="kg")],
    )
    struct_req = StructuredRequest(
        requestText=f"{kg} kg gluten-free pasta.", requestedDate=day,
        items=[RequestItem(itemId="I1", what="pasta", quantity=Decimal(kg), unit="kg", requirements=[GLUTEN_FREE])],
    )
    offer = gateway.get_offer(offer_req, offer_id=offer_id).offer
    return create_proposal(gateway, M2.customerId, offer.offerId, offer.offerVersion, struct_req)


def test_four_small_orders_on_one_day_cannot_all_be_auto():
    gateway = _gateway()
    first = _order(gateway, "OF-SPLIT-1")
    assert first.totals.exclVat == Decimal("297.96")
    assert (first.decision, first.state) == ("AUTO", "CONFIRMED_SIMULATED")

    second = _order(gateway, "OF-SPLIT-2")
    assert (second.decision, second.state) == ("ASK", "PENDING_APPROVAL")
    assert second.reasons == [ReasonCode.OVER_AUTO_LIMIT]

    assert _order(gateway, "OF-SPLIT-3").decision == "ASK"  # the pending second order counts too
    assert _order(gateway, "OF-SPLIT-4").decision == "ASK"
    assert gateway.committed_total(M2.customerId, MONDAY) == Decimal("297.96") * 4

    trail = get_audit_trail(gateway, proposal_id=second.proposalId)
    (decided,) = [e for e in trail.entries if e.event == "POLICY_DECIDED"]
    assert "€297.96 already committed for delivery on Mon 16 Nov; with this order €595.92" in decided.summary
    assert "€500.00 auto-approve limit" in decided.summary

    # The explanation never claims the €297.96 order alone is over the limit.
    text = template_explanation(proposal=second, mandate=M2)
    assert "plus the orders already committed for delivery on Mon 16 Nov is above your €500.00 auto-approve limit" in text


def test_another_delivery_date_does_not_count():
    gateway = _gateway()
    _order(gateway, "OF-DAY-1")  # delivered Mon 16 Nov
    # The next order is available for Tuesday only: a different delivery day has its own limit.
    gateway.provider.stocks["SYN-PASTA"] = stock_row("SYN-PASTA", 100, sales_unit="PSS", day=TUESDAY)
    tuesday = _order(gateway, "OF-DAY-2", day=TUESDAY)
    assert {line.deliveryDate for line in tuesday.lines} == {TUESDAY}
    assert tuesday.decision == "AUTO"
    assert gateway.committed_total(M2.customerId, TUESDAY) == Decimal("297.96")
    assert gateway.committed_total(M2.customerId, MONDAY) == Decimal("297.96")


def test_commitments_are_scoped_to_a_sandbox_and_cleared_by_reset():
    resources = GatewayState(_gateway(), list(PRODUCTS.values()), "RECORDED")
    first = fresh_sandbox(resources, "first")
    second = fresh_sandbox(resources, "second")

    assert _order(first.gateway, "OF-FIRST").decision == "AUTO"
    pending = _order(first.gateway, "OF-REPEAT")
    assert pending.reasons == [ReasonCode.OVER_AUTO_LIMIT]
    assert _order(second.gateway, "OF-SECOND").decision == "AUTO"

    reset_sandbox(first)
    assert _order(first.gateway, "OF-AFTER-RESET").decision == "AUTO"
    assert _order(second.gateway, "OF-STILL-COMMITTED").reasons == [ReasonCode.OVER_AUTO_LIMIT]


def test_a_pending_proposal_counts_until_it_is_rejected():
    gateway = _gateway()
    first = _order(gateway, "OF-PEND-1", kg="90")  # 18 bags = €412.56: AUTO, confirmed
    assert first.decision == "AUTO"
    second = _order(gateway, "OF-PEND-2", kg="25")  # 5 bags = €114.60: €527.16 for the day
    assert (second.decision, second.state) == ("ASK", "PENDING_APPROVAL")

    view = gateway.get_approver_view(second.proposalId)
    decide_approval(gateway, second.proposalId, second.offerVersion, view.approvalToken, "REJECT")
    assert gateway.committed_total(M2.customerId, MONDAY) == Decimal("412.56")
    third = _order(gateway, "OF-PEND-3", kg="10")  # 2 bags = €45.84: €458.40 for the day
    assert third.decision == "AUTO"


def test_a_new_version_does_not_count_the_version_it_replaces():
    gateway = _gateway()
    assert _order(gateway, "OF-SUP-1", kg="90").decision == "AUTO"  # €412.56 confirmed
    second = _order(gateway, "OF-SUP-2", kg="25")  # €114.60 pending
    assert gateway.committed_total(M2.customerId, MONDAY, exclude=[second.proposalId]) == Decimal("412.56")

    # A price change at the re-check supersedes v1: v2 is decided from scratch.
    view = gateway.get_approver_view(second.proposalId)
    gateway.provider.prices["SYN-PASTA"] = price_row("SYN-PASTA", 22.93)
    superseded = decide_approval(gateway, second.proposalId, second.offerVersion, view.approvalToken, "APPROVE")
    assert superseded.state == "SUPERSEDED"
    v2 = gateway.get_proposal(superseded.supersededBy)
    assert v2.totals.exclVat == Decimal("114.65")
    assert v2.reasons == [ReasonCode.OVER_AUTO_LIMIT]  # €412.56 + €114.65, without the replaced €114.60
    trail = get_audit_trail(gateway, proposal_id=v2.proposalId)
    decided = [e for e in trail.entries if e.event == "POLICY_DECIDED"][-1]
    assert "€412.56 already committed for delivery on Mon 16 Nov; with this order €527.21" in decided.summary
    # The superseded version no longer counts; the new one does.
    assert gateway.committed_total(M2.customerId, MONDAY) == Decimal("412.56") + v2.totals.exclVat


# --------------------------------------------------------------------------- keyed on the delivery date (review)


def test_orders_requested_for_different_days_but_delivered_on_one_day_count_together():
    """The live API delivers every order on Mon 16 Nov whatever date was requested: the guard follows the
    lines' delivery dates, so requesting Mon, Tue and Wed cannot split one delivery day past the limit."""
    gateway = _gateway()
    first = _order(gateway, "OF-DD-1", day=MONDAY)
    assert (first.decision, first.reasons) == ("AUTO", [])
    second = _order(gateway, "OF-DD-2", day=TUESDAY)
    assert {line.deliveryDate for line in second.lines} == {MONDAY}
    assert (second.decision, second.reasons) == ("ASK", [ReasonCode.OVER_AUTO_LIMIT])
    third = _order(gateway, "OF-DD-3", day=TUESDAY + timedelta(days=1))
    assert (third.decision, third.reasons) == ("ASK", [ReasonCode.OVER_AUTO_LIMIT])

    assert gateway.committed_total(M2.customerId, MONDAY) == Decimal("297.96") * 3
    assert gateway.committed_total(M2.customerId, TUESDAY) == 0  # nothing is delivered on Tuesday
    trail = get_audit_trail(gateway, proposal_id=second.proposalId)
    (decided,) = [e for e in trail.entries if e.event == "POLICY_DECIDED"]
    assert "€297.96 already committed for delivery on Mon 16 Nov; with this order €595.92" in decided.summary
    text = template_explanation(proposal=second, mandate=M2)
    assert "plus the orders already committed for delivery on Mon 16 Nov is above your €500.00" in text


def test_review_repro_three_requested_dates_one_delivery_day_m1():
    """Reviewer's repro: three €599.94 M1 orders requested for Sat 14, Sun 15 and Mon 16 Nov all deliver on
    Mon 16 Nov. The third takes the delivery day to €1,799.82, over the €1,500.00 auto-approve limit."""
    gateway = ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES),
                              provider=MutableSyntheticProvider())
    proposals = []
    for n, day in enumerate((MONDAY - timedelta(days=2), MONDAY - timedelta(days=1), MONDAY), start=1):
        offer_req = GetOfferRequest(
            customerId=M1.customerId, requestedDate=day,
            items=[OfferItem(itemId="I1", productId="SYN-MILK", quantity=Decimal("594"), unit="l")],
        )
        struct_req = StructuredRequest(
            requestText="594 l lactose-free milk.", requestedDate=day,
            items=[RequestItem(itemId="I1", what="milk", quantity=Decimal("594"), unit="l",
                               requirements=[LACTOSE_FREE])],
        )
        offer = gateway.get_offer(offer_req, offer_id=f"OF-REPRO-{n}").offer
        proposals.append(create_proposal(gateway, M1.customerId, offer.offerId, offer.offerVersion, struct_req))

    assert all({line.deliveryDate for line in p.lines} == {MONDAY} for p in proposals)
    assert ReasonCode.OVER_AUTO_LIMIT not in proposals[1].reasons  # €1,199.88 for the day: within €1,500.00
    assert ReasonCode.OVER_AUTO_LIMIT in proposals[2].reasons
    assert proposals[2].decision == "ASK"
    trail = get_audit_trail(gateway, proposal_id=proposals[2].proposalId)
    decided = [e for e in trail.entries if e.event == "POLICY_DECIDED"][-1]
    assert "€1,199.88 already committed for delivery on Mon 16 Nov; with this order €1,799.82" in decided.summary


def test_delivery_days_and_their_wording():
    assert delivery_dates([], MONDAY) == (MONDAY,)
    assert format_days([TUESDAY, MONDAY, MONDAY]) == "Mon 16 Nov and Tue 17 Nov"
    assert format_days([MONDAY, TUESDAY, TUESDAY + timedelta(days=1)]) == "Mon 16 Nov, Tue 17 Nov and Wed 18 Nov"
