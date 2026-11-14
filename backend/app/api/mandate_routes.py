"""Buyer-admin mandate controls (D5): a human changes a limit or the allergen mode, and open proposals are
re-decided live by the deterministic policy, with no model call.

- Only the buyer admin channel can change a mandate; the agent channel only reads it (`getMandate`).
- Every change bumps `Mandate.version` and is audited as "Policy changed by buyer admin: …".
- Each open proposal of that customer (waiting for approval, or approved but not yet confirmed) was decided
  under the old version, so it is SUPERSEDED and a new offer + proposal version is decided from scratch
  under the new mandate (P1.3: any approval of the old version is void). A new AUTO version is re-checked
  live and confirmed (simulated) right away.
"""

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from app.contracts import AuditEntry, Mandate, Money, Proposal
from app.contracts.common import Contract
from app.gateway.offers import shortlists
from app.gateway.proposals import _derive_offer_request

from .errors import ERROR_RESPONSES
from .state import GatewayState, get_state

router = APIRouter(prefix="/v0.1/admin", tags=["Buyer admin"], responses=ERROR_RESPONSES)
State = Annotated[GatewayState, Depends(get_state)]

BUYER_ADMIN_ACTOR = "human:Buyer admin (demo)"
OPEN_STATES = frozenset({"PENDING_APPROVAL", "APPROVED", "AUTO_APPROVED", "PROPOSED"})


class MandateChangeRequest(Contract):
    """The buyer-admin toggles. Omitted fields stay as they are."""

    autoApproveLimitEur: Money | None = None
    hardLimitEur: Money | None = None
    allergenMode: Literal["STANDARD", "STRICT"] | None = None
    autoAcceptValioSubstitutes: bool | None = None
    autoAcceptSupplierStock: bool | None = None
    minReliability: Decimal | None = Field(default=None, ge=0, le=1)


class MandateChangeResult(Contract):
    mandate: Mandate
    auditEntry: AuditEntry
    superseded: list[str]
    redecided: list[Proposal]


def _redecide(state: GatewayState, proposal: Proposal) -> Proposal:
    """Supersede `proposal` (decided under an older mandate) and decide a new version from scratch."""
    gateway = state.gateway
    stored = gateway._offers.get((proposal.offerId, proposal.offerVersion))
    offer_request = (stored.offer_request if stored and stored.offer_request else
                     _derive_offer_request(gateway._offer_from_proposal(proposal), proposal.request))
    snapshot = gateway.provider.lookup(
        shortlists(offer_request), offer_request.requestedDate, offer_request.customerId,
        products=gateway.products, force_refresh=True,
    )
    now = state.now_for(proposal.mode) or proposal.createdAt
    mandate = gateway.mandates.get(proposal.customerId)
    superseded = gateway._supersede_with_new_version(
        proposal, offer_req=offer_request, snapshot=snapshot, current_mandate=mandate, check_now=now,
        diffs=[f"mandateVersion v{proposal.mandateVersion} → v{mandate.version} (changed by buyer admin)"],
        refreshed_facts=[],
    )
    new = gateway.get_proposal(superseded.supersededBy)
    state.remember(new.proposalId)
    if new.state == "AUTO_APPROVED":
        new = gateway.recheck_and_confirm(new.proposalId, now=state.now_for(new.mode))
    return new


@router.patch("/mandates/{customerId}", response_model=MandateChangeResult, operation_id="changeMandate",
              summary="Buyer admin: change the mandate; open proposals are re-decided live")
def change_mandate(customerId: str, body: MandateChangeRequest, state: State) -> MandateChangeResult:
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    gateway = state.gateway
    gateway.mandates.get(customerId)  # 404 for an unknown customer
    try:
        updated, entry = gateway.mandates.change(customerId, changes, actor=BUYER_ADMIN_ACTOR, now=state.clock())
    except ValueError as exc:  # no change, or hard limit below the auto-approve limit
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    gateway.log.append(entry)

    superseded: list[str] = []
    redecided: list[Proposal] = []
    for proposal_id in list(state.proposal_ids):
        proposal = gateway.get_proposal(proposal_id)
        if (proposal.customerId != customerId or proposal.state not in OPEN_STATES
                or proposal.mandateVersion == updated.version):
            continue
        redecided.append(_redecide(state, proposal))
        superseded.append(proposal_id)
    return MandateChangeResult(mandate=updated, auditEntry=entry, superseded=superseded, redecided=redecided)
