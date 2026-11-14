"""Single-use approval tokens and approver-channel verification (patch P1.3, P2, P3, docs/API.md).

- An approval token is bound to `proposalId + offerVersion + offerHash` and expires with the offer.
- It is single-use: once consumed for `APPROVE` or `REJECT`, any reuse is refused.
- `BLOCK` proposals (and any proposal with a P2.3 non-approvable reason) can never be approved.
- When a re-check fails and supersedes a proposal, its approval is voided and never transfers to the
  new proposal version.
- `ApproverView` is the only model carrying `approvalToken`, `approvalUrl` or `qrPayload`, and belongs
  exclusively to the approver channel.
- Demo audit actor: `"human:Head Chef (link)"`.
"""

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import (
    REASON_REGISTRY,
    ApprovalAction,
    Approver,
    ApproverView,
    Proposal,
    ReasonCode,
)
from app.db.models import ApprovalTokenRow

DEMO_APPROVER_ACTOR = "human:Head Chef (link)"
PERMISSION_NOTICE = "Single-use approval link"
ALLOWED_ACTIONS: frozenset[str] = frozenset({"APPROVE", "REJECT"})


class ApprovalError(ValueError):
    """Base error for approval verification and channel rules."""

    def __init__(self, message: str, *, proposal: Proposal | None = None):
        self.proposal = proposal
        super().__init__(message)


class ApprovalNotAllowedError(ApprovalError):
    """Raised when a proposal is BLOCK, AUTO, not pending, or carries non-approvable reasons."""


class InvalidApprovalActionError(ApprovalError):
    """Raised when an approval action is not APPROVE or REJECT."""


class InvalidApprovalTokenError(ApprovalError):
    """Raised when an approval token is unknown or malformed."""


class TokenBindingMismatchError(ApprovalError):
    """Raised when an approval token does not match proposalId + offerVersion + offerHash."""


class TokenAlreadyUsedError(ApprovalError):
    """Raised when a single-use approval token is reused."""


class ApprovalExpiredError(ApprovalError):
    """Raised when the offer and its approval token have expired."""


class ApprovalSupersededError(ApprovalError):
    """Raised when the proposal version was superseded and its approval is void."""


def sandbox_approval_token(token: str, sandbox_id: str, *, signing_key: bytes) -> str:
    """An approver-only capability carrying an authenticated sandbox for cross-device routing."""
    encoded = urlsafe_b64encode(sandbox_id.encode()).decode().rstrip("=")
    payload = f"{token}.sbx1.{encoded}"
    mac = hmac.new(signing_key, b"voa-approval-sandbox:" + payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def open_sandbox_approval_token(token: str, *, signing_key: bytes) -> tuple[str, str] | None:
    """Verify the sandbox before selecting any state; legacy local tokens cannot route across sandboxes."""
    if ".sbx1." not in token:
        return None
    try:
        inner, version, encoded, supplied_mac = token.split(".")
        payload = f"{inner}.{version}.{encoded}"
        expected = hmac.new(signing_key, b"voa-approval-sandbox:" + payload.encode(), hashlib.sha256).hexdigest()
        if version != "sbx1" or not hmac.compare_digest(supplied_mac, expected):
            raise ValueError("Bad signature")
        sandbox_id = urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        if not 1 <= len(sandbox_id) <= 100 or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in sandbox_id):
            raise ValueError("Bad sandbox")
        return sandbox_id, inner
    except (ValueError, UnicodeError):
        raise InvalidApprovalTokenError("Unknown or invalid approval token") from None


def _assert_proposal_approvable(proposal: Proposal) -> None:
    """Enforce P2: BLOCK and P2.3 non-approvable reasons can never be approved by a human."""
    if proposal.decision == "BLOCK" or proposal.state == "BLOCKED":
        raise ApprovalNotAllowedError("BLOCK proposals can never be approved", proposal=proposal)
    if proposal.decision != "ASK":
        raise ApprovalNotAllowedError(
            f"Proposal with decision {proposal.decision} does not take human approval",
            proposal=proposal,
        )
    non_approvable = [
        code
        for code in proposal.reasons
        if code in REASON_REGISTRY
        and not REASON_REGISTRY[code].approvable
        and REASON_REGISTRY[code].effect != "INFO"
    ]
    if non_approvable:
        joined = ", ".join(str(c) for c in non_approvable)
        raise ApprovalNotAllowedError(
            f"Proposal carries non-approvable reasons ({joined}); missing evidence cannot be waived",
            proposal=proposal,
        )


@dataclass(frozen=True)
class ApprovalGrant:
    """Internal server record of an issued single-use approval token."""

    token: str
    proposal_id: str
    offer_id: str
    offer_version: int
    offer_hash: str
    customer_id: str
    approver: Approver
    expires_at: datetime
    used_at: datetime | None = None
    used_action: ApprovalAction | None = None
    voided: bool = False

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    def is_expired(self, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return now > self.expires_at

    def matches_binding(self, proposal_id: str, offer_version: int, offer_hash: str) -> bool:
        return (
            self.proposal_id == proposal_id
            and self.offer_version == offer_version
            and self.offer_hash == offer_hash
        )


def _row_to_grant(row: ApprovalTokenRow) -> ApprovalGrant:
    return ApprovalGrant(
        token=row.token,
        proposal_id=row.proposal_id,
        offer_id=row.offer_id,
        offer_version=row.offer_version,
        offer_hash=row.offer_hash,
        customer_id=row.customer_id,
        approver=Approver(name=row.approver_name, role=row.approver_role),
        expires_at=row.expires_at,
        used_at=row.used_at,
        used_action=cast(ApprovalAction | None, row.used_action),
        voided=bool(row.voided),
    )


class ApprovalStore:
    """Manages single-use approval tokens bound to `proposalId + offerVersion + offerHash`."""

    def __init__(self, *, signing_key: str | bytes | None = None, session: Session | None = None):
        if signing_key is None:
            self._key = secrets.token_bytes(32)
        elif isinstance(signing_key, str):
            self._key = signing_key.encode("utf-8")
        else:
            self._key = bytes(signing_key)
        self._session = session
        self._by_token: dict[str, ApprovalGrant] = {}
        self._by_proposal: dict[str, list[str]] = {}
        if session is not None:
            for row in session.scalars(select(ApprovalTokenRow)).all():
                grant = _row_to_grant(row)
                self._by_token[grant.token] = grant
                self._by_proposal.setdefault(grant.proposal_id, []).append(grant.token)

    def _mint_token(self, proposal: Proposal) -> str:
        nonce = secrets.token_hex(8)
        payload = f"{proposal.proposalId}:{proposal.offerVersion}:{proposal.offerHash}:{nonce}".encode()
        mac = hmac.new(self._key, payload, hashlib.sha256).hexdigest()[:24]
        return f"atk-{nonce}-{mac}"

    def _save_grant(self, grant: ApprovalGrant) -> None:
        self._by_token[grant.token] = grant
        tokens = self._by_proposal.setdefault(grant.proposal_id, [])
        if grant.token not in tokens:
            tokens.append(grant.token)
        if self._session is not None:
            existing = self._session.get(ApprovalTokenRow, grant.token)
            if existing is None:
                self._session.add(
                    ApprovalTokenRow(
                        token=grant.token,
                        proposal_id=grant.proposal_id,
                        offer_id=grant.offer_id,
                        offer_version=grant.offer_version,
                        offer_hash=grant.offer_hash,
                        customer_id=grant.customer_id,
                        approver_name=grant.approver.name,
                        approver_role=grant.approver.role,
                        expires_at=grant.expires_at,
                        used_at=grant.used_at,
                        used_action=grant.used_action,
                        voided=1 if grant.voided else 0,
                    )
                )
            else:
                existing.used_at = grant.used_at
                existing.used_action = grant.used_action
                existing.voided = 1 if grant.voided else 0
            self._session.commit()

    def _refresh_from_session(self) -> None:
        if self._session is None:
            return
        for row in self._session.scalars(select(ApprovalTokenRow)).all():
            grant = _row_to_grant(row)
            self._by_token[grant.token] = grant
            tokens = self._by_proposal.setdefault(grant.proposal_id, [])
            if grant.token not in tokens:
                tokens.append(grant.token)

    def issue(
        self,
        proposal: Proposal,
        approver: Approver,
        *,
        approval_statement: str | None = None,
        approval_url: str | None = None,
        qr_payload: str | None = None,
    ) -> ApproverView:
        """Issue or retrieve the single-use `ApproverView` for a `PENDING_APPROVAL` proposal."""
        _assert_proposal_approvable(proposal)
        if proposal.state != "PENDING_APPROVAL":
            raise ApprovalNotAllowedError(
                f"Cannot issue approval token for proposal in state {proposal.state}",
                proposal=proposal,
            )
        self._refresh_from_session()
        for token in self._by_proposal.get(proposal.proposalId, ()):
            existing = self._by_token[token]
            if (
                not existing.is_used
                and not existing.voided
                and existing.matches_binding(proposal.proposalId, proposal.offerVersion, proposal.offerHash)
            ):
                return ApproverView(
                    proposal=proposal,
                    approver=existing.approver,
                    approvalStatement=approval_statement,
                    approvalToken=existing.token,
                    approvalUrl=approval_url,
                    qrPayload=qr_payload,
                    expiresAt=existing.expires_at,
                    permissionNotice=PERMISSION_NOTICE,
                )

        token = self._mint_token(proposal)
        grant = ApprovalGrant(
            token=token,
            proposal_id=proposal.proposalId,
            offer_id=proposal.offerId,
            offer_version=proposal.offerVersion,
            offer_hash=proposal.offerHash,
            customer_id=proposal.customerId,
            approver=approver,
            expires_at=proposal.validUntil,
        )
        self._save_grant(grant)
        return ApproverView(
            proposal=proposal,
            approver=approver,
            approvalStatement=approval_statement,
            approvalToken=token,
            approvalUrl=approval_url,
            qrPayload=qr_payload,
            expiresAt=proposal.validUntil,
            permissionNotice=PERMISSION_NOTICE,
        )

    def get_grant(self, token: str) -> ApprovalGrant | None:
        self._refresh_from_session()
        return self._by_token.get(token)

    def grants_for_proposal(self, proposal_id: str) -> tuple[ApprovalGrant, ...]:
        self._refresh_from_session()
        return tuple(self._by_token[t] for t in self._by_proposal.get(proposal_id, ()))

    def void_for_proposal(self, proposal_id: str) -> None:
        """Void all issued or consumed approvals for `proposal_id` (e.g. on SUPERSEDED or EXPIRED)."""
        self._refresh_from_session()
        for token in self._by_proposal.get(proposal_id, ()):
            grant = self._by_token[token]
            if not grant.voided:
                self._save_grant(replace(grant, voided=True))

    def is_approval_valid(
        self,
        proposal_id: str,
        offer_version: int | None = None,
        offer_hash: str | None = None,
    ) -> bool:
        """Return True only when an unvoided APPROVE decision exists for the exact proposal version."""
        self._refresh_from_session()
        for token in self._by_proposal.get(proposal_id, ()):
            grant = self._by_token[token]
            if grant.used_action != "APPROVE" or grant.voided:
                continue
            if offer_version is not None and grant.offer_version != offer_version:
                continue
            if offer_hash is not None and grant.offer_hash != offer_hash:
                continue
            return True
        return False

    def verify_and_consume(
        self,
        proposal: Proposal,
        *,
        offer_version: int,
        approval_token: str,
        action: str,
        now: datetime,
    ) -> ApprovalGrant:
        """Verify single use, expiry and binding to `proposalId + offerVersion + offerHash`, then consume."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if action not in ALLOWED_ACTIONS:
            raise InvalidApprovalActionError(
                f"Invalid approval action {action!r}; only APPROVE or REJECT is allowed",
                proposal=proposal,
            )
        _assert_proposal_approvable(proposal)
        self._refresh_from_session()

        grant = self._by_token.get(approval_token)
        if grant is None:
            raise InvalidApprovalTokenError("Unknown or invalid approval token", proposal=proposal)
        if grant.proposal_id != proposal.proposalId:
            raise TokenBindingMismatchError("Approval token is not bound to this proposalId", proposal=proposal)
        if grant.is_used:
            raise TokenAlreadyUsedError("Approval token is single-use and has already been used", proposal=proposal)
        if grant.offer_version != offer_version or proposal.offerVersion != offer_version:
            if proposal.state == "SUPERSEDED" or grant.voided:
                raise ApprovalSupersededError(
                    "Proposal was superseded; prior approval token is void",
                    proposal=proposal,
                )
            raise TokenBindingMismatchError("Approval token is not bound to this offerVersion", proposal=proposal)
        if grant.offer_hash != proposal.offerHash:
            if proposal.state == "SUPERSEDED" or grant.voided:
                raise ApprovalSupersededError(
                    "Proposal was superseded; prior approval token is void",
                    proposal=proposal,
                )
            raise TokenBindingMismatchError("Approval token is not bound to this offerHash", proposal=proposal)
        if grant.voided or proposal.state == "SUPERSEDED":
            raise ApprovalSupersededError(
                "Proposal was superseded; prior approval token is void",
                proposal=proposal,
            )
        if proposal.state == "EXPIRED" or now > proposal.validUntil or grant.is_expired(now):
            raise ApprovalExpiredError("Offer and approval token have expired", proposal=proposal)
        if proposal.state != "PENDING_APPROVAL":
            raise ApprovalNotAllowedError(
                f"Proposal in state {proposal.state} cannot be decided",
                proposal=proposal,
            )

        consumed = replace(grant, used_at=now, used_action=cast(ApprovalAction, action))
        self._save_grant(consumed)
        return consumed


__all__ = [
    "ALLOWED_ACTIONS",
    "DEMO_APPROVER_ACTOR",
    "PERMISSION_NOTICE",
    "ApprovalError",
    "ApprovalExpiredError",
    "ApprovalGrant",
    "ApprovalNotAllowedError",
    "ApprovalStore",
    "ApprovalSupersededError",
    "InvalidApprovalActionError",
    "InvalidApprovalTokenError",
    "TokenAlreadyUsedError",
    "TokenBindingMismatchError",
    "ReasonCode",
]
