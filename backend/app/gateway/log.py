"""P1.4 state machine and per-proposal decision log (patch P1.4, P6 P0-5, docs/API.md).

Every state transition appends an `AuditEntry` recording:
- what happened (`event`, `fromState`, `toState`, `summary`);
- who or what decided (`actor`: `gateway:*` or `human:Head Chef (demo, link-based)`);
- which rule (`rule`, `reasons`);
- which facts (`facts` with their `TrustLabel`s).

No approval token, URL or QR payload may ever be stored in a decision-log entry (patch P3).
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType

from pydantic import Field
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
    TrustSource,
    format_date,
    format_money,
)
from app.contracts.common import Contract
from app.db.models import DecisionLogRow
from app.policy import Finding

GENESIS_HASH: str = "0" * 64


class AuditChainLink(Contract):
    """One link in the SHA-256 hash chain over the decision log (T28)."""

    index: int = Field(ge=0)
    entryId: str
    prevHash: str
    entryHash: str
    expectedPrevHash: str
    expectedEntryHash: str
    valid: bool


class AuditChainVerification(Contract):
    """Verification result for the SHA-256 hash chain over a decision log (T28)."""

    verified: bool
    chainLength: int = Field(ge=0)
    genesisHash: str = GENESIS_HASH
    headHash: str = GENESIS_HASH
    tamperedEntryIds: list[str] = Field(default_factory=list)
    firstTamperedIndex: int | None = None
    reason: str | None = None
    links: list[AuditChainLink] = Field(default_factory=list)


def canonical_entry_json(entry: AuditEntry) -> str:
    """Canonical JSON serialization of an `AuditEntry` for SHA-256 hashing.

    `OFFER_CREATED` entries are logged at `getOffer` time with `proposalId=None` and later indexed
    under a `proposalId` by `bind_offer_entries`; normalising `proposalId=None` on `OFFER_CREATED`
    keeps the entry's hash invariant when bound so earlier links never need to be rewritten.
    """
    payload = entry.model_dump(mode="json")
    if payload.get("event") == "OFFER_CREATED":
        payload["proposalId"] = None
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_entry_hash(entry: AuditEntry, prev_hash: str = GENESIS_HASH) -> str:
    """Compute `sha256(f"{prev_hash}:{canonical_entry_json(entry)}")`."""
    canonical = canonical_entry_json(entry)
    return hashlib.sha256(f"{prev_hash}:{canonical}".encode()).hexdigest()


def build_hash_chain(
    entries: Sequence[AuditEntry],
    genesis_hash: str = GENESIS_HASH,
) -> list[AuditChainLink]:
    """Build the sequential SHA-256 hash chain links for `entries`."""
    links: list[AuditChainLink] = []
    prev_hash = genesis_hash
    for idx, entry in enumerate(entries):
        entry_hash = compute_entry_hash(entry, prev_hash)
        links.append(
            AuditChainLink(
                index=idx,
                entryId=entry.entryId,
                prevHash=prev_hash,
                entryHash=entry_hash,
                expectedPrevHash=prev_hash,
                expectedEntryHash=entry_hash,
                valid=True,
            )
        )
        prev_hash = entry_hash
    return links


def verify_hash_chain(
    entries: Sequence[AuditEntry],
    sealed_links: Sequence[AuditChainLink] | None = None,
    *,
    genesis_hash: str = GENESIS_HASH,
) -> AuditChainVerification:
    """Verify `entries` against `sealed_links` (or self-consistency when `sealed_links` is None)."""
    if sealed_links is None:
        links = build_hash_chain(entries, genesis_hash=genesis_hash)
        head = links[-1].entryHash if links else genesis_hash
        return AuditChainVerification(
            verified=True,
            chainLength=len(links),
            genesisHash=genesis_hash,
            headHash=head,
            links=links,
        )

    links = []
    tampered_ids: list[str] = []
    first_tampered: int | None = None
    reason: str | None = None
    recomputed_prev = genesis_hash

    if len(entries) != len(sealed_links):
        first_tampered = min(len(entries), len(sealed_links))
        reason = f"Chain length mismatch: expected {len(sealed_links)} entries, found {len(entries)}"

    for idx, entry in enumerate(entries):
        recomputed_hash = compute_entry_hash(entry, recomputed_prev)
        if idx < len(sealed_links):
            sealed = sealed_links[idx]
            id_matches = entry.entryId == sealed.entryId
            prev_matches = recomputed_prev == sealed.prevHash
            hash_matches = recomputed_hash == sealed.entryHash
            valid = id_matches and prev_matches and hash_matches
            if not valid:
                if entry.entryId not in tampered_ids:
                    tampered_ids.append(entry.entryId)
                if not id_matches and sealed.entryId not in tampered_ids:
                    tampered_ids.append(sealed.entryId)
                if first_tampered is None or idx < first_tampered:
                    first_tampered = idx
                if reason is None:
                    if not id_matches:
                        reason = (
                            f"Entry ID mismatch at index {idx}: "
                            f"expected {sealed.entryId}, got {entry.entryId}"
                        )
                    else:
                        reason = (
                            f"SHA-256 hash mismatch at index {idx} ({entry.entryId}): "
                            f"expected {sealed.entryHash[:12]}…, got {recomputed_hash[:12]}…"
                        )
            links.append(
                AuditChainLink(
                    index=idx,
                    entryId=entry.entryId,
                    prevHash=sealed.prevHash,
                    entryHash=sealed.entryHash,
                    expectedPrevHash=recomputed_prev,
                    expectedEntryHash=recomputed_hash,
                    valid=valid,
                )
            )
        else:
            if entry.entryId not in tampered_ids:
                tampered_ids.append(entry.entryId)
            links.append(
                AuditChainLink(
                    index=idx,
                    entryId=entry.entryId,
                    prevHash=recomputed_prev,
                    entryHash=recomputed_hash,
                    expectedPrevHash=recomputed_prev,
                    expectedEntryHash=recomputed_hash,
                    valid=False,
                )
            )
        recomputed_prev = recomputed_hash

    if len(sealed_links) > len(entries):
        for missing in sealed_links[len(entries) :]:
            if missing.entryId not in tampered_ids:
                tampered_ids.append(missing.entryId)

    verified = (
        len(entries) == len(sealed_links)
        and first_tampered is None
        and all(link.valid for link in links)
    )
    head_hash = links[-1].expectedEntryHash if links else genesis_hash
    return AuditChainVerification(
        verified=verified,
        chainLength=len(links),
        genesisHash=genesis_hash,
        headHash=head_hash,
        tamperedEntryIds=tampered_ids,
        firstTamperedIndex=first_tampered,
        reason=reason,
        links=links,
    )

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
    available_date: date | None = None,
    requested_date: date | None = None,
) -> str:
    """Readable summary of the gateway's policy decision for the decision log."""
    total_text = format_money(total_excl_vat)
    auto_text = format_money(mandate.autoApproveLimitEur)
    hard_text = format_money(mandate.hardLimitEur)
    reason_details: list[str] = []
    for code in reasons:
        if code not in REASON_REGISTRY:
            continue
        chip = REASON_REGISTRY[code].chipText
        if code == ReasonCode.DATE_LATER and available_date is not None and requested_date is not None:
            reason_details.append(
                f"{chip} ({REASON_REGISTRY[code].template.format(availableDate=format_date(available_date), requestedDate=format_date(requested_date))})"
            )
        else:
            reason_details.append(chip)
    reason_suffix = f" Reasons: {', '.join(reason_details)}." if reason_details else ""
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


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _infer_requested_date_from_entry(
    *,
    offer_id: str | None = None,
    proposal_id: str | None = None,
    reasons: Sequence[ReasonCode] = (),
    facts: Sequence[TrustLabel] = (),
) -> date | None:
    """Infer `requestedDate` for a transition entry when not yet present in `facts`."""
    for fact in facts:
        if fact.fact == "delivery.requestedDate" and fact.value is not None:
            parsed = _parse_iso_date(fact.value)
            if parsed is not None:
                return parsed
    for ident in (offer_id, proposal_id):
        if not ident:
            continue
        stem = ident.removeprefix("OF-").removeprefix("PR-")
        if len(stem) >= 10:
            parsed = _parse_iso_date(stem[:10])
            if parsed is not None:
                return parsed
    if ReasonCode.DATE_LATER not in reasons:
        for fact in facts:
            if fact.fact == "delivery.date" and fact.value is not None:
                parsed = _parse_iso_date(fact.value)
                if parsed is not None:
                    return parsed
    return None


def _fill_summary_reason_templates(
    summary: str,
    *,
    reasons: Sequence[ReasonCode],
    facts: Sequence[TrustLabel],
) -> str:
    """Append filled reason template text (such as DATE_LATER) to `summary` when not already present."""
    if not summary:
        return summary
    delivery_dates = [
        d
        for f in facts
        if f.fact == "delivery.date" and f.value is not None and (d := _parse_iso_date(f.value)) is not None
    ]
    req_dates = [
        d
        for f in facts
        if f.fact == "delivery.requestedDate" and f.value is not None and (d := _parse_iso_date(f.value)) is not None
    ]
    if ReasonCode.DATE_LATER in reasons and delivery_dates and req_dates:
        avail_date = max(delivery_dates)
        req_date = req_dates[0]
        filled = REASON_REGISTRY[ReasonCode.DATE_LATER].template.format(
            availableDate=format_date(avail_date),
            requestedDate=format_date(req_date),
        )
        if filled not in summary and format_date(avail_date) not in summary:
            summary = f"{summary.rstrip()} {filled}"
    return summary


def _requested_date_label(
    lines: Sequence[OfferLine],
    *,
    requested_date: date | None = None,
    retrieved_at: datetime | None = None,
) -> TrustLabel | None:
    """Return a `delivery.requestedDate` TrustLabel when `requested_date` is known and not yet in `lines`."""
    if requested_date is None:
        return None
    for line in lines:
        for fact in line.facts:
            if fact.fact == "delivery.requestedDate":
                return None
    ts = retrieved_at
    if ts is None:
        for line in lines:
            for fact in line.facts:
                if fact.source.retrievedAt is not None:
                    ts = fact.source.retrievedAt
                    break
            if ts is not None:
                break
    return TrustLabel(
        fact="delivery.requestedDate",
        value=requested_date.isoformat(),
        display=f"Requested delivery: {format_date(requested_date)}",
        level="VERIFIED",
        source=TrustSource(
            system="derived",
            field="request.requestedDate",
            retrievedAt=ts,
        ),
        method=None,
        conflict=None,
    )


def decisive_facts(
    lines: Sequence[OfferLine],
    *,
    requested_date: date | None = None,
    retrieved_at: datetime | None = None,
) -> list[TrustLabel]:
    """Collect the trust-labelled facts across the proposal lines for the decision log."""
    collected: list[TrustLabel] = []
    for line in lines:
        collected.extend(line.facts)
    req_label = _requested_date_label(lines, requested_date=requested_date, retrieved_at=retrieved_at)
    if req_label is not None:
        collected.append(req_label)
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


def _strip_chain_meta(raw: dict) -> dict:
    return {k: v for k, v in raw.items() if k != "_chain"}


class DecisionLog:
    """Append-only decision log for offers, proposals and mandate events (in-memory + optional SQLite)."""

    def __init__(self, session: Session | None = None):
        self._session = session
        self._entries: list[AuditEntry] = []
        self._forbidden_tokens: set[str] = set()
        self._sealed_global: list[AuditChainLink] = []
        self._sealed_by_proposal: dict[str, list[AuditChainLink]] = {}
        self._sealed_by_customer: dict[str, list[AuditChainLink]] = {}
        if session is not None:
            rows = session.scalars(select(DecisionLogRow).order_by(DecisionLogRow.seq)).all()
            for row in rows:
                entry = AuditEntry.model_validate(_strip_chain_meta(row.data))
                self._entries.append(entry)
                meta = (row.data or {}).get("_chain") or {}
                g_meta = meta.get("global")
                if g_meta and "prevHash" in g_meta and "entryHash" in g_meta:
                    g_idx = len(self._sealed_global)
                    self._sealed_global.append(
                        AuditChainLink(
                            index=g_idx,
                            entryId=row.entry_id,
                            prevHash=g_meta["prevHash"],
                            entryHash=g_meta["entryHash"],
                            expectedPrevHash=g_meta["prevHash"],
                            expectedEntryHash=g_meta["entryHash"],
                            valid=True,
                        )
                    )
                else:
                    self._seal_entry_global(entry)

                c_meta = meta.get("customer")
                if c_meta and "prevHash" in c_meta and "entryHash" in c_meta:
                    c_chain = self._sealed_by_customer.setdefault(row.customer_id, [])
                    c_idx = len(c_chain)
                    c_chain.append(
                        AuditChainLink(
                            index=c_idx,
                            entryId=row.entry_id,
                            prevHash=c_meta["prevHash"],
                            entryHash=c_meta["entryHash"],
                            expectedPrevHash=c_meta["prevHash"],
                            expectedEntryHash=c_meta["entryHash"],
                            valid=True,
                        )
                    )
                else:
                    self._seal_entry_customer(entry)

                if row.proposal_id is not None:
                    p_meta = meta.get("proposal")
                    if p_meta and "prevHash" in p_meta and "entryHash" in p_meta:
                        p_chain = self._sealed_by_proposal.setdefault(row.proposal_id, [])
                        p_idx = len(p_chain)
                        p_chain.append(
                            AuditChainLink(
                                index=p_idx,
                                entryId=row.entry_id,
                                prevHash=p_meta["prevHash"],
                                entryHash=p_meta["entryHash"],
                                expectedPrevHash=p_meta["prevHash"],
                                expectedEntryHash=p_meta["entryHash"],
                                valid=True,
                            )
                        )
                    else:
                        self._seal_entry_proposal(entry)

    def _seal_entry_global(self, entry: AuditEntry) -> AuditChainLink:
        prev_hash = self._sealed_global[-1].entryHash if self._sealed_global else GENESIS_HASH
        entry_hash = compute_entry_hash(entry, prev_hash)
        link = AuditChainLink(
            index=len(self._sealed_global),
            entryId=entry.entryId,
            prevHash=prev_hash,
            entryHash=entry_hash,
            expectedPrevHash=prev_hash,
            expectedEntryHash=entry_hash,
            valid=True,
        )
        self._sealed_global.append(link)
        return link

    def _seal_entry_customer(self, entry: AuditEntry) -> AuditChainLink:
        c_chain = self._sealed_by_customer.setdefault(entry.customerId, [])
        prev_hash = c_chain[-1].entryHash if c_chain else GENESIS_HASH
        entry_hash = compute_entry_hash(entry, prev_hash)
        link = AuditChainLink(
            index=len(c_chain),
            entryId=entry.entryId,
            prevHash=prev_hash,
            entryHash=entry_hash,
            expectedPrevHash=prev_hash,
            expectedEntryHash=entry_hash,
            valid=True,
        )
        c_chain.append(link)
        return link

    def _seal_entry_proposal(self, entry: AuditEntry) -> AuditChainLink | None:
        if entry.proposalId is None:
            return None
        p_chain = self._sealed_by_proposal.setdefault(entry.proposalId, [])
        prev_hash = p_chain[-1].entryHash if p_chain else GENESIS_HASH
        entry_hash = compute_entry_hash(entry, prev_hash)
        link = AuditChainLink(
            index=len(p_chain),
            entryId=entry.entryId,
            prevHash=prev_hash,
            entryHash=entry_hash,
            expectedPrevHash=prev_hash,
            expectedEntryHash=entry_hash,
            valid=True,
        )
        p_chain.append(link)
        return link

    def register_secret_token(self, token: str) -> None:
        """Register an approval token so the log guard refuses it if ever passed in text or facts."""
        if token:
            self._forbidden_tokens.add(token)

    def entries(self) -> tuple[AuditEntry, ...]:
        if self._session is not None:
            rows = self._session.scalars(select(DecisionLogRow).order_by(DecisionLogRow.seq)).all()
            return tuple(AuditEntry.model_validate(_strip_chain_meta(row.data)) for row in rows)
        return tuple(self._entries)

    def append(self, entry: AuditEntry) -> AuditEntry:
        """Validate and append an immutable `AuditEntry`, sealing only the new link incrementally."""
        _check_no_credentials(entry, self._forbidden_tokens)
        self._entries.append(entry)
        g_link = self._seal_entry_global(entry)
        c_link = self._seal_entry_customer(entry)
        p_link = self._seal_entry_proposal(entry)

        if self._session is not None:
            chain_meta: dict[str, dict[str, str]] = {
                "global": {"prevHash": g_link.prevHash, "entryHash": g_link.entryHash},
                "customer": {"prevHash": c_link.prevHash, "entryHash": c_link.entryHash},
            }
            if p_link is not None:
                chain_meta["proposal"] = {"prevHash": p_link.prevHash, "entryHash": p_link.entryHash}
            self._session.add(
                DecisionLogRow(
                    entry_id=entry.entryId,
                    customer_id=entry.customerId,
                    proposal_id=entry.proposalId,
                    offer_id=entry.offerId,
                    offer_version=entry.offerVersion,
                    event=entry.event,
                    data={**entry.model_dump(mode="json"), "_chain": chain_meta},
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
        facts_list = list(facts)
        if facts_list and not any(f.fact == "delivery.requestedDate" for f in facts_list):
            req_date = _infer_requested_date_from_entry(
                offer_id=offer_id,
                proposal_id=proposal_id,
                reasons=reasons,
                facts=facts_list,
            )
            if req_date is None:
                for prev in reversed(self._entries):
                    if (proposal_id and prev.proposalId == proposal_id) or (offer_id and prev.offerId == offer_id):
                        req_date = _infer_requested_date_from_entry(
                            offer_id=prev.offerId,
                            proposal_id=prev.proposalId,
                            reasons=prev.reasons,
                            facts=prev.facts,
                        )
                        if req_date is not None:
                            break
            if req_date is None and ReasonCode.DATE_LATER in reasons:
                req_date = timestamp.date()
            if req_date is not None:
                facts_list.append(
                    TrustLabel(
                        fact="delivery.requestedDate",
                        value=req_date.isoformat(),
                        display=f"Requested delivery: {format_date(req_date)}",
                        level="VERIFIED",
                        source=TrustSource(
                            system="derived",
                            field="request.requestedDate",
                            retrievedAt=timestamp,
                        ),
                        method=None,
                        conflict=None,
                    )
                )
        filled_summary = _fill_summary_reason_templates(summary, reasons=reasons, facts=facts_list)
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
            facts=facts_list,
            summary=filled_summary,
            comment=comment,
        )
        return self.append(entry)

    def bind_offer_entries(self, offer_id: str, offer_version: int, proposal_id: str) -> None:
        """Attach `proposal_id` to the initial `OFFERED` entry for `(offer_id, offer_version)`."""
        bound_links: dict[str, AuditChainLink] = {}
        for idx, entry in enumerate(self._entries):
            if entry.offerId == offer_id and entry.offerVersion == offer_version and entry.proposalId is None:
                updated = entry.model_copy(update={"proposalId": proposal_id})
                self._entries[idx] = updated
                p_link = self._seal_entry_proposal(updated)
                if p_link is not None:
                    bound_links[updated.entryId] = p_link
        if self._session is not None:
            rows = self._session.scalars(
                select(DecisionLogRow).where(
                    DecisionLogRow.offer_id == offer_id,
                    DecisionLogRow.offer_version == offer_version,
                    DecisionLogRow.proposal_id.is_(None),
                )
            ).all()
            for row in rows:
                existing_meta = dict((row.data or {}).get("_chain") or {})
                clean = _strip_chain_meta(row.data)
                updated = AuditEntry.model_validate({**clean, "proposalId": proposal_id})
                p_link = bound_links.get(updated.entryId) or self._seal_entry_proposal(updated)
                if p_link is not None:
                    existing_meta["proposal"] = {
                        "prevHash": p_link.prevHash,
                        "entryHash": p_link.entryHash,
                    }
                row.proposal_id = proposal_id
                row.data = {**updated.model_dump(mode="json"), "_chain": existing_meta}
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

    def verify(
        self,
        *,
        proposal_id: str | None = None,
        customer_id: str | None = None,
    ) -> AuditChainVerification:
        """Verify the SHA-256 hash chain over the decision log (global, per-proposal, or per-customer)."""
        if proposal_id is not None and customer_id is not None:
            raise ValueError("Specify at most one of proposal_id or customer_id")

        current_entries = list(self.entries())
        if proposal_id is not None:
            scoped_entries = [e for e in current_entries if e.proposalId == proposal_id]
            sealed = list(self._sealed_by_proposal.get(proposal_id, ()))
            if self._session is not None:
                rows = self._session.scalars(
                    select(DecisionLogRow)
                    .where(DecisionLogRow.proposal_id == proposal_id)
                    .order_by(DecisionLogRow.seq)
                ).all()
                db_sealed: list[AuditChainLink] = []
                for idx, row in enumerate(rows):
                    meta = (row.data or {}).get("_chain", {}).get("proposal")
                    if meta and "prevHash" in meta and "entryHash" in meta:
                        db_sealed.append(
                            AuditChainLink(
                                index=idx,
                                entryId=row.entry_id,
                                prevHash=meta["prevHash"],
                                entryHash=meta["entryHash"],
                                expectedPrevHash=meta["prevHash"],
                                expectedEntryHash=meta["entryHash"],
                                valid=True,
                            )
                        )
                if db_sealed and len(db_sealed) == len(rows):
                    sealed = db_sealed
            return verify_hash_chain(scoped_entries, sealed)

        if customer_id is not None:
            scoped_entries = [e for e in current_entries if e.customerId == customer_id]
            sealed = list(self._sealed_by_customer.get(customer_id, ()))
            if self._session is not None:
                rows = self._session.scalars(
                    select(DecisionLogRow)
                    .where(DecisionLogRow.customer_id == customer_id)
                    .order_by(DecisionLogRow.seq)
                ).all()
                db_sealed = []
                for idx, row in enumerate(rows):
                    meta = (row.data or {}).get("_chain", {}).get("customer")
                    if meta and "prevHash" in meta and "entryHash" in meta:
                        db_sealed.append(
                            AuditChainLink(
                                index=idx,
                                entryId=row.entry_id,
                                prevHash=meta["prevHash"],
                                entryHash=meta["entryHash"],
                                expectedPrevHash=meta["prevHash"],
                                expectedEntryHash=meta["entryHash"],
                                valid=True,
                            )
                        )
                if db_sealed and len(db_sealed) == len(rows):
                    sealed = db_sealed
            return verify_hash_chain(scoped_entries, sealed)

        sealed = list(self._sealed_global)
        if self._session is not None:
            rows = self._session.scalars(select(DecisionLogRow).order_by(DecisionLogRow.seq)).all()
            db_sealed = []
            for idx, row in enumerate(rows):
                meta = (row.data or {}).get("_chain", {}).get("global")
                if meta and "prevHash" in meta and "entryHash" in meta:
                    db_sealed.append(
                        AuditChainLink(
                            index=idx,
                            entryId=row.entry_id,
                            prevHash=meta["prevHash"],
                            entryHash=meta["entryHash"],
                            expectedPrevHash=meta["prevHash"],
                            expectedEntryHash=meta["entryHash"],
                            valid=True,
                        )
                    )
            if db_sealed and len(db_sealed) == len(rows):
                sealed = db_sealed
        return verify_hash_chain(current_entries, sealed)

    verify_chain = verify
