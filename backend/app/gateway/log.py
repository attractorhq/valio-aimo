"""P1.4 state machine and per-proposal decision log (patch P1.4, P6 P0-5, docs/API.md).

Every state transition appends an `AuditEntry` recording:
- what happened (`event`, `fromState`, `toState`, `summary`);
- who or what decided (`actor`: `gateway:*` or `human:Head Chef (demo, link-based)`);
- which rule (`rule`, `reasons`);
- which facts (`facts` with their `TrustLabel`s).

No approval token, URL or QR payload may ever be stored in a decision-log entry (patch P3).
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import (
    REASON_REGISTRY,
    AuditEntry,
    AuditTrail,
    DecisionOutcome,
    GetAuditTrailRequest,
    Mandate,
    OfferLine,
    ProposalState,
    ReasonCode,
    TrustLabel,
    format_money,
)
from app.db.models import DecisionLogRow
from app.policy import Finding

PRE_CONFIRMATION_STATES: frozenset[ProposalState] = frozenset({
    "OFFERED",
    "PROPOSED",
    "AUTO_APPROVED",
    "PENDING_APPROVAL",
    "APPROVED",
    "RECHECKED",
})

TERMINAL_STATES: frozenset[ProposalState] = frozenset({
    "CONFIRMED_SIMULATED",
    "BLOCKED",
    "REJECTED",
    "EXPIRED",
    "SUPERSEDED",
})

ALLOWED_TRANSITIONS: MappingProxyType[ProposalState | None, frozenset[ProposalState]] = MappingProxyType({
    None: frozenset({"OFFERED"}),
    "OFFERED": frozenset({"PROPOSED", "EXPIRED", "SUPERSEDED"}),
    "PROPOSED": frozenset({"AUTO_APPROVED", "PENDING_APPROVAL", "BLOCKED", "EXPIRED", "SUPERSEDED"}),
    "AUTO_APPROVED": frozenset({"RECHECKED", "EXPIRED", "SUPERSEDED"}),
    "PENDING_APPROVAL": frozenset({"APPROVED", "REJECTED", "EXPIRED", "SUPERSEDED"}),
    "APPROVED": frozenset({"RECHECKED", "EXPIRED", "SUPERSEDED"}),
    "RECHECKED": frozenset({"CONFIRMED_SIMULATED", "EXPIRED", "SUPERSEDED"}),
    "CONFIRMED_SIMULATED": frozenset(),
    "BLOCKED": frozenset(),
    "REJECTED": frozenset(),
    "EXPIRED": frozenset(),
    "SUPERSEDED": frozenset(),
})


class InvalidStateTransitionError(ValueError):
    """Raised when a transition violates the P1.4 state machine."""

    def __init__(self, from_state: ProposalState | None, to_state: ProposalState, message: str | None = None):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(message or f"invalid state transition: {from_state!r} -> {to_state!r}")


def can_transition(from_state: ProposalState | None, to_state: ProposalState) -> bool:
    """Return True if `from_state -> to_state` is permitted by P1.4."""
    return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())


def validate_transition(from_state: ProposalState | None, to_state: ProposalState) -> None:
    """Enforce the P1.4 state machine before any state change or log entry."""
    if not can_transition(from_state, to_state):
        raise InvalidStateTransitionError(from_state, to_state)


def rule_summary(findings: Sequence[Finding]) -> str:
    """Format the policy rules that fired in evaluation order, or §6.6 when none fired."""
    rules = list(dict.fromkeys(f.rule for f in findings if f.rule))
    return ", ".join(rules) if rules else "§6.6"


def decision_summary(
    decision: DecisionOutcome,
    reasons: Sequence[ReasonCode],
    *,
    total_excl_vat: Decimal,
    mandate: Mandate,
) -> str:
    """Readable summary of the gateway's policy decision for the decision log."""
    total_text = format_money(total_excl_vat)
    auto_text = format_money(mandate.autoApproveLimitEur)
    hard_text = format_money(mandate.hardLimitEur)
    chips = [REASON_REGISTRY[code].chipText for code in reasons if code in REASON_REGISTRY]
    reason_suffix = f" Reasons: {', '.join(chips)}." if chips else ""
    if decision == "AUTO":
        return (
            f"AUTO: all requirements proven and {total_text} excl. VAT is within the "
            f"{auto_text} auto-approve limit.{reason_suffix}"
        )
    if decision == "ASK":
        if list(reasons) == [ReasonCode.OVER_AUTO_LIMIT]:
            return (
                f"ASK: {total_text} excl. VAT is above your {auto_text} auto-approve limit. "
                f"Waiting for {mandate.approver.role}."
            )
        return (
            f"ASK: human approval required from {mandate.approver.role} for {total_text} excl. VAT."
            f"{reason_suffix}"
        )
    if ReasonCode.OVER_HARD_LIMIT in reasons:
        return (
            f"BLOCK: {total_text} excl. VAT is above your {hard_text} hard limit. "
            f"No agent or approver can override this.{reason_suffix}"
        )
    return f"BLOCK: proposal violates a hard mandate constraint.{reason_suffix}"


def decisive_facts(lines: Sequence[OfferLine]) -> list[TrustLabel]:
    """Collect the trust-labelled facts across the proposal lines for the decision log."""
    collected: list[TrustLabel] = []
    for line in lines:
        collected.extend(line.facts)
    return collected


def _check_no_credentials(entry: AuditEntry, forbidden_tokens: Iterable[str] = ()) -> None:
    """Guard against leaking approval tokens, URLs or QR payloads into shared logs (patch P3)."""
    texts = [entry.summary, entry.comment or "", *(str(f.value or "") for f in entry.facts),
             *(f.display for f in entry.facts)]
    combined = "\n".join(texts)
    lower = combined.lower()
    if any(marker in lower for marker in ("approvaltoken", "approvalurl", "qrpayload", "/approver/proposals/")):
        raise ValueError("Decision log entry must never contain approval links, tokens or QR payloads")
    for secret in forbidden_tokens:
        if secret and secret in combined:
            raise ValueError("Decision log entry must never contain an approval token")


class DecisionLog:
    """Append-only decision log for offers, proposals and mandate events (in-memory + optional SQLite)."""

    def __init__(self, session: Session | None = None):
        self._session = session
        self._entries: list[AuditEntry] = []
        self._forbidden_tokens: set[str] = set()
        if session is not None:
            rows = session.scalars(select(DecisionLogRow).order_by(DecisionLogRow.seq)).all()
            self._entries = [AuditEntry.model_validate(row.data) for row in rows]

    def register_secret_token(self, token: str) -> None:
        """Register an approval token so the log guard refuses it if ever passed in text or facts."""
        if token:
            self._forbidden_tokens.add(token)

    def entries(self) -> tuple[AuditEntry, ...]:
        if self._session is not None:
            rows = self._session.scalars(select(DecisionLogRow).order_by(DecisionLogRow.seq)).all()
            return tuple(AuditEntry.model_validate(row.data) for row in rows)
        return tuple(self._entries)

    def append(self, entry: AuditEntry) -> AuditEntry:
        """Validate and append an immutable `AuditEntry`."""
        _check_no_credentials(entry, self._forbidden_tokens)
        self._entries.append(entry)
        if self._session is not None:
            self._session.add(
                DecisionLogRow(
                    entry_id=entry.entryId,
                    customer_id=entry.customerId,
                    proposal_id=entry.proposalId,
                    offer_id=entry.offerId,
                    offer_version=entry.offerVersion,
                    event=entry.event,
                    data=entry.model_dump(mode="json"),
                    timestamp=entry.timestamp,
                )
            )
            self._session.commit()
        return entry

    def record_transition(
        self,
        *,
        customer_id: str,
        mandate_version: int,
        timestamp: datetime,
        actor: str,
        event: str,
        from_state: ProposalState | None,
        to_state: ProposalState,
        summary: str,
        proposal_id: str | None = None,
        offer_id: str | None = None,
        offer_version: int | None = None,
        offer_hash: str | None = None,
        reasons: Sequence[ReasonCode] = (),
        rule: str | None = None,
        facts: Sequence[TrustLabel] = (),
        comment: str | None = None,
        entry_id: str | None = None,
    ) -> AuditEntry:
        """Validate a P1.4 state transition and append its `AuditEntry`."""
        if timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        validate_transition(from_state, to_state)
        seq = len(self.entries()) + 1
        prefix = proposal_id or offer_id or customer_id
        entry = AuditEntry(
            entryId=entry_id or f"{prefix}-e{seq}",
            customerId=customer_id,
            proposalId=proposal_id,
            offerId=offer_id,
            offerVersion=offer_version,
            offerHash=offer_hash,
            mandateVersion=mandate_version,
            timestamp=timestamp,
            actor=actor,
            event=event,
            fromState=from_state,
            toState=to_state,
            reasons=list(reasons),
            rule=rule,
            facts=list(facts),
            summary=summary,
            comment=comment,
        )
        return self.append(entry)

    def bind_offer_entries(self, offer_id: str, offer_version: int, proposal_id: str) -> None:
        """Attach `proposal_id` to the initial `OFFERED` entry for `(offer_id, offer_version)`."""
        for idx, entry in enumerate(self._entries):
            if entry.offerId == offer_id and entry.offerVersion == offer_version and entry.proposalId is None:
                updated = entry.model_copy(update={"proposalId": proposal_id})
                self._entries[idx] = updated
        if self._session is not None:
            rows = self._session.scalars(
                select(DecisionLogRow).where(
                    DecisionLogRow.offer_id == offer_id,
                    DecisionLogRow.offer_version == offer_version,
                    DecisionLogRow.proposal_id.is_(None),
                )
            ).all()
            for row in rows:
                updated = AuditEntry.model_validate({**row.data, "proposalId": proposal_id})
                row.proposal_id = proposal_id
                row.data = updated.model_dump(mode="json")
            if rows:
                self._session.commit()

    def for_proposal(self, proposal_id: str) -> AuditTrail:
        """Return the ordered decision log for `proposal_id`."""
        return AuditTrail(entries=[e for e in self.entries() if e.proposalId == proposal_id])

    def for_customer(self, customer_id: str) -> AuditTrail:
        """Return the ordered decision log for `customer_id`."""
        return AuditTrail(entries=[e for e in self.entries() if e.customerId == customer_id])

    def get_audit_trail(
        self,
        request: GetAuditTrailRequest | None = None,
        *,
        proposal_id: str | None = None,
        customer_id: str | None = None,
    ) -> AuditTrail:
        """Return the audit trail for either `proposalId` or `customerId` (XOR per docs/API.md)."""
        if request is not None:
            proposal_id = request.proposalId
            customer_id = request.customerId
        if bool(proposal_id) == bool(customer_id):
            raise ValueError("Specify exactly one of proposalId or customerId")
        if proposal_id is not None:
            return self.for_proposal(proposal_id)
        assert customer_id is not None
        return self.for_customer(customer_id)
