"""Agent channel /v0.1 (patch P1.5): any agent may call these; none of them can approve or confirm.

Agent responses never carry an approval token, URL or QR payload (P3, AC15): those exist only on the
approver channel. The server alone decides AUTO / ASK / BLOCK and confirms after a live re-check.
"""

from dataclasses import dataclass
from inspect import isawaitable
from time import perf_counter
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from pydantic import Field

from app.catalog.search import SearchProductsRequest, SearchProductsResult, search_products
from app.contracts import (
    AuditTrail,
    CreateProposalRequest,
    GetOfferRequest,
    Mandate,
    Offer,
    Proposal,
    ReasonCode,
)
from app.contracts.common import Contract
from app.gateway.log import (
    AgentAccess,
    AgentActivity,
    AgentActivitySnapshot,
    AgentOutcome,
    AuditChainVerification,
    agent_client_label,
    aggregate_audit_trails,
    aggregate_audit_verification,
)
from app.gateway.proposals import NoOrderError, ProposalRequestMismatchError

from .approver_channel import ApprovalState
from .auth import (
    AgentCustomer,
    agent_customer,
    audit_scope,
    bind_customer,
    proposal_read_scope,
    require_admin,
)
from .errors import ERROR_RESPONSES, error
from .state import (
    GatewayState,
    GatewayUnavailable,
    find_proposal_state,
    get_state,
    iter_audit_sessions,
    utc_now,
)

State = Annotated[GatewayState, Depends(get_state)]
FORBIDDEN_CAPABILITY_KEYS = frozenset({"approvalToken", "approvalUrl", "qrPayload"})
_AGENT_WRITES = frozenset({"getOffer", "createProposal", "runAgent"})


def has_approval_field_error(errors: list[dict]) -> bool:
    return any(
        issue.get("type") == "extra_forbidden" and issue.get("loc")
        and issue["loc"][-1] in FORBIDDEN_CAPABILITY_KEYS
        for issue in errors
    )


@dataclass
class AgentCall:
    operation: str
    access: AgentAccess
    client: str
    customer: str | None
    started: float
    activity: AgentActivity | None = None
    stream_failed: bool = False
    instruction_refused: bool = False

    def record(self, outcome: AgentOutcome, reason: str | None = None, *, count_refusal: bool = False) -> None:
        if self.activity is not None:
            self.activity.record(
                customer=self.customer, client=self.client, operation=self.operation, access=self.access,
                outcome=outcome, reason=reason, count_refusal=count_refusal, timestamp=utc_now(),
                duration_ms=round((perf_counter() - self.started) * 1000),
            )

    def record_success(self) -> None:
        if self.instruction_refused:
            self.record("REFUSED", "A policy instruction in the request was ignored.", count_refusal=True)
        else:
            self.record("OK")


class AgentControlError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def _attach_activity(request: Request, state: State) -> None:
    call: AgentCall | None = getattr(request.state, "agent_call", None)
    if call is None:
        return
    call.activity = state.gateway.log.agent_activity
    if call.customer and (block := call.activity.block(call.customer, call.client, call.access)):
        raise AgentControlError(*block)


class AgentRoute(APIRoute):
    """Observe the result of agent REST calls, including validation and streaming failures."""

    def __init__(self, path: str, endpoint, **options):
        if path.startswith("/v0.1/admin/"):
            options["tags"] = ["Buyer admin"]
            options["dependencies"] = [
                dependency for dependency in (options.get("dependencies") or ())
                if dependency.dependency is not _attach_activity
            ]
        super().__init__(path, endpoint, **options)

    def get_route_handler(self):
        handler = super().get_route_handler()
        if "/admin/" in self.path:
            return handler
        operation = self.operation_id or self.name

        async def observe(request: Request):
            try:
                customer = agent_customer(request)
            except HTTPException:
                customer = None
            call = AgentCall(
                operation=operation, access="WRITE" if operation in _AGENT_WRITES else "READ",
                client=agent_client_label(request.headers.get("user-agent")),
                customer=customer, started=perf_counter(),
            )
            request.state.agent_call = call
            try:
                response = await handler(request)
            except AgentControlError as exc:
                call.record("REFUSED", str(exc))
                return error(403, exc.code, str(exc))
            except RequestValidationError as exc:
                if call.activity is None:
                    # Invalid JSON fails before dependency resolution. Try the same sandbox for the record.
                    overrides = getattr(self.dependency_overrides_provider, "dependency_overrides", {})
                    loader = overrides.get(get_state, get_state)
                    try:
                        state = loader(request) if loader is get_state else loader()
                        if isawaitable(state):
                            state = await state
                        call.activity = state.gateway.log.agent_activity
                    except (GatewayUnavailable, HTTPException):
                        pass
                if has_approval_field_error(exc.errors()):
                    call.record("REFUSED", "Approval fields are forbidden in agent requests.", count_refusal=True)
                else:
                    call.record("ERROR", "Request validation failed.")
                raise
            except HTTPException as exc:
                if exc.status_code in (401, 403):
                    call.record("REFUSED", "Agent authorization failed.")
                else:
                    call.record("ERROR", f"HTTP {exc.status_code}.")
                raise
            except NoOrderError:
                call.record("REFUSED", "No order could be confirmed.")
                raise
            except Exception:
                call.record("ERROR", "Gateway could not complete the call.")
                raise
            if isinstance(response, StreamingResponse):
                iterator = response.body_iterator

                async def observed_stream():
                    complete = False
                    try:
                        async for chunk in iterator:
                            yield chunk
                        complete = True
                    finally:
                        if complete and not call.stream_failed:
                            call.record_success()
                        else:
                            call.record("ERROR", "Agent stream did not complete successfully.")

                response.body_iterator = observed_stream()
            elif response.status_code < 400:
                call.record_success()
            else:
                call.record("ERROR")
            return response

        return observe


router = APIRouter(prefix="/v0.1", tags=["Agent channel"], responses=ERROR_RESPONSES,
                   route_class=AgentRoute, dependencies=[Depends(_attach_activity)])


class AgentPauseRequest(Contract):
    paused: bool


class ResumeAgentRequest(Contract):
    clientId: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.get("/admin/agent-activity", response_model=AgentActivitySnapshot,
            tags=["Buyer admin"], dependencies=[Depends(require_admin)],
            openapi_extra={"security": [{"demoAdmin": []}]})
def get_agent_activity(state: State) -> AgentActivitySnapshot:
    return state.gateway.log.agent_activity.snapshot()


@router.patch("/admin/agent-activity", response_model=AgentActivitySnapshot,
              tags=["Buyer admin"], dependencies=[Depends(require_admin)])
def pause_agent_activity(body: AgentPauseRequest, state: State) -> AgentActivitySnapshot:
    activity = state.gateway.log.agent_activity
    activity.set_paused(body.paused, utc_now())
    return activity.snapshot()


@router.post("/admin/agent-activity/resume", response_model=AgentActivitySnapshot,
             tags=["Buyer admin"], dependencies=[Depends(require_admin)])
def resume_agent_activity(body: ResumeAgentRequest, state: State) -> AgentActivitySnapshot:
    activity = state.gateway.log.agent_activity
    if not activity.resume(body.clientId, utc_now()):
        raise HTTPException(404, "No suspended agent with that ID in this session.")
    return activity.snapshot()

SCHOOL_DELIVERY = "2026-11-16"
SCHOOL_CUSTOMER = "northbay-school-kitchen"
GLUTEN_FREE = {"kind": "CLAIM", "code": "FREE_FROM_GLUTEN"}
SCHOOL_SHORTLIST = [
    {"itemId": "I1", "productId": "474235", "quantity": "90", "unit": "kg"},
    {"itemId": "I2", "productId": "468465", "quantity": "80", "unit": "l"},
]


@router.post("/products/search", response_model=SearchProductsResult, operation_id="searchProducts",
             summary="Static product lookup with hard requirements and an excluded list")
def search(
    body: Annotated[SearchProductsRequest, Body(openapi_examples={
        "school_gluten_free": {
            "summary": "Look up gluten-free macaroni",
            "description": "Set the customerId query parameter to northbay-school-kitchen to apply its mandate.",
            "value": {"query": "macaroni", "itemId": "I1", "requirements": [GLUTEN_FREE], "limit": 5},
        },
    })],
    state: State,
    customer: AgentCustomer,
    customerId: Annotated[str | None, Query(description="Apply this customer's mandate (allergen mode, "
                                                       "forbidden allergens)")] = None,
) -> SearchProductsResult:
    mandate = state.gateway.mandates.get(bind_customer(customer, customerId))
    return search_products(body, mandate=mandate, products=state.catalog)


@router.post("/offers", response_model=Offer, operation_id="getOffer",
             summary="Live price + availability for the shortlist → an offer at fixed prices",
             description=(
                 "Quotes the shortlist and computes `offerHash` over `customerId`, `requestedDate`, `mode`, "
                 "confirmed `lines` (prices, VAT, quantities, dates, cut-offs, trust facts), `totals`, "
                 "excluded `productId`/`itemId` entries, and `offerId` when every candidate is excluded."
             ))
def get_offer(
    body: Annotated[GetOfferRequest, Body(openapi_examples={
        "school_gluten_free": {
            "summary": "Quote the school kitchen's shortlist",
            "description": "Use the returned offerId and offerVersion to create the proposal.",
            "value": {"customerId": SCHOOL_CUSTOMER, "requestedDate": SCHOOL_DELIVERY, "items": SCHOOL_SHORTLIST},
        },
    })],
    state: State,
    customer: AgentCustomer,
) -> Offer:
    bind_customer(customer, body.customerId)
    return state.gateway.get_offer(body).offer


COMPACT_HINT = (
    "Shortcut for agents: `POST /v0.1/agent/run?view=compact` runs search, offer and proposal in one call and "
    "returns a few-KB summary (one-line `summary`, reason codes with their human text, totals, lines, links)."
)


@router.post("/proposals", response_model=Proposal, status_code=201, operation_id="createProposal",
             summary="Bind the request to an offer version; the gateway decides AUTO / ASK / BLOCK",
             description=(
                 "Returns the full Proposal. If nothing can be ordered the gateway answers 422 `NO_ORDER`; for "
                 "NO_CANDIDATE, `error.details.noOrder` lists each unmatched item with why, `closestMatches` "
                 "(never ordered) and `earliestDeliverableDate` with a `retryHint` (repeat getOffer with "
                 "`lookupDate`). " + COMPACT_HINT
             ))
def create_proposal_route(
    body: Annotated[CreateProposalRequest, Body(openapi_examples={
        "school_gluten_free": {
            "summary": "Create a proposal from the school kitchen's offer",
            "description": (
                "Run the offers example first in this browser. Replace offerId and offerVersion with "
                "the returned values. Use the same customer and requested date before the offer expires."
            ),
            "value": {
                "customerId": SCHOOL_CUSTOMER,
                "offerId": "REPLACE_WITH_OFFER_ID",
                "offerVersion": 1,
                "request": {
                    "requestText": "We need 90 kg of gluten-free macaroni and 80 litres of gluten-free oat drink.",
                    "requestedDate": SCHOOL_DELIVERY,
                    "items": [
                        {**line, "what": name, "requirements": [GLUTEN_FREE]}
                        for line, name in zip(SCHOOL_SHORTLIST, ("macaroni", "oat drink"), strict=True)
                    ],
                },
            },
        },
    })],
    request: Request,
    state: State,
    customer: AgentCustomer,
) -> Proposal:
    try:
        proposal = create_proposal(body, state, customer)
    except NoOrderError as exc:
        from .no_order_help import attach_no_candidate_details

        offer = state.gateway.get_registered_offer(body.offerId, body.offerVersion)
        attach_no_candidate_details(state, body, offer, exc)  # T108: same help as the buyer UI
        raise
    call: AgentCall | None = getattr(request.state, "agent_call", None)
    if call is not None:
        call.instruction_refused = ReasonCode.INSTRUCTION_IGNORED in proposal.reasons
    return proposal


def create_proposal(body: CreateProposalRequest, state: GatewayState, customer: str) -> Proposal:
    bind_customer(customer, body.customerId)
    offer = state.gateway.get_registered_offer(body.offerId, body.offerVersion)
    try:
        proposal = state.gateway.create_proposal(body, now=state.now_for(offer.mode))
    except ProposalRequestMismatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state.remember(proposal.proposalId)
    return proposal


@router.get("/proposals/{proposalId}", response_model=Proposal, operation_id="getProposal",
            summary="The current state of a proposal (the owning buyer's agent key; 404 for another buyer)",
            description="Returns the full Proposal with facts. " + COMPACT_HINT)
def get_proposal_route(proposalId: str, request: Request, state: ApprovalState) -> Proposal:
    proposal_read_scope(request, state, proposalId)
    return get_proposal(proposalId, state)


def get_proposal(proposalId: str, state: GatewayState) -> Proposal:
    """In-process read for the agent run, which already acts for one authenticated buyer."""
    current = state.gateway.get_proposal(proposalId)
    return state.gateway.get_proposal(proposalId, now=state.now_for(current.mode))


AuditScopeParam = Annotated[
    Literal["session", "all"],
    Query(
        description=(
            "`session` (default) reads only the caller's current demo session. "
            "`all` aggregates read-only across active browser and API sessions for that customer "
            "(Valio oversight view); each session's SHA-256 hash chain is verified independently."
        )
    ),
]


@router.get("/audit", response_model=AuditTrail, operation_id="getAuditTrail",
            summary="Decision log for one proposal or one customer (session or cross-session)")
def get_audit_trail(
    request: Request,
    state: State,
    proposalId: str | None = None,
    customerId: str | None = None,
    scope: AuditScopeParam = "session",
) -> AuditTrail:
    all_sessions = scope == "all"
    audit_scope(request, state, proposalId, customerId, all_sessions=all_sessions)
    if all_sessions:
        if proposalId is not None:
            owner = find_proposal_state(state, proposalId, all_sessions=True)
            return owner.gateway.get_audit_trail(proposal_id=proposalId)
        logs = [(sid, s.gateway.log) for sid, s in iter_audit_sessions(state)]
        return aggregate_audit_trails(logs, customer_id=customerId)
    if proposalId is None and customerId is None:
        return AuditTrail(entries=list(state.gateway.log.entries()))
    return state.gateway.get_audit_trail(proposal_id=proposalId, customer_id=customerId)


@router.get("/audit/verify", response_model=AuditChainVerification, operation_id="verifyAuditTrail",
            summary="Verify the SHA-256 hash chain over the decision log")
def verify_audit_trail(
    request: Request,
    state: State,
    proposalId: str | None = None,
    customerId: str | None = None,
    scope: AuditScopeParam = "session",
) -> AuditChainVerification:
    all_sessions = scope == "all"
    audit_scope(request, state, proposalId, customerId, all_sessions=all_sessions)
    if all_sessions:
        if proposalId is not None:
            owner = find_proposal_state(state, proposalId, all_sessions=True)
            return owner.gateway.log.verify(proposal_id=proposalId)
        logs = [(sid, s.gateway.log) for sid, s in iter_audit_sessions(state)]
        return aggregate_audit_verification(logs, customer_id=customerId)
    return state.gateway.log.verify(proposal_id=proposalId, customer_id=customerId)


@router.get("/proposals", response_model=list[Proposal], operation_id="listCustomerProposals",
            summary="This agent key's customer proposals, newest first; no approval capabilities")
def list_customer_proposals(state: State, customer: AgentCustomer) -> list[Proposal]:
    return [p for pid in reversed(state.proposal_ids)
            if (p := state.gateway.get_proposal(pid)).customerId == customer]


@router.get("/mandates", response_model=list[Mandate], operation_id="listMandates",
            summary="The demo buyers' mandates (read-only)")
def list_mandates(state: State) -> list[Mandate]:
    book = state.gateway.mandates
    return [book.get(customer) for customer in book.customers()]


@router.get("/mandates/{customerId}", response_model=Mandate, operation_id="getMandate",
            summary="One buyer's current mandate (read-only; agents can never change it)")
def get_mandate(customerId: str, state: State) -> Mandate:
    return state.gateway.mandates.get(customerId)
