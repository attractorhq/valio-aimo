"""Explicit, public demo credentials. These illustrate scope, not production identity."""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.policy import DEMO_MANDATES

DEMO_AGENT_KEYS = {f"demo-agent-{m.customerId}": m.customerId for m in DEMO_MANDATES}


def require_admin(request: Request) -> None:
    if not hmac.compare_digest(request.headers.get("x-demo-admin-key", "").encode(), b"demo-admin"):
        raise HTTPException(401, "Buyer-admin changes need the public demo header 'X-Demo-Admin-Key: demo-admin'.")


def agent_customer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    customer = DEMO_AGENT_KEYS.get(token) if scheme.lower() == "bearer" else None
    if customer is None:
        raise HTTPException(401, "Send the buyer's public agent key: 'Authorization: Bearer demo-agent-<customerId>' "
                                 f"(one of: {', '.join(sorted(DEMO_AGENT_KEYS))}). See /docs.",
                            headers={"WWW-Authenticate": "Bearer"})
    return customer


AgentCustomer = Annotated[str, Depends(agent_customer)]


def bind_customer(authenticated: str, supplied: str | None) -> str:
    if supplied is not None and supplied != authenticated:
        raise HTTPException(403, "This credential belongs to another buyer.")
    return authenticated


def audit_scope(request: Request, state, proposal_id: str | None, customer_id: str | None) -> str | None:
    """Resolve proposal ownership before reading any trail, export, or chain."""
    if proposal_id is not None and customer_id is not None:
        raise HTTPException(400, "Pass at most one of proposalId or customerId.")
    if request.headers.get("x-demo-admin-key") is not None:
        require_admin(request)
        return customer_id
    authenticated = agent_customer(request)
    if proposal_id is not None:
        proposal = state.gateway.get_proposal(proposal_id)
        bind_customer(authenticated, proposal.customerId)
    else:
        if customer_id is None:
            raise HTTPException(401, "The global audit requires the demo admin key.")
        bind_customer(authenticated, customer_id)
    return authenticated


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
