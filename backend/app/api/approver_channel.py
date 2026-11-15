"""Approver channel /v0.1/approver (patch P3): the named human signs off one exact proposal version.

`decideApproval` exists only here and is never an agent or MCP tool. The approval capability (a single-use
demo token) is issued by the view endpoint for a PENDING_APPROVAL proposal and is bound to
proposalId + offerVersion + offerHash. Demo links are not production authentication (SSO/OIDC is).
"""

from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from app.contracts import ApprovalAction, ApproverView, Proposal
from app.contracts.common import Contract
from app.gateway.approvals import (
    InvalidApprovalTokenError,
    approver_actor,
    open_sandbox_approval_token,
    sandbox_approval_token,
)

from .auth import approver_role, require_admin
from .errors import ERROR_RESPONSES
from .state import GatewayState, get_state, sandbox_registry, sandbox_signing_key

router = APIRouter(prefix="/v0.1/approver", tags=["Approver channel"], responses=ERROR_RESPONSES)
State = Annotated[GatewayState, Depends(get_state)]


async def approval_state(request: Request, state: State) -> GatewayState:
    token = request.headers.get("x-approval-token")
    if request.method == "POST" and not isinstance(token, str):
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
    path_proposal_id = request.path_params.get("proposalId")
    if grant is None or (path_proposal_id is not None and grant.proposal_id != path_proposal_id):
        raise InvalidApprovalTokenError("Unknown or invalid approval token")
    request.state.approval_token = inner
    request.state.approval_grant = grant
    return target


ApprovalState = Annotated[GatewayState, Depends(approval_state)]


def _resolve_rp_id_and_origin(request: Request, explicit_rp_id: str | None = None) -> tuple[str, str | None]:
    origin = request.headers.get("origin")
    if explicit_rp_id and explicit_rp_id.strip():
        return explicit_rp_id.strip(), origin
    if origin:
        parsed = urlparse(origin)
        if parsed.hostname and parsed.hostname != "testserver":
            return parsed.hostname, origin
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
    hostname = host.split(",")[0].strip().split(":")[0]
    if not hostname or hostname == "testserver":
        hostname = "localhost"
    return hostname, origin


def _resolve_passkey_role(
    request: Request,
    state: GatewayState,
    explicit_role: str | None = None,
    proposal: Proposal | None = None,
) -> str:
    grant = getattr(request.state, "approval_grant", None)
    if grant is not None and proposal is None:
        proposal = state.gateway.get_proposal(grant.proposal_id)
    role = approver_role(request, state, proposal)
    if explicit_role and explicit_role.strip() and explicit_role.strip() != role:
        raise HTTPException(403, f"Signed in as {role}, not {explicit_role.strip()}.")
    return role


class DecideApprovalRequest(Contract):
    offerVersion: int = Field(ge=1)
    approvalToken: str = Field(min_length=1, max_length=512)
    action: ApprovalAction
    comment: str | None = Field(default=None, max_length=500)
    passkeyAssertion: dict[str, Any] | None = None


class PasskeyStatusResponse(Contract):
    role: str
    enrolled: bool
    credentialCount: int


class PasskeyRegisterOptionsRequest(Contract):
    role: str | None = None
    rpId: str | None = None
    approvalToken: str | None = None


class PasskeyRegisterOptionsResponse(Contract):
    role: str
    rpId: str
    options: dict[str, Any]


class PasskeyRegisterVerifyRequest(Contract):
    role: str | None = None
    rpId: str | None = None
    credential: dict[str, Any]
    approvalToken: str | None = None


class PasskeyAuthOptionsResponse(Contract):
    proposalId: str
    role: str
    enrolled: bool
    options: dict[str, Any] | None = None


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


@router.get("/passkey/status", response_model=PasskeyStatusResponse, operation_id="getPasskeyStatus",
            include_in_schema=False)
def get_passkey_status(request: Request, state: ApprovalState, role: str | None = None) -> PasskeyStatusResponse:
    resolved_role = _resolve_passkey_role(request, state, role)
    creds = state.gateway.approvals.passkeys_for_role(resolved_role)
    return PasskeyStatusResponse(
        role=resolved_role,
        enrolled=len(creds) > 0,
        credentialCount=len(creds),
    )


@router.post("/passkey/register/options", response_model=PasskeyRegisterOptionsResponse,
             operation_id="beginPasskeyRegistration", include_in_schema=False)
def begin_passkey_registration(
    body: PasskeyRegisterOptionsRequest,
    request: Request,
    state: ApprovalState,
) -> PasskeyRegisterOptionsResponse:
    resolved_role = _resolve_passkey_role(request, state, body.role)
    rp_id, _origin = _resolve_rp_id_and_origin(request, body.rpId)
    options = state.gateway.approvals.begin_passkey_registration(resolved_role, rp_id=rp_id)
    return PasskeyRegisterOptionsResponse(role=resolved_role, rpId=rp_id, options=options)


@router.post("/passkey/register/verify", response_model=PasskeyStatusResponse,
             operation_id="completePasskeyRegistration", include_in_schema=False)
def complete_passkey_registration(
    body: PasskeyRegisterVerifyRequest,
    request: Request,
    state: ApprovalState,
) -> PasskeyStatusResponse:
    resolved_role = _resolve_passkey_role(request, state, body.role)
    rp_id, origin = _resolve_rp_id_and_origin(request, body.rpId)
    state.gateway.approvals.complete_passkey_registration(
        resolved_role,
        body.credential,
        expected_origin=origin,
        expected_rp_id=rp_id,
    )
    creds = state.gateway.approvals.passkeys_for_role(resolved_role)
    return PasskeyStatusResponse(
        role=resolved_role,
        enrolled=len(creds) > 0,
        credentialCount=len(creds),
    )


@router.get("/proposals/{proposalId}/passkey-options", response_model=PasskeyAuthOptionsResponse,
            operation_id="getPasskeyAuthOptions", include_in_schema=False)
def get_passkey_auth_options(
    proposalId: str,
    request: Request,
    state: ApprovalState,
    rpId: str | None = None,
) -> PasskeyAuthOptionsResponse:
    current = state.gateway.get_proposal(proposalId)
    role = _resolve_passkey_role(request, state, proposal=current)
    rp_id, _origin = _resolve_rp_id_and_origin(request, rpId)
    options = state.gateway.approvals.authentication_options_for_proposal(
        current,
        role=role,
        rp_id=rp_id,
    )
    return PasskeyAuthOptionsResponse(
        proposalId=proposalId,
        role=role,
        enrolled=options is not None,
        options=options,
    )


@router.post("/proposals/{proposalId}/decision", response_model=Proposal, operation_id="decideApproval",
             summary="APPROVE or REJECT this exact version; approval triggers a live re-check")
def decide_approval(proposalId: str, body: DecideApprovalRequest, request: Request, state: ApprovalState) -> Proposal:
    current = state.gateway.get_proposal(proposalId)
    role = approver_role(request, state, current)
    rp_id, origin = _resolve_rp_id_and_origin(request)
    used_passkey = body.action == "APPROVE" and (
        body.passkeyAssertion is not None or state.gateway.approvals.has_passkey(role)
    )
    actor = approver_actor(role, passkey=used_passkey)
    with state.gateway.approvals.with_assertion(
        body.passkeyAssertion,
        expected_origin=origin,
        expected_rp_id=rp_id,
    ):
        return state.gateway.decide_approval(
            proposalId,
            body.offerVersion,
            getattr(request.state, "approval_token", body.approvalToken),
            body.action,
            body.comment,
            now=state.now_for(current.mode),
            actor=actor,
        )
