"""REST route `POST /v0.1/agent/run`: streams the four purchasing steps for the UI (T10).

Uses only the public agent channel (`GatewayAgentChannel`). The response never contains an approval URL,
token or QR payload (patch P3, AC15).
"""

import json
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime, Field

from app.agent.explain import explain_proposal
from app.agent.run import (
    AgentRunResult,
    AgentStepEvent,
    GatewayAgentChannel,
    InvalidDeliveryRetry,
    feasible_order_for,
    iter_agent_steps,
    run_agent,
)
from app.contracts import GetOfferRequest, NotOrderedItem, OfferItem, ReasonCode, Totals
from app.contracts.common import (
    Contract,
    DecisionOutcome,
    Identifier,
    Money,
    NonNegativeDecimal,
    PositiveDecimal,
    ProposalState,
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


class CompactAgentRunResult(Contract):
    """Allow-listed decision summary, without candidate facts or approval capabilities."""

    customerId: str
    mode: Literal["LIVE", "RECORDED"]
    recordedAt: AwareDatetime | None
    proposalId: Identifier | None
    decision: DecisionOutcome | None
    state: ProposalState | None
    reasons: list[ReasonCode]
    totals: Totals | None
    lines: list[CompactAgentLine]
    approverRole: str | None
    clarificationQuestion: str | None
    links: CompactAgentLinks | None
    # T106: requested lines this proposal does not order, each with its plain reason.
    notOrdered: list[NotOrderedItem] = Field(default_factory=list)


def _compact_result(result: AgentRunResult) -> CompactAgentRunResult:
    proposal = result.proposal
    snapshot = proposal or result.offer
    pid = quote(proposal.proposalId, safe="") if proposal else None
    return CompactAgentRunResult(
        customerId=result.customerId,
        mode=result.mode,
        recordedAt=snapshot.recordedAt if snapshot else None,
        proposalId=proposal.proposalId if proposal else None,
        decision=proposal.decision if proposal else None,
        state=proposal.state if proposal else None,
        reasons=list(proposal.reasons if proposal else result.reasons),
        totals=proposal.totals if proposal else None,
        lines=[CompactAgentLine(**{
            field: getattr(line, field) for field in CompactAgentLine.model_fields
        }) for line in proposal.lines] if proposal else [],
        approverRole=proposal.approverRole if proposal else None,
        clarificationQuestion=result.clarificationQuestion,
        links=CompactAgentLinks(
            proposal=f"/v0.1/proposals/{pid}", audit=f"/v0.1/audit?proposalId={pid}",
        ) if proposal else None,
        notOrdered=list(proposal.notOrdered) if proposal else [],
    )


RUN_DESCRIPTION = (
    "Defaults to the full AgentRunResult. Set view=compact for a JSON decision summary with "
    "totals, selected product quantities/prices, and links to the full proposal and audit. "
    "Compact responses contain no facts or approval capabilities. If no proposal is created, "
    "decision/state/proposalId/totals/links are null and lines is empty; reasons and any "
    "clarificationQuestion explain why. Compact view cannot be combined with SSE/NDJSON "
    "streaming (HTTP 422)."
)


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
    view: Annotated[Literal["full", "compact"], Query(description="JSON response detail; compact is non-streaming only")] = "full",
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
        return _compact_result(result) if view == "compact" else result
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
    )

__all__ = ["AgentRunRequest", "router", "run_agent_route"]
