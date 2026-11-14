"""T62: the split-order guard. The auto-approve and hard limits apply to what a customer already has committed
for the same delivery date plus the new order, so a need split into small orders can never pass unnoticed."""

from datetime import timedelta
from decimal import Decimal

from app.agent.explain import template_explanation
from app.contracts import GetOfferRequest, OfferItem, ReasonCode, RequestItem, StructuredRequest
from app.gateway.proposals import ProposalGateway, create_proposal, decide_approval, get_audit_trail
from app.policy import DEMO_MANDATES, M2, MandateBook, decide
from tests.synthetic.policy import NOW, line, product, request
from tests.synthetic.policy import item as policy_item
from tests.test_proposals_gateway import GLUTEN_FREE, MONDAY, PRODUCTS, MutableSyntheticProvider, price_row

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
        "€300.00 already committed for Mon 16 Nov; with this order €600.00 > €500.00 auto-approve limit"
    )
    assert decision.committed_excl_vat == Decimal("300.00")


def test_committed_same_day_orders_count_against_the_hard_limit():
    decision = _decide("300.00", "2900.00")
    assert decision.outcome == "BLOCK"
    assert ReasonCode.OVER_HARD_LIMIT in decision.reasons
    (finding,) = [f for f in decision.findings if f.code == ReasonCode.OVER_HARD_LIMIT]
    assert finding.detail == (
        "€2,900.00 already committed for Mon 16 Nov; with this order €3,200.00 > €3,000.00 hard limit"
    )


def test_staying_within_the_limit_with_committed_orders_is_still_auto():
    assert _decide("100.00", "300.00").outcome == "AUTO"


# --------------------------------------------------------------------------- gateway: real proposals


def _gateway():
    return ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES), provider=MutableSyntheticProvider())


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
    assert "€297.96 already committed for Mon 16 Nov; with this order €595.92" in decided.summary
    assert "€500.00 auto-approve limit" in decided.summary

    # The explanation never claims the €297.96 order alone is over the limit.
    text = template_explanation(proposal=second, mandate=M2)
    assert "plus the orders already committed for Mon 16 Nov is above your €500.00 auto-approve limit" in text


def test_another_delivery_date_does_not_count():
    gateway = _gateway()
    _order(gateway, "OF-DAY-1")
    tuesday = _order(gateway, "OF-DAY-2", day=TUESDAY)
    assert tuesday.decision == "AUTO"
    assert gateway.committed_total(M2.customerId, TUESDAY) == Decimal("297.96")


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
    assert "€412.56 already committed for Mon 16 Nov; with this order €527.21" in decided.summary
    # The superseded version no longer counts; the new one does.
    assert gateway.committed_total(M2.customerId, MONDAY) == Decimal("412.56") + v2.totals.exclVat
