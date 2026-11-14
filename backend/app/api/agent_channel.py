"""Agent channel /v0.1 (patch P1.5): any agent may call these; none of them can approve or confirm.

Agent responses never carry an approval token, URL or QR payload (P3, AC15): those exist only on the
approver channel. The server alone decides AUTO / ASK / BLOCK and confirms after a live re-check.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.catalog.search import SearchProductsRequest, SearchProductsResult, search_products
from app.contracts import AuditTrail, CreateProposalRequest, GetOfferRequest, Mandate, Offer, Proposal
from app.gateway.log import AuditChainVerification

from .errors import ERROR_RESPONSES
from .state import GatewayState, get_state

router = APIRouter(prefix="/v0.1", tags=["Agent channel"], responses=ERROR_RESPONSES)
State = Annotated[GatewayState, Depends(get_state)]

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
    customerId: Annotated[str | None, Query(description="Apply this customer's mandate (allergen mode, "
                                                       "forbidden allergens)")] = None,
) -> SearchProductsResult:
    mandate = state.gateway.mandates.get(customerId) if customerId else None
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
) -> Offer:
    return state.gateway.get_offer(body).offer


@router.post("/proposals", response_model=Proposal, status_code=201, operation_id="createProposal",
             summary="Bind the request to an offer version; the gateway decides AUTO / ASK / BLOCK")
def create_proposal(
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
    state: State,
) -> Proposal:
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
    return state.gateway.get_audit_trail(proposal_id=proposalId, customer_id=customerId)


@router.get("/audit/verify", response_model=AuditChainVerification, operation_id="verifyAuditTrail",
            summary="Verify the SHA-256 hash chain over the decision log")
def verify_audit_trail(
    state: State,
    proposalId: str | None = None,
    customerId: str | None = None,
) -> AuditChainVerification:
    if proposalId is not None and customerId is not None:
        raise HTTPException(status_code=400, detail="Pass at most one of proposalId or customerId.")
    return state.gateway.log.verify(proposal_id=proposalId, customer_id=customerId)


@router.get("/mandates", response_model=list[Mandate], operation_id="listMandates",
            summary="The demo buyers' mandates (read-only)")
def list_mandates(state: State) -> list[Mandate]:
    book = state.gateway.mandates
    return [book.get(customer) for customer in book.customers()]


@router.get("/mandates/{customerId}", response_model=Mandate, operation_id="getMandate",
            summary="One buyer's current mandate (read-only; agents can never change it)")
def get_mandate(customerId: str, state: State) -> Mandate:
    return state.gateway.mandates.get(customerId)
