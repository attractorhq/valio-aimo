"""Explicit, public demo credentials. These illustrate scope, not production identity."""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.policy import DEMO_MANDATES

DEMO_AGENT_KEYS = {f"demo-agent-{m.customerId}": m.customerId for m in DEMO_MANDATES}


AGENT_KEY_HINT = "'Authorization: Bearer demo-agent-<customerId>'"
ADMIN_KEY_HINT = "'X-Demo-Admin-Key: demo-admin'"
AUDIT_ADMIN_HINT = f" A buyer admin can read any buyer with {ADMIN_KEY_HINT}."


def require_admin(request: Request, message: str | None = None) -> None:
    if not hmac.compare_digest(request.headers.get("x-demo-admin-key", "").encode(), b"demo-admin"):
        raise HTTPException(401, message or f"Buyer-admin changes need the public demo header {ADMIN_KEY_HINT}.")


def agent_customer(request: Request, extra_hint: str = "") -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    customer = DEMO_AGENT_KEYS.get(token) if scheme.lower() == "bearer" else None
    if customer is None:
        raise HTTPException(401, f"Send the buyer's public agent key: {AGENT_KEY_HINT} "
                                 f"(one of: {', '.join(sorted(DEMO_AGENT_KEYS))}).{extra_hint} Echo the "
                                 "X-Demo-Sandbox response header on later calls to stay in your session. See /docs.",
                            headers={"WWW-Authenticate": "Bearer"})
    return customer


AgentCustomer = Annotated[str, Depends(agent_customer)]


def bind_customer(authenticated: str, supplied: str | None, hint: str = "") -> str:
    if supplied is not None and supplied != authenticated:
        raise HTTPException(403, f"This agent key belongs to buyer {authenticated} and works only for that buyer. "
                                 f"Send the owning buyer's agent key {AGENT_KEY_HINT}.{hint}")
    return authenticated


def audit_scope(
    request: Request,
    state,
    proposal_id: str | None,
    customer_id: str | None,
    *,
    all_sessions: bool = False,
    agent_defaults_to_own: bool = False,
) -> str | None:
    """Resolve proposal ownership before reading any trail, export, or chain.

    Returns the customer the read is limited to, or None for the admin's global read.
    With `agent_defaults_to_own` (audit verify, T110), an agent key without proposalId/customerId
    is scoped to its own buyer instead of being refused.
    """
    from .state import find_proposal_state

    if proposal_id is not None and customer_id is not None:
        raise HTTPException(400, "Pass at most one of proposalId or customerId.")
    if request.headers.get("x-demo-admin-key") is not None:
        require_admin(request, f"The admin header must be exactly {ADMIN_KEY_HINT}. "
                               f"A buyer can instead send its agent key {AGENT_KEY_HINT} for its own entries.")
        if proposal_id is not None and all_sessions:
            find_proposal_state(state, proposal_id, all_sessions=True)
        return customer_id
    is_global = proposal_id is None and customer_id is None
    if is_global and not agent_defaults_to_own:
        if request.headers.get("authorization") is None:
            raise HTTPException(401, f"The global audit needs the admin header {ADMIN_KEY_HINT}. "
                                     f"A buyer sends its agent key {AGENT_KEY_HINT} with customerId or proposalId.",
                                headers={"WWW-Authenticate": "Bearer"})
        agent_customer(request)
        raise HTTPException(401, f"The global audit needs the admin header {ADMIN_KEY_HINT}. "
                                 "An agent key reads only its own buyer: add customerId or proposalId.")
    authenticated = agent_customer(
        request, f" Or send {ADMIN_KEY_HINT} for the full audit." if is_global else "")
    if proposal_id is not None:
        owner_state = find_proposal_state(state, proposal_id, all_sessions=all_sessions)
        proposal = owner_state.gateway.get_proposal(proposal_id)
        bind_customer(authenticated, proposal.customerId, AUDIT_ADMIN_HINT)
    else:
        bind_customer(authenticated, customer_id, AUDIT_ADMIN_HINT)
    return authenticated


def proposal_read_scope(request: Request, state, proposal_id: str):
    """T100: who may read one proposal. Mirrors the audit scope (T55).

    - a browser's private sandbox: everything in it was created by that browser, so no key is needed;
    - the demo admin key, or the approver role / single-use approval link for that proposal;
    - otherwise the owning buyer's agent key: none is 401, another buyer's key is 404 (no existence leak).
    """
    from app.gateway.proposals import ProposalNotFoundError

    proposal = state.gateway.get_proposal(proposal_id)
    if request.headers.get("x-demo-admin-key") is not None:
        require_admin(request)
        return proposal
    if request.headers.get("authorization") is None:
        if state.sandbox_id != "shared":
            return proposal
        if getattr(request.state, "approval_token", None) or request.headers.get("x-demo-approver-role"):
            approver_role(request, state, proposal)
            return proposal
    if agent_customer(request) != proposal.customerId:
        raise ProposalNotFoundError(f"Unknown proposal {proposal_id}")
    return proposal


def approver_role(request: Request, state, proposal=None) -> str:
    role = request.headers.get("x-demo-approver-role")
    # A signed phone link carries only the named role to which the grant was issued.
    if role is None and (token := getattr(request.state, "approval_token", None)):
        grant = state.gateway.approvals.get_grant(token)
        if grant is not None:
            role = grant.approver.role
    if not role:
        raise HTTPException(401, "Choose your approver role first.")
    customers = [c for c in state.gateway.mandates.customers()
                 if state.gateway.mandates.get(c).approver.role == role]
    if not customers or (proposal is not None and (
        proposal.customerId not in customers or (proposal.approverRole is not None and proposal.approverRole != role)
    )):
        raise HTTPException(403, "This proposal requires another approver role.")
    return role
