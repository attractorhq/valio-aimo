"""REST route `POST /v0.1/agent/run`: streams the four purchasing steps for the UI (T10).

Uses only the public agent channel (`GatewayAgentChannel`). The response never contains an approval URL,
token or QR payload (patch P3, AC15).
"""

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime, Field

from app.agent.explain import explain_proposal, render_reason_occurrence
from app.agent.plan import NoOrderItem
from app.agent.run import (
    AgentRunResult,
    AgentStepEvent,
    GatewayAgentChannel,
    InvalidDeliveryRetry,
    feasible_order_for,
    iter_agent_steps,
    run_agent,
)
from app.contracts import (
    REASON_REGISTRY,
    GetOfferRequest,
    Mandate,
    NotOrderedItem,
    OfferItem,
    ReasonCode,
    ReasonEffect,
    Totals,
)
from app.contracts.common import (
    Contract,
    DecisionOutcome,
    Identifier,
    Money,
    NonNegativeDecimal,
    PositiveDecimal,
    ProposalState,
    format_date,
    format_money,
)

from . import agent_channel
from .auth import AgentCustomer, bind_customer
from .errors import ERROR_RESPONSES
from .state import GatewayState, get_state

router = APIRouter(prefix="/v0.1", tags=["Agent channel"], responses=ERROR_RESPONSES)
State = Annotated[GatewayState, Depends(get_state)]


class AgentRunRequest(Contract):
    """Input for `POST /v0.1/agent/run`."""

    customerId: Identifier
    requestText: str = Field(min_length=1, max_length=4000)
    stream: bool = False
    lookupDate: date | None = None


class CompactAgentLine(Contract):
    productId: Identifier
    name: str
    quantitySalesUnits: PositiveDecimal
    salesUnit: str
    unitPriceExclVat: NonNegativeDecimal
    lineTotalExclVat: Money


class CompactAgentLinks(Contract):
    proposal: str
    audit: str


class CompactReason(Contract):
    """One reason code with the same human sentence the buyer UI shows for it (T108)."""

    code: ReasonCode
    effect: ReasonEffect
    text: str


class CompactRetryRequest(Contract):
    """Ready-to-send body for `POST /v0.1/agent/run` that retries the same request on a later delivery day."""

    customerId: str
    requestText: str
    lookupDate: date


class CompactRetry(Contract):
    hint: str
    method: Literal["POST"] = "POST"
    path: str = "/v0.1/agent/run?view=compact"
    body: CompactRetryRequest


class CompactNoOrder(Contract):
    """Why no order was created, per requested item, with the UI's closest matches and earliest-date retry."""

    items: list[NoOrderItem]
    earliestDeliverableDate: date | None
    retry: CompactRetry | None


class CompactAgentRunResult(Contract):
    """Allow-listed decision summary, without candidate facts or approval capabilities."""

    summary: str = Field(description="One deterministic line: outcome, total and the decisive reason.")
    customerId: str
    mode: Literal["LIVE", "RECORDED"]
    recordedAt: AwareDatetime | None
    proposalId: Identifier | None
    decision: DecisionOutcome | None
    state: ProposalState | None
    reasons: list[ReasonCode]
    reasonTexts: list[CompactReason] = Field(
        description="Each code in `reasons`, in order, with its effect and the sentence the buyer UI shows.",
    )
    totals: Totals | None
    lines: list[CompactAgentLine]
    approverRole: str | None
    clarificationQuestion: str | None
    noOrder: CompactNoOrder | None = Field(
        default=None,
        description="Set when no proposal was created because items could not be matched or delivered in time.",
    )
    links: CompactAgentLinks | None
    # T106: requested lines this proposal does not order, each with its plain reason.
    notOrdered: list[NotOrderedItem] = Field(default_factory=list)


def _compact_no_order(result: AgentRunResult) -> CompactNoOrder | None:
    details = next((step.noOrder for step in reversed(result.steps) if step.noOrder is not None), None)
    if details is None:
        return None
    earliest = details.earliestDelivery
    retry = None
    if earliest is not None and result.request is not None:
        retry = CompactRetry(
            hint=(
                f"The full request can be delivered on {format_date(earliest)}. Send the same requestText with "
                f"lookupDate {earliest.isoformat()} to get a proposal for that day; the later date needs approval."
            ),
            body=CompactRetryRequest(
                customerId=result.customerId, requestText=result.request.requestText, lookupDate=earliest,
            ),
        )
    return CompactNoOrder(items=details.items, earliestDeliverableDate=earliest, retry=retry)


def _no_candidate_text(no_order: CompactNoOrder) -> str:
    parts: list[str] = []
    for item in no_order.items:
        sentence = f"{item.searched}: {item.why}".strip()
        if item.closestMatches:
            names = ", ".join(f"{m.name} ({m.productId})" for m in item.closestMatches)
            sentence = sentence.rstrip(".") + f". Closest matches (not ordered): {names}."
        parts.append(sentence)
    if no_order.earliestDeliverableDate is not None:
        parts.append(f"Earliest delivery: {format_date(no_order.earliestDeliverableDate)}.")
    return " ".join(parts) or REASON_REGISTRY[ReasonCode.NO_CANDIDATE].chipText


_LIMIT_CODES = (ReasonCode.OVER_AUTO_LIMIT, ReasonCode.OVER_HARD_LIMIT)


def _no_order_limit_text(code: ReasonCode, mandate: Mandate) -> str:
    """A limit finding next to NO_CANDIDATE: no proposal exists, so say that nothing waits for approval."""
    limit = mandate.hardLimitEur if code == ReasonCode.OVER_HARD_LIMIT else mandate.autoApproveLimitEur
    kind = "hard limit" if code == ReasonCode.OVER_HARD_LIMIT else "auto-approve limit"
    return (
        f"The quoted lines are above your {format_money(limit)} {kind}, but no order was created, "
        "so nothing awaits approval."
    )


def _reason_texts(
    result: AgentRunResult, mandate: Mandate | None, no_order: CompactNoOrder | None,
) -> list[CompactReason]:
    proposal = result.proposal
    reasons = list(proposal.reasons if proposal else result.reasons)
    excluded = result.steps[-1].excluded if result.steps else []
    out: list[CompactReason] = []
    for code in reasons:
        occurrence = render_reason_occurrence(
            code, proposal=proposal, mandate=mandate,
            clarification_question=result.clarificationQuestion, excluded=excluded,
        )
        text = occurrence.message
        if proposal is not None and code in proposal.reasonDetails:
            text = proposal.reasonDetails[code]  # The gateway's own wording, e.g. a same-day committed total.
        elif code == ReasonCode.NO_CANDIDATE and no_order is not None:
            text = _no_candidate_text(no_order)
        elif proposal is None and code in _LIMIT_CODES and mandate is not None:
            text = _no_order_limit_text(code, mandate)
        out.append(CompactReason(code=code, effect=occurrence.effect, text=text))
    return out


def _amount(value: Decimal) -> str:
    norm = value.normalize()
    return f"{int(norm):,}" if norm == norm.to_integral() else format(norm, ",f")


def _ordered_products(lines: list) -> str:
    """T112: "1,000 l · Valio Eila® ... (1,000 × 1 l)"; several lines: the first product and "and N more"."""
    if not lines:
        return ""
    first = lines[0]
    delivered = first.deliveredQuantity
    content = first.contentPerSalesUnit
    unit = delivered.unit
    text = f"{_amount(delivered.value)} {unit} · {' '.join(first.name.split())}"
    same_unit = content.unit == delivered.unit and content.value > 0
    if same_unit and content.value * first.quantitySalesUnits == delivered.value and not (
        content.value == 1 and first.salesUnit.upper() == content.unit.upper()
    ):
        text += f" ({_amount(first.quantitySalesUnits)} × {_amount(content.value)} {unit})"
    more = len(lines) - 1
    return f"{text} and {more} more" if more else text


def _compact_summary(
    result: AgentRunResult, reason_texts: list[CompactReason], no_order: CompactNoOrder | None,
) -> str:
    proposal = result.proposal
    if proposal is None:
        if result.clarificationQuestion:
            return f"No order: more detail needed. {result.clarificationQuestion}"
        if no_order is not None and no_order.items:
            why = " ".join(f"{item.searched}: {item.why}" for item in no_order.items)
            if no_order.retry is not None and no_order.earliestDeliverableDate is not None:
                day = format_date(no_order.earliestDeliverableDate)
                return f"No order: {why} Earliest full delivery {day}; send noOrder.retry to get a proposal for it."
            return f"No order: {why}"
        lead = "No order: the full request could not be fulfilled."
        return f"{lead} {reason_texts[0].text}" if reason_texts else lead
    total = format_money(proposal.totals.exclVat)
    lines = _ordered_products(proposal.lines) or "0 lines"
    decisive = [r.text for r in reason_texts if r.effect == proposal.decision]
    if proposal.decision == "AUTO":
        return f"AUTO: {total} excl. VAT, {lines}, within the mandate ({proposal.state})."
    if proposal.decision == "ASK":
        who = proposal.approverRole or "an approver"
        return f"ASK: {total} excl. VAT, {lines}, waits for {who} approval. {' '.join(decisive)}".strip()
    return f"BLOCK: {total} excl. VAT cannot be ordered. {' '.join(decisive)}".strip()


def _compact_result(result: AgentRunResult, mandate: Mandate | None = None) -> CompactAgentRunResult:
    proposal = result.proposal
    snapshot = proposal or result.offer
    pid = quote(proposal.proposalId, safe="") if proposal else None
    no_order = _compact_no_order(result) if proposal is None else None
    reason_texts = _reason_texts(result, mandate, no_order)
    return CompactAgentRunResult(
        summary=_compact_summary(result, reason_texts, no_order),
        customerId=result.customerId,
        mode=result.mode,
        recordedAt=snapshot.recordedAt if snapshot else None,
        proposalId=proposal.proposalId if proposal else None,
        decision=proposal.decision if proposal else None,
        state=proposal.state if proposal else None,
        reasons=list(proposal.reasons if proposal else result.reasons),
        reasonTexts=reason_texts,
        totals=proposal.totals if proposal else None,
        lines=[CompactAgentLine(**{
            field: getattr(line, field) for field in CompactAgentLine.model_fields
        }) for line in proposal.lines] if proposal else [],
        approverRole=proposal.approverRole if proposal else None,
        clarificationQuestion=result.clarificationQuestion,
        noOrder=no_order,
        links=CompactAgentLinks(
            proposal=f"/v0.1/proposals/{pid}", audit=f"/v0.1/audit?proposalId={pid}",
        ) if proposal else None,
        notOrdered=list(proposal.notOrdered) if proposal else [],
    )


RUN_DESCRIPTION = (
    "Runs the purchasing agent and the deterministic gateway policy for one buyer request.\n\n"
    "**Agents: use `?view=compact`.** The default `view=full` returns the whole `AgentRunResult` "
    "(steps, plan, candidates and Trust Label facts; often tens of KB). `view=compact` returns a "
    "`CompactAgentRunResult` of a few KB: a one-line `summary`, the decision and state, `reasons` "
    "plus `reasonTexts` (each code with its effect and the same sentence the buyer UI shows, e.g. why "
    "OVER_AUTO_LIMIT fired), totals, the selected lines (sales-unit quantities and prices excl. VAT), and "
    "links to the full proposal and audit trail. Compact responses contain no facts or approval "
    "capabilities.\n\n"
    "If no proposal is created, decision/state/proposalId/totals/links are null and lines is empty. "
    "`clarificationQuestion` says what is missing; for NO_CANDIDATE, `noOrder.items` lists each search with "
    "why it failed and `closestMatches` (type-checked, never ordered), and `noOrder.earliestDeliverableDate` "
    "plus `noOrder.retry` give a ready request body (same requestText, `lookupDate`) for the earliest day "
    "the full request can be delivered. The full view carries the same data in `steps[-1].noOrder`.\n\n"
    "Compact view cannot be combined with SSE/NDJSON streaming (HTTP 422)."
)

VIEW_DESCRIPTION = (
    "Response detail. `compact` (recommended for agents): a few-KB decision summary with a one-line "
    "`summary`, reason codes with their human text, totals, lines, links and, when nothing is ordered, "
    "closest matches and the earliest-date retry. `full` (default): the complete AgentRunResult used by the "
    "buyer UI. Compact is non-streaming JSON only."
)

_COMPACT_ASK_EXAMPLE = {
    "summary": (
        "ASK: €506.16 excl. VAT, 2 lines, waits for Head Chef approval. "
        "€506.16 excl. VAT is above your €500.00 auto-approve limit."
    ),
    "customerId": "northbay-school-kitchen",
    "mode": "RECORDED",
    "recordedAt": "2026-11-13T21:52:02+02:00",
    "proposalId": "PR-EXAMPLE-v1",
    "decision": "ASK",
    "state": "PENDING_APPROVAL",
    "reasons": ["OVER_AUTO_LIMIT"],
    "reasonTexts": [{
        "code": "OVER_AUTO_LIMIT", "effect": "ASK",
        "text": "€506.16 excl. VAT is above your €500.00 auto-approve limit.",
    }],
    "totals": {"exclVat": "506.16", "vat": "68.33", "inclVat": "574.49", "currency": "EUR"},
    "lines": [
        {"productId": "MOCK-PASTA", "name": "Synthetic gluten-free macaroni", "quantitySalesUnits": "18",
         "salesUnit": "ST", "unitPriceExclVat": "22.92", "lineTotalExclVat": "412.56"},
        {"productId": "MOCK-OAT", "name": "Synthetic gluten-free oat drink", "quantitySalesUnits": "80",
         "salesUnit": "ST", "unitPriceExclVat": "1.17", "lineTotalExclVat": "93.60"},
    ],
    "approverRole": "Head Chef",
    "clarificationQuestion": None,
    "noOrder": None,
    "links": {"proposal": "/v0.1/proposals/PR-EXAMPLE-v1", "audit": "/v0.1/audit?proposalId=PR-EXAMPLE-v1"},
}

_COMPACT_NO_CANDIDATE_EXAMPLE = {
    "summary": (
        "No order: salmon fillet: Matching products deliver Mon 16 Nov, after your Sun 15 Nov deadline. "
        "Earliest full delivery Mon 16 Nov; send noOrder.retry to get a proposal for it."
    ),
    "customerId": "nordic-harbour-hotels",
    "mode": "RECORDED",
    "recordedAt": None,
    "proposalId": None,
    "decision": None,
    "state": None,
    "reasons": ["NO_CANDIDATE"],
    "reasonTexts": [{
        "code": "NO_CANDIDATE", "effect": "NO_ORDER",
        "text": (
            "salmon fillet: Matching products deliver Mon 16 Nov, after your Sun 15 Nov deadline. "
            "Closest matches (not ordered): Synthetic salmon fillet 1 kg (MOCK-SALMON). Earliest delivery: Mon 16 Nov."
        ),
    }],
    "totals": None,
    "lines": [],
    "approverRole": None,
    "clarificationQuestion": None,
    "noOrder": {
        "items": [{
            "itemId": "item-1", "searched": "salmon fillet",
            "why": "Matching products deliver Mon 16 Nov, after your Sun 15 Nov deadline.",
            "closestMatches": [{"productId": "MOCK-SALMON", "name": "Synthetic salmon fillet 1 kg"}],
        }],
        "earliestDeliverableDate": "2026-11-16",
        "retry": {
            "hint": (
                "The full request can be delivered on Mon 16 Nov. Send the same requestText with lookupDate "
                "2026-11-16 to get a proposal for that day; the later date needs approval."
            ),
            "method": "POST",
            "path": "/v0.1/agent/run?view=compact",
            "body": {
                "customerId": "nordic-harbour-hotels",
                "requestText": "10 kg salmon fillet today",
                "lookupDate": "2026-11-16",
            },
        },
    },
    "links": None,
}

RUN_RESPONSES: dict[int | str, dict] = {200: {
    "description": (
        "The agent run. With `?view=compact` (recommended for agents) a CompactAgentRunResult of a few KB "
        "with reason texts and a one-line summary; otherwise the full AgentRunResult. Examples are synthetic."
    ),
    "content": {"application/json": {"examples": {
        "compact_ask": {"summary": "view=compact · ASK above the auto-approve limit", "value": _COMPACT_ASK_EXAMPLE},
        "compact_no_candidate": {
            "summary": "view=compact · no order, with closest matches and the earliest-date retry",
            "value": _COMPACT_NO_CANDIDATE_EXAMPLE,
        },
    }}},
}}


def _assemble_result_from_steps(
    customer_id: str,
    steps: list[AgentStepEvent],
    channel: GatewayAgentChannel,
    lookup_date: date | None,
) -> AgentRunResult:
    from app.agent.plan import AgentPlan

    mandate = channel.get_mandate(customer_id)
    last = steps[-1]
    step1 = steps[0]
    step2 = next((s for s in steps if s.step == 2), None)
    step3 = next((s for s in steps if s.step == 3), None)

    req = last.request or step1.request
    offer = last.offer or (step3.offer if step3 else None)
    proposal = last.proposal
    mode = (
        proposal.mode
        if proposal is not None
        else (offer.mode if offer is not None else channel.state.mode)
    )

    plan_obj: AgentPlan | None = None
    if req is not None and step2 is not None and step2.plan is not None:
        all_offer_items: list[OfferItem] = []
        for ip in step2.plan:
            for pid in ip.shortlistIds:
                all_offer_items.append(
                    OfferItem(
                        itemId=ip.item.itemId,
                        productId=pid,
                        quantity=ip.item.quantity,
                        unit=ip.item.unit,
                    )
                )
        if all_offer_items:
            plan_obj = AgentPlan(
                customerId=customer_id,
                mandateVersion=mandate.version,
                request=req,
                items=step2.plan,
                offerRequest=GetOfferRequest(
                    customerId=customer_id,
                    requestedDate=req.requestedDate,
                    items=all_offer_items,
                    lookupDate=lookup_date,
                ),
            )

    explanation = last.explanation or explain_proposal(
        proposal=proposal,
        mandate=mandate,
        reasons=last.reasons,
        clarification_question=last.clarificationQuestion,
        excluded=last.excluded,
    )

    return AgentRunResult(
        customerId=customer_id,
        mode=mode,
        steps=steps,
        request=req,
        clarificationQuestion=last.clarificationQuestion,
        clarificationOptions=last.clarificationOptions,
        plan=plan_obj,
        offer=offer,
        proposal=proposal,
        decision=last.decision,
        explanation=explanation,
        reasons=list(last.reasons),
        feasibleWithinHardLimit=feasible_order_for(proposal, mandate, channel),
    )


def _stream_sse(
    customer_id: str,
    request_text: str,
    channel: GatewayAgentChannel,
    lookup_date: date | None,
    call: agent_channel.AgentCall | None,
) -> Iterator[str]:
    steps: list[AgentStepEvent] = []
    for step_event in iter_agent_steps(
        customer_id,
        request_text,
        channel=channel,
        lookup_date=lookup_date,
        catch_stream_errors=True,
    ):
        if step_event.name == "ERROR":
            if call is not None:
                call.stream_failed = True
            yield f"event: step\ndata: {step_event.model_dump_json()}\n\n"
            yield f"event: error\ndata: {json.dumps({'error': step_event.error}, ensure_ascii=False)}\n\n"
            return
        steps.append(step_event)
        yield f"event: step\ndata: {step_event.model_dump_json()}\n\n"
    if steps:
        final = _assemble_result_from_steps(customer_id, steps, channel, lookup_date)
        if call is not None:
            call.instruction_refused = ReasonCode.INSTRUCTION_IGNORED in final.reasons
        yield f"event: result\ndata: {final.model_dump_json()}\n\n"


def _stream_ndjson(
    customer_id: str,
    request_text: str,
    channel: GatewayAgentChannel,
    lookup_date: date | None,
    call: agent_channel.AgentCall | None,
) -> Iterator[str]:
    steps: list[AgentStepEvent] = []
    for step_event in iter_agent_steps(
        customer_id,
        request_text,
        channel=channel,
        lookup_date=lookup_date,
        catch_stream_errors=True,
    ):
        if step_event.name == "ERROR":
            if call is not None:
                call.stream_failed = True
            payload = {"type": "error", **step_event.model_dump(mode="json")}
            yield json.dumps(payload, ensure_ascii=False) + "\n"
            return
        steps.append(step_event)
        payload = {"type": "step", **step_event.model_dump(mode="json")}
        yield json.dumps(payload, ensure_ascii=False) + "\n"
    if steps:
        final = _assemble_result_from_steps(customer_id, steps, channel, lookup_date)
        if call is not None:
            call.instruction_refused = ReasonCode.INSTRUCTION_IGNORED in final.reasons
        payload = {"type": "result", **final.model_dump(mode="json")}
        yield json.dumps(payload, ensure_ascii=False) + "\n"


@router.post(
    "/agent/run",
    response_model=AgentRunResult | CompactAgentRunResult,
    operation_id="runAgent",
    summary="Run the purchasing agent across the four steps (Request, Lookup, Order, Decision)",
    description=RUN_DESCRIPTION,
    responses=RUN_RESPONSES,
)
def run_agent_route(
    body: Annotated[AgentRunRequest, Body(openapi_examples={
        "hotel_milk": {
            "summary": "Lactose-free milk for the hotel",
            "value": {
                "customerId": "nordic-harbour-hotels",
                "requestText": (
                    "I need 1,000 litres of lactose-free milk by Monday. "
                    "Find an option that meets our requirements based on price and availability."
                ),
            },
        },
        "school_gluten_free": {
            "summary": "Gluten-free groceries for the school kitchen",
            "value": {
                "customerId": "northbay-school-kitchen",
                "requestText": "We need 90 kg of gluten-free macaroni and 80 litres of gluten-free oat drink by Monday.",
            },
        },
    })],
    request: Request,
    state: State,
    customer: AgentCustomer,
    stream: Annotated[bool, Query(description="Stream Server-Sent Events for the four steps")] = False,
    view: Annotated[Literal["full", "compact"], Query(description=VIEW_DESCRIPTION, openapi_examples={
        "compact": {"summary": "Compact decision summary (recommended for agents)", "value": "compact"},
        "full": {"summary": "Full AgentRunResult", "value": "full"},
    })] = "full",
) -> AgentRunResult | CompactAgentRunResult | StreamingResponse:
    bind_customer(customer, body.customerId)
    # The authenticated buyer is the only authority for the request's customer; unknown customers return 404.
    state.gateway.mandates.get(body.customerId)
    channel = GatewayAgentChannel(state)
    call: agent_channel.AgentCall | None = getattr(request.state, "agent_call", None)
    accept = (request.headers.get("accept") or "").lower()
    if view == "compact" and (
        body.stream or stream or "text/event-stream" in accept or "application/x-ndjson" in accept
    ):
        raise HTTPException(status_code=422, detail="view=compact cannot be combined with streaming.")

    if body.stream or stream or "text/event-stream" in accept:
        return StreamingResponse(
            _stream_sse(body.customerId, body.requestText, channel, body.lookupDate, call),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    if "application/x-ndjson" in accept:
        return StreamingResponse(
            _stream_ndjson(body.customerId, body.requestText, channel, body.lookupDate, call),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    try:
        result = run_agent(body.customerId, body.requestText, channel=channel, lookup_date=body.lookupDate)
        if call is not None:
            call.instruction_refused = ReasonCode.INSTRUCTION_IGNORED in result.reasons
        if view == "compact":
            return _compact_result(result, channel.get_mandate(body.customerId))
        return result
    except InvalidDeliveryRetry as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Register on the shared agent_channel router so `app.main:app` serves `/v0.1/agent/run`.
if not any( getattr(r, "path", None) == "/v0.1/agent/run" for r in agent_channel.router.routes ):
    agent_channel.router.add_api_route(
        "/agent/run",
        run_agent_route,
        methods=["POST"],
        response_model=AgentRunResult | CompactAgentRunResult,
        operation_id="runAgent",
        summary="Run the purchasing agent across the four steps (Request, Lookup, Order, Decision)",
        description=RUN_DESCRIPTION,
        responses=RUN_RESPONSES,
    )

__all__ = ["AgentRunRequest", "router", "run_agent_route"]
