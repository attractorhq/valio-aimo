"""Approver channel /v0.1/approver (patch P3): the named human signs off one exact proposal version.

`decideApproval` exists only here and is never an agent or MCP tool. The approval capability (a single-use
demo token) is issued by the view endpoint for a PENDING_APPROVAL proposal and is bound to
proposalId + offerVersion + offerHash. Demo links are not production authentication (SSO/OIDC is).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field

from app.contracts import ApprovalAction, ApproverView, Proposal
from app.contracts.common import Contract

from .errors import ERROR_RESPONSES
from .state import GatewayState, get_state

router = APIRouter(prefix="/v0.1/approver", tags=["Approver channel"], responses=ERROR_RESPONSES)
State = Annotated[GatewayState, Depends(get_state)]


class DecideApprovalRequest(Contract):
    offerVersion: int = Field(ge=1)
    approvalToken: str = Field(min_length=1, max_length=512)
    action: ApprovalAction
    comment: str | None = Field(default=None, max_length=500)


@router.get("/proposals", response_model=list[Proposal], operation_id="listApproverProposals",
            summary="Proposals for the approver panel, newest first (no capabilities included)")
def list_proposals(state: State, customerId: str | None = None, pendingOnly: bool = False) -> list[Proposal]:
    proposals = []
    for proposal_id in reversed(state.proposal_ids):
        proposal = state.gateway.get_proposal(proposal_id)
        if customerId and proposal.customerId != customerId:
            continue
        if pendingOnly and proposal.state != "PENDING_APPROVAL":
            continue
        proposals.append(proposal)
    return proposals


@router.get("/proposals/{proposalId}", response_model=ApproverView, operation_id="getApproverView",
            summary="The exact version to approve, with a single-use demo approval token")
def get_approver_view(proposalId: str, state: State) -> ApproverView:
    current = state.gateway.get_proposal(proposalId)
    state.gateway.get_proposal(proposalId, now=state.now_for(current.mode))  # expire first if overdue
    return state.gateway.get_approver_view(proposalId)


@router.post("/proposals/{proposalId}/decision", response_model=Proposal, operation_id="decideApproval",
             summary="APPROVE or REJECT this exact version; approval triggers a live re-check")
def decide_approval(proposalId: str, body: DecideApprovalRequest, state: State) -> Proposal:
    current = state.gateway.get_proposal(proposalId)
    return state.gateway.decide_approval(
        proposalId, body.offerVersion, body.approvalToken, body.action, body.comment,
        now=state.now_for(current.mode),
    )
