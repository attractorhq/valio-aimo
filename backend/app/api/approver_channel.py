"""Approver channel /v0.1/approver (patch P3): the named human signs off one exact proposal version.

`decideApproval` exists only here and is never an agent or MCP tool. The approval capability (a single-use
demo token) is issued by the view endpoint for a PENDING_APPROVAL proposal and is bound to
proposalId + offerVersion + offerHash. Demo links are not production authentication (SSO/OIDC is).
"""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from app.config import get_settings
from app.contracts import ApprovalAction, ApproverView, Proposal
from app.contracts.common import Contract
from app.gateway.approvals import (
    InvalidApprovalTokenError,
    PasskeyOrigin,
    PasskeyRole,
    open_sandbox_approval_token,
    sandbox_approval_token,
)

from .auth import approver_role, require_admin
from .errors import ERROR_RESPONSES
from .state import GatewayState, get_state, sandbox_registry, sandbox_signing_key, utc_now

router = APIRouter(prefix="/v0.1/approver", tags=["Approver channel"], responses=ERROR_RESPONSES)
State = Annotated[GatewayState, Depends(get_state)]
_DEPLOYED_ORIGIN = "https://valio-aimo.attractorfi.workers.dev"


def passkey_site(request: Request) -> PasskeyOrigin:
    origin = request.headers.get("origin", "")
    try:
        parsed = urlsplit(origin)
        _ = parsed.port  # Reject malformed authority before creating a challenge.
    except ValueError:
        raise HTTPException(403, "Passkeys require the approved Aimo Connect origin.") from None
    configured = {value.strip() for value in get_settings().frontend_origins.split(",")}
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname or parsed.username is not None or parsed.password is not None
        or origin != f"{parsed.scheme}://{parsed.netloc}"
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        or not (origin == _DEPLOYED_ORIGIN or origin in configured or loopback)
    ):
        raise HTTPException(403, "Passkeys require the approved Aimo Connect origin.")
    return PasskeyOrigin(origin, parsed.hostname)


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


class VersionedApprovalLink(Contract):
    offerVersion: int = Field(ge=1)
    approvalToken: str = Field(min_length=1, max_length=512)


class DecideApprovalRequest(VersionedApprovalLink):
    action: ApprovalAction
    comment: str | None = Field(default=None, max_length=500)
    passkeyAssertion: dict | None = None


class EnrolPasskeyRequest(Contract):
    credential: dict


def passkey_role(request: Request, state: GatewayState, customer_id: str) -> PasskeyRole:
    role = approver_role(request, state)
    if role != state.gateway.mandates.get(customer_id).approver.role:
        raise HTTPException(403, "This buyer requires another approver role.")
    return customer_id, role


@router.get("/passkeys/{customerId}", operation_id="getApproverPasskeyStatus",
            summary="Passkey status for the signed-in approver role")
def get_passkey_status(customerId: str, request: Request, state: State) -> dict[str, bool]:
    role = passkey_role(request, state, customerId)
    return {"enrolled": state.gateway.approvals.has_passkey(role)}


@router.post("/passkeys/{customerId}/options", operation_id="getApproverPasskeyRegistrationOptions",
             summary="Start passkey enrollment for the signed-in approver role")
def get_registration_options(customerId: str, request: Request, state: State) -> dict:
    role = passkey_role(request, state, customerId)
    return state.gateway.approvals.registration_options(role, passkey_site(request), now=utc_now())


@router.post("/passkeys/{customerId}", operation_id="registerApproverPasskey",
             summary="Register a verified passkey for the signed-in approver role")
def register_passkey(customerId: str, body: EnrolPasskeyRequest, request: Request, state: State) -> dict[str, bool]:
    role = passkey_role(request, state, customerId)
    state.gateway.approvals.register_passkey(role, passkey_site(request), body.credential, now=utc_now())
    return {"enrolled": True}


@router.get("/proposals", response_model=list[Proposal], operation_id="listApproverProposals",
            summary="Proposals for the approver panel, newest first (no capabilities included)")
def list_proposals(request: Request, state: State, customerId: str | None = None,
                   pendingOnly: bool = False) -> list[Proposal]:
    if request.headers.get("x-demo-admin-key") is not None:
        require_admin(request)
        role = None
    else:
        role = approver_role(request, state)
        if customerId is not None and state.gateway.mandates.get(customerId).approver.role != role:
            raise HTTPException(403, "This buyer requires another approver role.")
    proposals = []
    for proposal_id in reversed(state.proposal_ids):
        proposal = state.gateway.get_proposal(proposal_id)
        if role and state.gateway.mandates.get(proposal.customerId).approver.role != role:
            continue
        if customerId and proposal.customerId != customerId:
            continue
        if pendingOnly and proposal.state != "PENDING_APPROVAL":
            continue
        proposals.append(proposal)
    return proposals


@router.get("/proposals/{proposalId}", response_model=ApproverView, operation_id="getApproverView",
            summary="The exact version to approve, with a single-use demo approval token")
def get_approver_view(proposalId: str, request: Request, state: ApprovalState) -> ApproverView:
    current = state.gateway.get_proposal(proposalId)
    approver_role(request, state, current)
    state.gateway.get_proposal(proposalId, now=state.now_for(current.mode))  # expire first if overdue
    view = state.gateway.get_approver_view(proposalId)
    token = sandbox_approval_token(view.approvalToken, state.sandbox_id, signing_key=sandbox_signing_key())
    return view.model_copy(update={"approvalToken": token})


@router.post("/proposals/{proposalId}/decision", response_model=Proposal, operation_id="decideApproval",
             summary="APPROVE or REJECT this exact version; approval triggers a live re-check")
def decide_approval(proposalId: str, body: DecideApprovalRequest, request: Request, state: ApprovalState) -> Proposal:
    current = state.gateway.get_proposal(proposalId)
    approver_role(request, state, current)
    return state.gateway.decide_approval(
        proposalId, body.offerVersion, getattr(request.state, "approval_token", body.approvalToken), body.action, body.comment,
        now=state.now_for(current.mode), passkey_assertion=body.passkeyAssertion, passkey_now=utc_now(),
    )


@router.post("/proposals/{proposalId}/passkey/options", operation_id="getApproverPasskeyApprovalOptions",
             summary="Start passkey verification for this exact proposal")
def get_approval_options(proposalId: str, body: VersionedApprovalLink, request: Request,
                         state: ApprovalState) -> dict | None:
    proposal = state.gateway.get_proposal(proposalId)
    approver_role(request, state, proposal)
    return state.gateway.approvals.approval_options(
        proposal, body.offerVersion, getattr(request.state, "approval_token", body.approvalToken),
        passkey_site(request), now=state.now_for(proposal.mode) or proposal.createdAt, passkey_now=utc_now(),
    )
