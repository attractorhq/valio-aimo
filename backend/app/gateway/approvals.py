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
import json
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, cast

import webauthn
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

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


def approver_actor(role: str = "Head Chef", *, passkey: bool = False) -> str:
    """Format the audit actor for a named approver role (`human:Head Chef (passkey)` vs `human:Head Chef (link)`)."""
    kind = "passkey" if passkey else "link"
    return f"human:{role} ({kind})"


def proposal_passkey_challenge(proposal_id: str, offer_version: int, offer_hash: str) -> bytes:
    """Deterministic 32-byte WebAuthn challenge derived from `proposalId + offerVersion + offerHash` (T70)."""
    return hashlib.sha256(f"{proposal_id}:{offer_version}:{offer_hash}".encode()).digest()


def proposal_passkey_challenge_b64(proposal_id: str, offer_version: int, offer_hash: str) -> str:
    """Base64url-encoded WebAuthn challenge for `proposalId + offerVersion + offerHash`."""
    return bytes_to_base64url(proposal_passkey_challenge(proposal_id, offer_version, offer_hash))


def _default_origins_for_rp(rp_id: str, extra: str | list[str] | None = None) -> list[str]:
    origins = [
        f"https://{rp_id}",
        f"http://{rp_id}",
        f"http://{rp_id}:5173",
        f"http://{rp_id}:8000",
        f"http://{rp_id}:3000",
    ]
    if isinstance(extra, str) and extra and extra not in origins:
        origins.append(extra)
    elif isinstance(extra, list):
        for o in extra:
            if o and o not in origins:
                origins.append(o)
    return origins


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


class PasskeyRequiredError(ApprovalError):
    """Raised when a passkey is enrolled for the approver role and an approval is attempted with the link alone."""


class PasskeyVerificationError(ApprovalError):
    """Raised when WebAuthn passkey registration or assertion verification fails."""


@dataclass(frozen=True)
class PasskeyCredential:
    """Enrolled WebAuthn passkey credential for a named approver role in a sandbox."""

    credential_id: bytes
    public_key: bytes
    sign_count: int
    role: str
    rp_id: str = "localhost"

    @property
    def credential_id_b64(self) -> str:
        return bytes_to_base64url(self.credential_id)


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
    used_passkey: bool = False

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
        self._passkeys_by_role: dict[str, list[PasskeyCredential]] = {}
        self._registration_challenges: dict[str, tuple[bytes, str]] = {}
        self._pending_assertion: tuple[dict[str, Any] | str, str | list[str] | None, str | None] | None = None
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

    def has_passkey(self, role: str) -> bool:
        """Return True if at least one passkey is enrolled for `role` in this store."""
        return bool(self._passkeys_by_role.get(role))

    def passkeys_for_role(self, role: str) -> tuple[PasskeyCredential, ...]:
        """Return all passkey credentials enrolled for `role`."""
        return tuple(self._passkeys_by_role.get(role, ()))

    def register_passkey(
        self,
        role: str,
        credential_id: bytes,
        public_key: bytes,
        *,
        sign_count: int = 0,
        rp_id: str = "localhost",
    ) -> PasskeyCredential:
        """Store an enrolled passkey credential for `role`."""
        cred = PasskeyCredential(
            credential_id=bytes(credential_id),
            public_key=bytes(public_key),
            sign_count=int(sign_count),
            role=role,
            rp_id=rp_id,
        )
        existing = [c for c in self._passkeys_by_role.get(role, ()) if c.credential_id != cred.credential_id]
        existing.append(cred)
        self._passkeys_by_role[role] = existing
        return cred

    def begin_passkey_registration(
        self,
        role: str,
        *,
        rp_id: str = "localhost",
        rp_name: str = "Aimo Connect",
        challenge: bytes | None = None,
    ) -> dict[str, Any]:
        """Generate WebAuthn registration options (platform authenticator) for `role`."""
        options = webauthn.generate_registration_options(
            rp_id=rp_id,
            rp_name=rp_name,
            user_id=hashlib.sha256(f"approver:{role}".encode()).digest()[:32],
            user_name=role,
            user_display_name=f"{role} (Approver)",
            challenge=challenge,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,
                user_verification=UserVerificationRequirement.PREFERRED,
                resident_key=ResidentKeyRequirement.PREFERRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=cred.credential_id)
                for cred in self._passkeys_by_role.get(role, ())
            ],
        )
        self._registration_challenges[role] = (options.challenge, rp_id)
        return json.loads(webauthn.options_to_json(options))

    def complete_passkey_registration(
        self,
        role: str,
        credential: dict[str, Any] | str,
        *,
        expected_origin: str | list[str] | None = None,
        expected_rp_id: str | None = None,
    ) -> PasskeyCredential:
        """Verify WebAuthn registration response and store the credential for `role`."""
        pending = self._registration_challenges.get(role)
        if pending is None:
            raise PasskeyVerificationError(f"No pending passkey registration challenge for role {role!r}")
        challenge, stored_rp_id = pending
        rp_id = expected_rp_id or stored_rp_id
        origins = _default_origins_for_rp(rp_id, expected_origin)
        try:
            verified = webauthn.verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origins,
                require_user_verification=False,
            )
        except Exception as exc:
            raise PasskeyVerificationError(f"Passkey registration verification failed: {exc}") from exc
        self._registration_challenges.pop(role, None)
        return self.register_passkey(
            role,
            verified.credential_id,
            verified.credential_public_key,
            sign_count=verified.sign_count,
            rp_id=rp_id,
        )

    def authentication_options_for_proposal(
        self,
        proposal: Proposal,
        *,
        role: str | None = None,
        rp_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return WebAuthn authentication options bound to `proposalId + offerVersion + offerHash`, or None if no passkey."""
        target_role = role or proposal.approverRole or "Head Chef"
        creds = self._passkeys_by_role.get(target_role, [])
        if not creds:
            return None
        effective_rp_id = rp_id or creds[0].rp_id
        challenge = proposal_passkey_challenge(
            proposal.proposalId,
            proposal.offerVersion,
            proposal.offerHash,
        )
        options = webauthn.generate_authentication_options(
            rp_id=effective_rp_id,
            challenge=challenge,
            allow_credentials=[PublicKeyCredentialDescriptor(id=c.credential_id) for c in creds],
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        return json.loads(webauthn.options_to_json(options))

    def verify_passkey_assertion(
        self,
        proposal: Proposal,
        assertion: dict[str, Any] | str,
        *,
        offer_version: int | None = None,
        role: str | None = None,
        expected_origin: str | list[str] | None = None,
        expected_rp_id: str | None = None,
    ) -> PasskeyCredential:
        """Verify a WebAuthn assertion against the challenge derived from `proposalId + offerVersion + offerHash`."""
        target_role = role or proposal.approverRole or "Head Chef"
        creds = self._passkeys_by_role.get(target_role, [])
        if not creds:
            raise PasskeyVerificationError(
                f"No passkey enrolled for approver role {target_role!r}",
                proposal=proposal,
            )
        ver = offer_version if offer_version is not None else proposal.offerVersion
        expected_challenge = proposal_passkey_challenge(proposal.proposalId, ver, proposal.offerHash)

        raw_dict: dict[str, Any] = json.loads(assertion) if isinstance(assertion, str) else dict(assertion)
        raw_id_str = raw_dict.get("rawId") or raw_dict.get("id")
        candidate_creds = list(creds)
        if isinstance(raw_id_str, str) and raw_id_str:
            try:
                target_id = base64url_to_bytes(raw_id_str)
                matched = [c for c in creds if c.credential_id == target_id]
                if matched:
                    candidate_creds = matched
            except Exception:
                pass

        last_err: Exception | None = None
        for idx, cred in enumerate(candidate_creds):
            rp_id = expected_rp_id or cred.rp_id
            origins = _default_origins_for_rp(rp_id, expected_origin)
            try:
                verified = webauthn.verify_authentication_response(
                    credential=assertion,
                    expected_challenge=expected_challenge,
                    expected_rp_id=rp_id,
                    expected_origin=origins,
                    credential_public_key=cred.public_key,
                    credential_current_sign_count=cred.sign_count,
                    require_user_verification=False,
                )
                updated = replace(cred, sign_count=verified.new_sign_count)
                self._passkeys_by_role[target_role] = [
                    updated if c.credential_id == cred.credential_id else c for c in creds
                ]
                return updated
            except Exception as exc:
                last_err = exc
                if idx == len(candidate_creds) - 1:
                    break
        raise PasskeyVerificationError(
            f"Passkey assertion verification failed: {last_err}",
            proposal=proposal,
        ) from last_err

    @contextmanager
    def with_assertion(
        self,
        assertion: dict[str, Any] | str | None,
        *,
        expected_origin: str | list[str] | None = None,
        expected_rp_id: str | None = None,
    ) -> Iterator[None]:
        """Scope a WebAuthn assertion for the next `verify_and_consume` call on this store."""
        prev = self._pending_assertion
        self._pending_assertion = (
            (assertion, expected_origin, expected_rp_id) if assertion is not None else None
        )
        try:
            yield
        finally:
            self._pending_assertion = prev

    def verify_and_consume(
        self,
        proposal: Proposal,
        *,
        offer_version: int,
        approval_token: str,
        action: str,
        now: datetime,
        passkey_assertion: dict[str, Any] | str | None = None,
        expected_origin: str | list[str] | None = None,
        expected_rp_id: str | None = None,
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

        active_assertion = passkey_assertion
        active_origin = expected_origin
        active_rp_id = expected_rp_id
        if active_assertion is None and self._pending_assertion is not None:
            active_assertion, active_origin, active_rp_id = self._pending_assertion

        used_passkey = False
        role = grant.approver.role
        if action == "APPROVE" and (self.has_passkey(role) or active_assertion is not None):
            if active_assertion is None:
                raise PasskeyRequiredError(
                    f"Passkey required: a passkey is enrolled for {role}; link alone is refused",
                    proposal=proposal,
                )
            self.verify_passkey_assertion(
                proposal,
                active_assertion,
                offer_version=offer_version,
                role=role,
                expected_origin=active_origin,
                expected_rp_id=active_rp_id,
            )
            used_passkey = True

        consumed = replace(
            grant,
            used_at=now,
            used_action=cast(ApprovalAction, action),
            used_passkey=used_passkey,
        )
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
    "PasskeyCredential",
    "PasskeyRequiredError",
    "PasskeyVerificationError",
    "TokenAlreadyUsedError",
    "TokenBindingMismatchError",
    "ReasonCode",
    "approver_actor",
    "proposal_passkey_challenge",
    "proposal_passkey_challenge_b64",
]
