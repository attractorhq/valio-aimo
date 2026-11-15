"""T108: no-order help for agents that call createProposal directly (REST and MCP).

When the gateway refuses a proposal with NO_CANDIDATE, the error details carry the same help the buyer UI
shows after an agent run: per item why, closest matches and the earliest deliverable date with a retry hint.
Information only: nothing here changes the decision.
"""

from typing import Any

from app.agent.explain import no_order_item_reason
from app.agent.plan import NoOrderItem, requirement_display_name
from app.catalog.search import SearchProductsRequest
from app.contracts import CreateProposalRequest, Offer, ReasonCode
from app.contracts.common import format_date
from app.gateway.proposals import NoOrderError
from app.policy.requirements import hard_requirements
from app.valio import IntegrationError

from .state import GatewayState


def attach_no_candidate_details(
    state: GatewayState, prop_req: CreateProposalRequest, offer: Offer, exc: NoOrderError,
) -> None:
    """Attach `no_order_details` to a NO_CANDIDATE refusal; error serializers add it as `details.noOrder`."""
    if ReasonCode.NO_CANDIDATE in exc.decision.reasons:
        exc.no_order_details = no_candidate_details(state, prop_req, offer, exc)  # type: ignore[attr-defined]


def no_candidate_details(
    state: GatewayState, prop_req: CreateProposalRequest, offer: Offer, exc: NoOrderError,
) -> dict[str, Any]:
    """The same no-order help the buyer UI shows: per item why, type-checked closest matches (never ordered),
    the earliest date the full request can be delivered, and how to retry with it."""
    from app.agent.run import GatewayAgentChannel, closest_matches, coverage_dates

    request = prop_req.request
    mandate = state.gateway.mandates.get(prop_req.customerId)
    channel = GatewayAgentChannel(state)
    stored = getattr(state.gateway, "_offers", {}).get((offer.offerId, offer.offerVersion))
    offer_request = getattr(stored, "offer_request", None)
    try:
        dates, earliest = coverage_dates(
            channel=channel, request=request, products=state.gateway.products, mandate=mandate,
            excluded=exc.excluded, offer=offer, offer_request=offer_request,
            now=state.now_for(offer.mode) or offer.createdAt,
        )
    except IntegrationError:
        dates, earliest = [None] * len(request.items), None
    flagged = {f.item_id for f in exc.decision.findings if f.code == ReasonCode.NO_CANDIDATE and f.item_id}
    deadline = request.requestedDate
    items = []
    for item, delivery in zip(request.items, dates, strict=True):
        if flagged and item.itemId not in flagged:
            continue
        requirements = hard_requirements(item.requirements, mandate)
        labels = [requirement_display_name(req) for req in requirements]
        item_excluded = [e for e in exc.excluded if e.itemId == item.itemId]
        has_candidates = bool(item_excluded) or any(ln.itemId == item.itemId for ln in offer.lines)
        search = SearchProductsRequest(query=item.what, requirements=item.requirements)
        items.append(NoOrderItem(
            itemId=item.itemId,
            searched=item.what + (f" · {', '.join(labels)}" if labels else ""),
            why=no_order_item_reason(
                has_candidates=has_candidates, deadline_label=format_date(deadline),
                delivery_label=format_date(delivery) if delivery and delivery > deadline else None,
                delivered=False, excluded=item_excluded, requirements=requirements,
            ),
            closestMatches=closest_matches(channel, prop_req.customerId, search, mandate),
        ).model_dump(mode="json"))
    retry_date = offer_request.lookupDate if offer_request is not None else None
    next_date = earliest if earliest and earliest > deadline and earliest != retry_date else None
    return {
        "items": items,
        "earliestDeliverableDate": next_date.isoformat() if next_date else None,
        "retryHint": (
            f"The full request can be delivered on {format_date(next_date)}. Call getOffer again with the same "
            f"items and lookupDate {next_date.isoformat()}, then createProposal; the later date needs approval."
            if next_date else None
        ),
    }
