"""REST route `POST /v0.1/agent/run`: streams the four purchasing steps for the UI (T10).

Uses only the public agent channel (`GatewayAgentChannel`). The response never contains an approval URL,
token or QR payload (patch P3, AC15).
"""

import json
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.agent.explain import explain_proposal
from app.agent.run import (
    AgentRunResult,
    AgentStepEvent,
    GatewayAgentChannel,
    feasible_order_for,
    iter_agent_steps,
    run_agent,
)
from app.contracts import GetOfferRequest, OfferItem
from app.contracts.common import Contract, Identifier

from . import agent_channel
from .errors import ERROR_RESPONSES
from .state import GatewayState, get_state

router = APIRouter(prefix="/v0.1", tags=["Agent channel"], responses=ERROR_RESPONSES)
State = Annotated[GatewayState, Depends(get_state)]


class AgentRunRequest(Contract):
    """Input for `POST /v0.1/agent/run`."""

    customerId: Identifier
    requestText: str = Field(min_length=1, max_length=4000)
    stream: bool = False


def _assemble_result_from_steps(
    customer_id: str,
    steps: list[AgentStepEvent],
    channel: GatewayAgentChannel,
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
) -> Iterator[str]:
    steps: list[AgentStepEvent] = []
    for step_event in iter_agent_steps(
        customer_id,
        request_text,
        channel=channel,
        catch_stream_errors=True,
    ):
        if step_event.name == "ERROR":
            yield f"event: step\ndata: {step_event.model_dump_json()}\n\n"
            yield f"event: error\ndata: {json.dumps({'error': step_event.error}, ensure_ascii=False)}\n\n"
            return
        steps.append(step_event)
        yield f"event: step\ndata: {step_event.model_dump_json()}\n\n"
    if steps:
        final = _assemble_result_from_steps(customer_id, steps, channel)
        yield f"event: result\ndata: {final.model_dump_json()}\n\n"


def _stream_ndjson(
    customer_id: str,
    request_text: str,
    channel: GatewayAgentChannel,
) -> Iterator[str]:
    steps: list[AgentStepEvent] = []
    for step_event in iter_agent_steps(
        customer_id,
        request_text,
        channel=channel,
        catch_stream_errors=True,
    ):
        if step_event.name == "ERROR":
            payload = {"type": "error", **step_event.model_dump(mode="json")}
            yield json.dumps(payload, ensure_ascii=False) + "\n"
            return
        steps.append(step_event)
        payload = {"type": "step", **step_event.model_dump(mode="json")}
        yield json.dumps(payload, ensure_ascii=False) + "\n"
    if steps:
        final = _assemble_result_from_steps(customer_id, steps, channel)
        payload = {"type": "result", **final.model_dump(mode="json")}
        yield json.dumps(payload, ensure_ascii=False) + "\n"


@router.post(
    "/agent/run",
    response_model=AgentRunResult,
    operation_id="runAgent",
    summary="Run the purchasing agent across the four steps (Request, Lookup, Order, Decision)",
)
def run_agent_route(
    body: AgentRunRequest,
    request: Request,
    state: State,
    stream: Annotated[bool, Query(description="Stream Server-Sent Events for the four steps")] = False,
) -> AgentRunResult | StreamingResponse:
    # Validate that the customer mandate exists up-front so unknown customers return HTTP 404.
    state.gateway.mandates.get(body.customerId)
    channel = GatewayAgentChannel(state)
    accept = (request.headers.get("accept") or "").lower()

    if body.stream or stream or "text/event-stream" in accept:
        return StreamingResponse(
            _stream_sse(body.customerId, body.requestText, channel),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    if "application/x-ndjson" in accept:
        return StreamingResponse(
            _stream_ndjson(body.customerId, body.requestText, channel),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return run_agent(body.customerId, body.requestText, channel=channel)


# Register on the shared agent_channel router so `app.main:app` serves `/v0.1/agent/run`.
if not any( getattr(r, "path", None) == "/v0.1/agent/run" for r in agent_channel.router.routes ):
    agent_channel.router.add_api_route(
        "/agent/run",
        run_agent_route,
        methods=["POST"],
        response_model=AgentRunResult,
        operation_id="runAgent",
        summary="Run the purchasing agent across the four steps (Request, Lookup, Order, Decision)",
    )

__all__ = ["AgentRunRequest", "router", "run_agent_route"]
