"""P1.4 state machine and per-proposal decision log (patch P1.4, P6 P0-5, docs/API.md).

Every state transition appends an `AuditEntry` recording:
- what happened (`event`, `fromState`, `toState`, `summary`);
- who or what decided (`actor`: `gateway:*` or `human:Head Chef (link)`);
- which rule (`rule`, `reasons`);
- which facts (`facts` with their `TrustLabel`s).

No approval token, URL or QR payload may ever be stored in a decision-log entry (patch P3).
"""

import hashlib
import json
import re
from collections import OrderedDict, deque
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Lock
from types import MappingProxyType
from typing import Literal

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
from app.gateway.signing import SIGNED_AUDIT_EVENTS, GatewaySigner
from app.policy import Finding
from app.policy.limits import format_days

GENESIS_HASH: str = "0" * 64

AgentAccess = Literal["READ", "WRITE"]
AgentOutcome = Literal["OK", "REFUSED", "ERROR"]
_REFUSAL_WINDOW = timedelta(minutes=10)
_MAX_ACTIVITY_ENTRIES = 50
_CLIENT_LABEL = re.compile(r"[\w .:/()_+;,-]{1,120}\Z")
_PRIVATE_LABEL = re.compile(r"bearer\s|demo-(?:agent-|admin)|(?:atk|tk)-[\w-]+|/approve/", re.I)


def agent_client_label(value: object) -> str:
    """Store only a bounded plain-text client label, with credential-looking input discarded."""
    label = " ".join(value.split())[:120] if isinstance(value, str) else ""
    return label if _CLIENT_LABEL.fullmatch(label) and not _PRIVATE_LABEL.search(label) else "unknown"


class AgentActivityEntry(Contract):
    id: int
    timestamp: datetime
    client: str
    operation: str
    access: AgentAccess
    outcome: AgentOutcome
    durationMs: int
    reason: str | None = None


class SuspendedAgent(Contract):
    clientId: str
    client: str
    customerId: str


class AgentActivitySnapshot(Contract):
    paused: bool
    suspended: list[SuspendedAgent]
    entries: list[AgentActivityEntry]


class AgentActivity:
    """A sandbox-local activity feed. Control events are separate from proposal state transitions."""

    def __init__(self):
        self._lock = Lock()
        self._paused = False
        self._last_id = 0
        self._entries: deque[AgentActivityEntry] = deque(maxlen=_MAX_ACTIVITY_ENTRIES)
        self._refusals: dict[str, list[datetime]] = {}
        self._suspended: dict[str, SuspendedAgent] = {}
        self._mcp_clients: OrderedDict[tuple[str, str], str] = OrderedDict()

    @staticmethod
    def _client_id(customer: str, client: str) -> str:
        return hashlib.sha256(f"{customer}\0{client.casefold()}".encode()).hexdigest()

    def remember_mcp_client(self, session: str, customer: str, client: str) -> None:
        with self._lock:
            key = (session, customer)
            self._mcp_clients.setdefault(key, client)
            self._mcp_clients.move_to_end(key)
            if len(self._mcp_clients) > 200:
                self._mcp_clients.popitem(last=False)

    def mcp_client(self, session: str | None, customer: str, fallback: str) -> str:
        if session is None:
            return fallback
        with self._lock:
            key = (session, customer)
            if key in self._mcp_clients:
                self._mcp_clients.move_to_end(key)
            return self._mcp_clients.get(key, fallback)

    def block(self, customer: str | None, client: str, access: AgentAccess) -> tuple[str, str] | None:
        with self._lock:
            if customer and self._client_id(customer, client) in self._suspended:
                return "AGENT_SUSPENDED", "This agent client is suspended. Ask the Valio admin to resume it."
            if access == "WRITE" and self._paused:
                return "AGENTS_PAUSED", "Agent writes are paused by the Valio admin."
        return None

    def _append(self, timestamp: datetime, client: str, operation: str, access: AgentAccess,
                outcome: AgentOutcome, duration_ms: int = 0, reason: str | None = None) -> None:
        self._last_id += 1
        self._entries.appendleft(AgentActivityEntry(
            id=self._last_id, timestamp=timestamp, client=client, operation=operation,
            access=access, outcome=outcome, durationMs=duration_ms, reason=reason,
        ))

    def record(self, *, customer: str | None, client: str, operation: str, access: AgentAccess,
               outcome: AgentOutcome, timestamp: datetime, duration_ms: int,
               reason: str | None = None, count_refusal: bool = False) -> None:
        with self._lock:
            self._append(timestamp, client, operation, access, outcome, duration_ms, reason)
            if outcome != "REFUSED" or not count_refusal or customer is None:
                return
            client_id = self._client_id(customer, client)
            if client_id in self._suspended:
                return
            cutoff = timestamp - _REFUSAL_WINDOW
            self._refusals = {key: times for key, times in self._refusals.items() if max(times) > cutoff}
            times = [time for time in self._refusals.get(client_id, ()) if time > cutoff]
            times.append(timestamp)
            if len(times) < 3:
                self._refusals[client_id] = times
                return
            self._refusals.pop(client_id, None)
            self._suspended[client_id] = SuspendedAgent(clientId=client_id, client=client, customerId=customer)
            self._append(timestamp, client, "Agent suspended", "WRITE", "OK", reason="3 refusals within 10 minutes.")

    def set_paused(self, paused: bool, timestamp: datetime) -> None:
        with self._lock:
            if self._paused != paused:
                self._paused = paused
                self._append(timestamp, "Valio admin", "Agent writes paused" if paused else "Agent writes resumed",
                             "WRITE", "OK")

    def resume(self, client_id: str, timestamp: datetime) -> bool:
        with self._lock:
            suspended = self._suspended.pop(client_id, None)
            if suspended is None:
                return False
            self._append(timestamp, "Valio admin", "Agent resumed", "WRITE", "OK", reason=suspended.client)
            return True

    def snapshot(self) -> AgentActivitySnapshot:
        with self._lock:
            return AgentActivitySnapshot(
                paused=self._paused, suspended=list(self._suspended.values()), entries=list(self._entries),
            )


class AuditChainLink(Contract):
    """One link in the SHA-256 hash chain over the decision log (T28)."""

    index: int = Field(ge=0)
    entryId: str
    prevHash: str
    entryHash: str
    expectedPrevHash: str
    expectedEntryHash: str
    valid: bool


class SessionChainVerification(Contract):
    """Per-session hash-chain verification when aggregating across demo sessions (T93)."""

    sessionId: str
    verified: bool
    chainLength: int = Field(ge=0)
    genesisHash: str = GENESIS_HASH
    headHash: str = GENESIS_HASH
    tamperedEntryIds: list[str] = Field(default_factory=list)
    firstTamperedIndex: int | None = None
    reason: str | None = None
    links: list[AuditChainLink] = Field(default_factory=list)


class AuditChainVerification(Contract):
    """Verification result for the SHA-256 hash chain over a decision log (T28, T93)."""

    verified: bool
    chainLength: int = Field(ge=0)
    genesisHash: str = GENESIS_HASH
    headHash: str = GENESIS_HASH
    tamperedEntryIds: list[str] = Field(default_factory=list)
    firstTamperedIndex: int | None = None
    reason: str | None = None
    links: list[AuditChainLink] = Field(default_factory=list)
    sessions: list[SessionChainVerification] | None = None
    # T110: which chain was checked. `customerId` null with no `proposalId` means the full log (admin key).
    customerId: str | None = None
    proposalId: str | None = None


def public_session_id(sandbox_id: str) -> str:
    """Opaque session identifier safe to expose in read-only audit views (never echoes a cookie secret)."""
    if sandbox_id == "shared":
        return "shared"
    return f"ses-{hashlib.sha256(sandbox_id.encode()).hexdigest()[:12]}"


_UNHASHED_ENTRY_FIELDS: frozenset[str] = frozenset({"prevHash", "hash", "kid", "signature"})


def canonical_entry_json(entry: AuditEntry) -> str:
    """Canonical JSON serialization of an `AuditEntry` for SHA-256 hashing.

    `OFFER_CREATED` entries are logged at `getOffer` time with `proposalId=None` and later indexed
    under a `proposalId` by `bind_offer_entries`; normalising `proposalId=None` on `OFFER_CREATED`
    keeps the entry's hash invariant when bound so earlier links never need to be rewritten.
    """
    payload = {k: v for k, v in entry.model_dump(mode="json").items() if k not in _UNHASHED_ENTRY_FIELDS}
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
    committed_excl_vat: Decimal = Decimal(0),
    committed_days: Sequence[date] = (),
) -> str:
    """Readable summary of the gateway's policy decision for the decision log."""
    total_text = format_money(total_excl_vat)
    # T62: when the same delivery day already has committed orders, the limits apply to their sum.
    if committed_excl_vat > 0:
        days = committed_days or ((requested_date,) if requested_date else ())
        day = f" for {format_days(days)}" if days else ""
        total_text = (
            f"{format_money(committed_excl_vat)} already ordered or awaiting approval{day}; with this order "
            f"{format_money(committed_excl_vat + total_excl_vat)}"
        )
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


def _attach_chain_links(
    entries: Sequence[AuditEntry],
    links: Sequence[AuditChainLink],
) -> list[AuditEntry]:
    by_id = {lnk.entryId: lnk for lnk in links}
    out: list[AuditEntry] = []
    for idx, entry in enumerate(entries):
        lnk = links[idx] if idx < len(links) and links[idx].entryId == entry.entryId else by_id.get(entry.entryId)
        if lnk is not None:
            out.append(entry.model_copy(update={"prevHash": lnk.prevHash, "hash": lnk.entryHash}))
        else:
            out.append(entry)
    return out


class DecisionLog:
    """Append-only decision log for offers, proposals and mandate events (in-memory + optional SQLite)."""

    def __init__(self, session: Session | None = None, *, signer: GatewaySigner | None = None):
        self._session = session
        self._signer = signer
        self.agent_activity = AgentActivity()
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
        if self._signer is not None and entry.event in SIGNED_AUDIT_EVENTS and entry.signature is None:
            entry = entry.model_copy(
                update={
                    "kid": self._signer.kid,
                    "signature": self._signer.sign_audit_entry(entry),
                }
            )
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

    def _db_links(self, scope: str, *, proposal_id: str | None = None, customer_id: str | None = None) -> list[AuditChainLink] | None:
        if self._session is None:
            return None
        stmt = select(DecisionLogRow).order_by(DecisionLogRow.seq)
        if scope == "proposal" and proposal_id is not None:
            stmt = stmt.where(DecisionLogRow.proposal_id == proposal_id)
        elif scope == "customer" and customer_id is not None:
            stmt = stmt.where(DecisionLogRow.customer_id == customer_id)
        rows = self._session.scalars(stmt).all()
        db_sealed: list[AuditChainLink] = []
        for idx, row in enumerate(rows):
            meta = (row.data or {}).get("_chain", {}).get(scope)
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
        return db_sealed if db_sealed and len(db_sealed) == len(rows) else None

    def global_trail(self) -> AuditTrail:
        """Return the global ordered decision log with each entry's global `prevHash` and `hash`."""
        current = list(self.entries())
        links = self._db_links("global") or list(self._sealed_global)
        return AuditTrail(entries=_attach_chain_links(current, links))

    def for_proposal(self, proposal_id: str) -> AuditTrail:
        """Return the ordered decision log for `proposal_id` with per-proposal `prevHash` and `hash`."""
        scoped = [e for e in self.entries() if e.proposalId == proposal_id]
        links = self._db_links("proposal", proposal_id=proposal_id) or list(self._sealed_by_proposal.get(proposal_id, ()))
        return AuditTrail(entries=_attach_chain_links(scoped, links))

    def for_customer(self, customer_id: str) -> AuditTrail:
        """Return the ordered decision log for `customer_id` with per-customer `prevHash` and `hash`."""
        scoped = [e for e in self.entries() if e.customerId == customer_id]
        links = self._db_links("customer", customer_id=customer_id) or list(self._sealed_by_customer.get(customer_id, ()))
        return AuditTrail(entries=_attach_chain_links(scoped, links))

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


def aggregate_audit_trails(
    logs: Sequence[tuple[str, DecisionLog]],
    *,
    proposal_id: str | None = None,
    customer_id: str | None = None,
) -> AuditTrail:
    """Aggregate decision-log entries across active demo sessions in chronological order (T93)."""
    if proposal_id is not None and customer_id is not None:
        raise ValueError("Specify at most one of proposal_id or customer_id")
    indexed: list[tuple[datetime, int, int, AuditEntry]] = []
    for s_idx, (_sid, log) in enumerate(logs):
        if proposal_id is not None:
            entries = log.for_proposal(proposal_id).entries
        elif customer_id is not None:
            entries = log.for_customer(customer_id).entries
        else:
            entries = list(log.global_trail().entries)
        for e_idx, entry in enumerate(entries):
            indexed.append((entry.timestamp, s_idx, e_idx, entry))
    indexed.sort(key=lambda item: (item[0], item[1], item[2]))
    return AuditTrail(entries=[item[3] for item in indexed])


def aggregate_audit_verification(
    logs: Sequence[tuple[str, DecisionLog]],
    *,
    proposal_id: str | None = None,
    customer_id: str | None = None,
) -> AuditChainVerification:
    """Verify each session's SHA-256 hash chain independently and return the aggregated result (T93)."""
    if proposal_id is not None and customer_id is not None:
        raise ValueError("Specify at most one of proposal_id or customer_id")
    session_results: list[SessionChainVerification] = []
    combined_links: list[AuditChainLink] = []
    tampered_ids: list[str] = []
    first_tampered: int | None = None
    first_reason: str | None = None

    for raw_sid, log in logs:
        ver = log.verify(proposal_id=proposal_id, customer_id=customer_id)
        if ver.chainLength == 0 and ver.verified:
            continue
        pub_sid = public_session_id(raw_sid)
        offset = len(combined_links)
        if not ver.verified:
            if first_tampered is None:
                first_tampered = offset + (ver.firstTamperedIndex or 0)
            if first_reason is None and ver.reason:
                first_reason = f"[{pub_sid}] {ver.reason}"
            tampered_ids.extend(ver.tamperedEntryIds)
        for link in ver.links:
            combined_links.append(link.model_copy(update={"index": len(combined_links)}))
        session_results.append(
            SessionChainVerification(
                sessionId=pub_sid,
                verified=ver.verified,
                chainLength=ver.chainLength,
                genesisHash=ver.genesisHash,
                headHash=ver.headHash,
                tamperedEntryIds=list(ver.tamperedEntryIds),
                firstTamperedIndex=ver.firstTamperedIndex,
                reason=ver.reason,
                links=list(ver.links),
            )
        )

    if not session_results:
        head = GENESIS_HASH
    elif len(session_results) == 1:
        head = session_results[0].headHash
    else:
        head = hashlib.sha256(":".join(s.headHash for s in session_results).encode()).hexdigest()

    return AuditChainVerification(
        verified=all(s.verified for s in session_results),
        chainLength=sum(s.chainLength for s in session_results),
        genesisHash=GENESIS_HASH,
        headHash=head,
        tamperedEntryIds=tampered_ids,
        firstTamperedIndex=first_tampered,
        reason=first_reason,
        links=combined_links,
        sessions=session_results,
    )
