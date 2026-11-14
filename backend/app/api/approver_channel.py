"""Approver channel /v0.1/approver (patch P3): the named human signs off one exact proposal version.

`decideApproval` exists only here and is never an agent or MCP tool. The approval capability (a single-use
demo token) is issued by the view endpoint for a PENDING_APPROVAL proposal and is bound to
proposalId + offerVersion + offerHash. Demo links are not production authentication (SSO/OIDC is).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.contracts import ApprovalAction, ApproverView, Proposal
from app.contracts.common import Contract
from app.gateway.approvals import (
    InvalidApprovalTokenError,
    open_sandbox_approval_token,
    sandbox_approval_token,
)

from .errors import ERROR_RESPONSES
from .state import GatewayState, get_state, sandbox_registry, sandbox_signing_key

router = APIRouter(prefix="/v0.1/approver", tags=["Approver channel"], responses=ERROR_RESPONSES)
State = Annotated[GatewayState, Depends(get_state)]


async def approval_state(request: Request, state: State) -> GatewayState:
    token = request.headers.get("x-approval-token")
    if request.method == "POST":
        try:
            body = await request.json()
            token = body.get("approvalToken") if isinstance(body, dict) else None
        except ValueError:
            token = None
    if not isinstance(token, str):
        return state
    binding = open_sandbox_approval_token(token, signing_key=sandbox_signing_key())
    if binding is None:
        return state
    sandbox_id, inner = binding
    target = state if state.sandbox_id == sandbox_id else sandbox_registry().get(sandbox_id, create=False)
    grant = target.gateway.approvals.get_grant(inner)
    if grant is None or grant.proposal_id != request.path_params.get("proposalId"):
        raise InvalidApprovalTokenError("Unknown or invalid approval token")
    request.state.approval_token = inner
    return target


ApprovalState = Annotated[GatewayState, Depends(approval_state)]


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
def get_approver_view(proposalId: str, state: ApprovalState) -> ApproverView:
    current = state.gateway.get_proposal(proposalId)
    state.gateway.get_proposal(proposalId, now=state.now_for(current.mode))  # expire first if overdue
    view = state.gateway.get_approver_view(proposalId)
    token = sandbox_approval_token(view.approvalToken, state.sandbox_id, signing_key=sandbox_signing_key())
    return view.model_copy(update={"approvalToken": token})


@router.post("/proposals/{proposalId}/decision", response_model=Proposal, operation_id="decideApproval",
             summary="APPROVE or REJECT this exact version; approval triggers a live re-check")
def decide_approval(proposalId: str, body: DecideApprovalRequest, request: Request, state: ApprovalState) -> Proposal:
    current = state.gateway.get_proposal(proposalId)
    return state.gateway.decide_approval(
        proposalId, body.offerVersion, getattr(request.state, "approval_token", body.approvalToken), body.action, body.comment,
        now=state.now_for(current.mode),
    )
