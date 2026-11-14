"""Agent channel /v0.1 (patch P1.5): any agent may call these; none of them can approve or confirm.

Agent responses never carry an approval token, URL or QR payload (P3, AC15): those exist only on the
approver channel. The server alone decides AUTO / ASK / BLOCK and confirms after a live re-check.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.catalog.search import SearchProductsRequest, SearchProductsResult, search_products
from app.contracts import AuditTrail, CreateProposalRequest, GetOfferRequest, Mandate, Offer, Proposal
from app.gateway.log import AuditChainVerification, canonical_customer_id

from .errors import ERROR_RESPONSES
from .state import GatewayState, get_state

router = APIRouter(prefix="/v0.1", tags=["Agent channel"], responses=ERROR_RESPONSES)
State = Annotated[GatewayState, Depends(get_state)]


def _resolve_customer_id(state: GatewayState, customer_id: str) -> str:
    known = state.gateway.mandates.customers()
    if customer_id in known:
        return customer_id
    canon = canonical_customer_id(customer_id)
    if canon in known:
        return canon
    return customer_id


@router.post("/products/search", response_model=SearchProductsResult, operation_id="searchProducts",
             summary="Static product lookup with hard requirements and an excluded list")
def search(body: SearchProductsRequest, state: State,
           customerId: Annotated[str | None, Query(description="Apply this customer's mandate (allergen mode, "
                                                    "forbidden allergens)")] = None) -> SearchProductsResult:
    resolved = _resolve_customer_id(state, customerId) if customerId else None
    mandate = state.gateway.mandates.get(resolved) if resolved else None
    return search_products(body, mandate=mandate, products=state.catalog)


@router.post("/offers", response_model=Offer, operation_id="getOffer",
             summary="Live price + availability for the shortlist → an offer at fixed prices")
def get_offer(body: GetOfferRequest, state: State) -> Offer:
    resolved = _resolve_customer_id(state, body.customerId)
    if resolved != body.customerId:
        body = body.model_copy(update={"customerId": resolved})
    return state.gateway.get_offer(body).offer


@router.post("/proposals", response_model=Proposal, status_code=201, operation_id="createProposal",
             summary="Bind the request to an offer version; the gateway decides AUTO / ASK / BLOCK")
def create_proposal(body: CreateProposalRequest, state: State) -> Proposal:
    resolved = _resolve_customer_id(state, body.customerId)
    if resolved != body.customerId:
        body = body.model_copy(update={"customerId": resolved})
    offer = state.gateway.get_registered_offer(body.offerId, body.offerVersion)
    proposal = state.gateway.create_proposal(body, now=state.now_for(offer.mode))
    state.remember(proposal.proposalId)
    return proposal


@router.get("/proposals/{proposalId}", response_model=Proposal, operation_id="getProposal",
            summary="The current state of a proposal")
def get_proposal(proposalId: str, state: State) -> Proposal:
    current = state.gateway.get_proposal(proposalId)
    return state.gateway.get_proposal(proposalId, now=state.now_for(current.mode))


@router.get("/audit", response_model=AuditTrail, operation_id="getAuditTrail",
            summary="Decision log for exactly one proposal or one customer")
def get_audit_trail(state: State, proposalId: str | None = None, customerId: str | None = None) -> AuditTrail:
    if (proposalId is None) == (customerId is None):
        raise HTTPException(status_code=400, detail="Pass exactly one of proposalId or customerId.")
    resolved = _resolve_customer_id(state, customerId) if customerId else None
    return state.gateway.get_audit_trail(proposal_id=proposalId, customer_id=resolved)


@router.get("/audit/verify", response_model=AuditChainVerification, operation_id="verifyAuditTrail",
            summary="Verify the SHA-256 hash chain over the decision log")
def verify_audit_trail(
    state: State,
    proposalId: str | None = None,
    customerId: str | None = None,
) -> AuditChainVerification:
    if proposalId is not None and customerId is not None:
        raise HTTPException(status_code=400, detail="Pass at most one of proposalId or customerId.")
    resolved = _resolve_customer_id(state, customerId) if customerId else None
    return state.gateway.log.verify(proposal_id=proposalId, customer_id=resolved)


@router.get("/mandates", response_model=list[Mandate], operation_id="listMandates",
            summary="The demo buyers' mandates (read-only)")
def list_mandates(state: State) -> list[Mandate]:
    book = state.gateway.mandates
    return [book.get(customer) for customer in book.customers()]


@router.get("/mandates/{customerId}", response_model=Mandate, operation_id="getMandate",
            summary="One buyer's current mandate (read-only; agents can never change it)")
def get_mandate(customerId: str, state: State) -> Mandate:
    return state.gateway.mandates.get(_resolve_customer_id(state, customerId))
