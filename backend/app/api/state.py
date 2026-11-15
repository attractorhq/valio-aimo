"""Shared supplier resources with bounded, per-visitor demo gateway state.

Built lazily on the first request so `/health` and `/docs` work before the catalogue exists. State lives in
memory and resets on restart/deploy (accepted, docs/WORKFLOW.md → Decisions).
"""

import os
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, RLock
from typing import Literal

from fastapi import HTTPException, Request

from app.catalog.build import ROOT_DIR, get_cached_catalog
from app.config import get_settings
from app.contracts import Product
from app.gateway.proposals import ProposalGateway, ProposalNotFoundError
from app.gateway.signing import default_signing_key
from app.policy import DEMO_MANDATES, MandateBook
from app.valio import LiveValioProvider, RecordedValioProvider

Mode = Literal["LIVE", "RECORDED"]
DEFAULT_FIXTURES_DIR = ROOT_DIR / "data/fixtures"
SANDBOX_COOKIE = "voa_sandbox"
SANDBOX_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
_EPHEMERAL_SIGNING_KEY = secrets.token_bytes(32)


class GatewayUnavailable(RuntimeError):
    """The gateway cannot serve orders yet (no catalogue, or neither a live key nor recorded fixtures)."""


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class GatewayState:
    gateway: ProposalGateway
    catalog: list[Product]
    mode: Mode
    clock: Callable[[], datetime] = utc_now
    proposal_ids: list[str] = field(default_factory=list)
    sandbox_id: str = "shared"

    def now_for(self, mode: str) -> datetime | None:
        """Live data runs on the wall clock. Recorded data runs frozen at its capture time (None = the
        gateway uses the offer/proposal's own timestamps), so a replayed demo never expires mid-flow."""
        return self.clock() if mode == "LIVE" else None

    def remember(self, proposal_id: str) -> None:
        if proposal_id not in self.proposal_ids:
            self.proposal_ids.append(proposal_id)


def latest_capture(fixtures: Path) -> Path | None:
    """The capture to replay: `fixtures` itself if it holds a manifest, else its newest
    `<capture-ts>/` subdirectory (the layout `scripts/record-fixtures` writes)."""
    if (fixtures / "manifest.json").is_file():
        return fixtures
    captures = sorted(p.parent for p in fixtures.glob("*/manifest.json"))
    return captures[-1] if captures else None


def build_state() -> GatewayState:
    settings = get_settings()
    try:
        artifact = get_cached_catalog()
    except (FileNotFoundError, OSError) as exc:
        raise GatewayUnavailable(
            "The catalogue artifact is not built (data/catalog/catalog.json). Run scripts/build-catalog."
        ) from exc
    capture = latest_capture(Path(os.environ.get("VALIO_FIXTURES_DIR") or DEFAULT_FIXTURES_DIR))
    # VALIO_MODE=recorded forces the recorded capture even when a key is set (P5.3 demo fallback).
    force_recorded = os.environ.get("VALIO_MODE", "").strip().lower() == "recorded"
    mode: Mode
    if settings.valio_api_key and not force_recorded:
        provider = LiveValioProvider(stale_fallback=True)  # T71: labelled, never AUTO, re-checked live
        mode = "LIVE"
    elif capture is not None:
        provider = RecordedValioProvider(capture)
        mode = "RECORDED"
    else:
        raise GatewayUnavailable(
            "No Valio API key and no recorded fixtures: set VALIO_API_KEY or provide data/fixtures/."
        )
    gateway = ProposalGateway(
        products=artifact.by_id(),
        mandates=MandateBook(DEMO_MANDATES),
        provider=provider,
        signing_key=sandbox_signing_key(),
    )
    return GatewayState(gateway=gateway, catalog=list(artifact.products), mode=mode)


def sandbox_signing_key() -> bytes:
    """Domain-separated capabilities/reset/receipt keys use the configured secret or a process-local key."""
    return default_signing_key()


def fresh_sandbox(resources: GatewayState, sandbox_id: str) -> GatewayState:
    gateway = ProposalGateway(
        mandates=MandateBook(m.model_copy(deep=True) for m in DEMO_MANDATES), provider=resources.gateway.provider,
        signing_key=sandbox_signing_key(),
    )
    # Do not copy the catalogue per visitor. Only mandates, stores, approvals and hash chains are private.
    gateway.products = resources.gateway.products
    return GatewayState(gateway, resources.catalog, resources.mode, resources.clock, sandbox_id=sandbox_id)


def reset_sandbox(state: GatewayState) -> None:
    """Discard every proposal (confirmed included), token, mandate edit and audit-chain link."""
    fresh = fresh_sandbox(state, state.sandbox_id)
    state.gateway = fresh.gateway
    state.proposal_ids.clear()


class SandboxRegistry:
    """Thread-safe LRU, at most 500 sandboxes, expiring after six idle hours (not recorded data time)."""

    def __init__(self, factory: Callable[[str], GatewayState], *, capacity=500, idle_seconds=6 * 60 * 60,
                 monotonic: Callable[[], float] = time.monotonic):
        if capacity < 1 or idle_seconds <= 0:
            raise ValueError("Sandbox capacity and idle expiry must be positive")
        self.factory, self.capacity, self.idle_seconds, self.monotonic = factory, capacity, idle_seconds, monotonic
        self._states: OrderedDict[str, tuple[float, GatewayState]] = OrderedDict()
        self._lock = RLock()

    def _expire(self, now: float) -> None:
        while self._states and next(iter(self._states.values()))[0] + self.idle_seconds <= now:
            self._states.popitem(last=False)

    def get(self, sandbox_id: str, *, create=True) -> GatewayState:
        with self._lock:
            now = self.monotonic()
            self._expire(now)
            entry = self._states.pop(sandbox_id, None)
            if entry is None:
                if not create:
                    raise HTTPException(404, "This session expired; request a new proposal.")
                state = self.factory(sandbox_id)
                if len(self._states) >= self.capacity:
                    self._states.popitem(last=False)
            else:
                state = entry[1]
            self._states[sandbox_id] = (now, state)
            return state

    def count(self) -> int:
        with self._lock:
            self._expire(self.monotonic())
            return len(self._states)

    def snapshots(self) -> list[tuple[str, GatewayState]]:
        """Read-only snapshot of active `(sandbox_id, state)` pairs without mutating LRU order."""
        with self._lock:
            self._expire(self.monotonic())
            return [(sid, state) for sid, (_last_used, state) in self._states.items()]

    def reset_all(self) -> int:
        with self._lock:
            count = len(self._states)
            for _last_used, state in self._states.values():
                reset_sandbox(state)
            self._states.clear()
            return count


# T116: sessions the gateway issues to cookieless API clients (`api-…`) and MCP sessions (`mcp-…`) live in their own
# LRU, so a client that never echoes its session id cannot evict browser sessions.
AGENT_SESSION_PREFIXES = ("api-", "mcp-")
SESSION_HEADER = "X-Demo-Sandbox"


def is_agent_session(sandbox_id: str) -> bool:
    return sandbox_id.startswith(AGENT_SESSION_PREFIXES)


def new_agent_session_id() -> str:
    return "api-" + secrets.token_hex(16)


class SessionRegistries:
    """Browser/explicit sessions and agent sessions behind one `SandboxRegistry`-like interface."""

    def __init__(self, browsers: SandboxRegistry, agents: SandboxRegistry):
        self.browsers, self.agents = browsers, agents

    def _for(self, sandbox_id: str) -> SandboxRegistry:
        return self.agents if is_agent_session(sandbox_id) else self.browsers

    @property
    def factory(self) -> Callable[[str], GatewayState]:
        return self.browsers.factory

    def get(self, sandbox_id: str, *, create=True) -> GatewayState:
        return self._for(sandbox_id).get(sandbox_id, create=create)

    def count(self) -> int:
        return self.browsers.count() + self.agents.count()

    def snapshots(self) -> list[tuple[str, GatewayState]]:
        return [*self.browsers.snapshots(), *self.agents.snapshots()]

    def reset_all(self) -> int:
        return self.browsers.reset_all() + self.agents.reset_all()


_state: GatewayState | None = None  # resource template only; never used for visitor mutations
_registry: SessionRegistries | None = None
_lock = Lock()


def sandbox_registry() -> SessionRegistries:
    global _state, _registry
    with _lock:
        if _state is None:
            _state = build_state()
            _registry = None
        if _registry is None:
            resources = _state

            def factory(sandbox_id: str) -> GatewayState:
                return fresh_sandbox(resources, sandbox_id)

            _registry = SessionRegistries(
                SandboxRegistry(factory),
                SandboxRegistry(factory, capacity=300, idle_seconds=2 * 60 * 60),
            )
        return _registry


def sandbox_count() -> int:
    return _registry.count() if _registry is not None else 0


def iter_audit_sessions(current: GatewayState) -> list[tuple[str, GatewayState]]:
    """Active `(sandbox_id, state)` pairs for `scope=all` read-only audit queries (T93)."""
    sessions = _registry.snapshots() if _registry is not None else []
    if all(state is not current for _, state in sessions):
        sessions.append((current.sandbox_id, current))
    return sessions


def find_proposal_state(current: GatewayState, proposal_id: str, *, all_sessions: bool = False) -> GatewayState:
    """Resolve the `GatewayState` holding `proposal_id` (current session only, or across active sessions)."""
    if not all_sessions:
        current.gateway.get_proposal(proposal_id)
        return current
    for _, candidate in iter_audit_sessions(current):
        try:
            candidate.gateway.get_proposal(proposal_id)
            return candidate
        except ProposalNotFoundError:
            continue
    raise ProposalNotFoundError(proposal_id)


def request_sandbox_id(request: Request) -> str | None:
    header = request.headers.get("x-demo-sandbox")
    if header is not None:
        if not SANDBOX_ID_RE.fullmatch(header):
            raise HTTPException(400, "X-Demo-Sandbox must be 1–100 letters, digits, underscores or hyphens.")
        return header
    cookie = getattr(request.state, "demo_sandbox", None) or request.cookies.get(SANDBOX_COOKIE)
    return cookie if cookie and SANDBOX_ID_RE.fullmatch(cookie) else None


_ID_FIELDS = ("offerId", "proposalId")


def _key_customer(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return {f"demo-agent-{m.customerId}": m.customerId for m in DEMO_MANDATES}.get(token.strip())


async def _named_ids(request: Request) -> dict[str, set[str]]:
    """offerId / proposalId the request names: path, query, JSON body and MCP tool arguments."""
    ids: dict[str, set[str]] = {name: set() for name in _ID_FIELDS}

    def take(source) -> None:
        if isinstance(source, dict):
            for name in _ID_FIELDS:
                if isinstance(value := source.get(name), str) and SANDBOX_ID_RE.fullmatch(value):
                    ids[name].add(value)

    take(dict(request.path_params))
    take(dict(request.query_params))
    if request.method in ("POST", "PUT", "PATCH") and "json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
        except ValueError:
            body = None
        for message in body if isinstance(body, list) else [body]:
            take(message)
            if isinstance(message, dict) and isinstance(params := message.get("params"), dict):
                take(params.get("arguments"))
    return ids


def _owning_session(registry, customer: str, ids: dict[str, set[str]]) -> str | None:
    """T116: the one agent session holding an offer/proposal this key's buyer owns, or None (absent or ambiguous).

    Offer and proposal ids are unguessable, so a header-less client that names one is the client that created it
    (or was given it by that buyer); another buyer's key never resolves it."""
    agents = getattr(registry, "agents", registry)
    matches: set[str] = set()
    for sandbox_id, state in agents.snapshots():
        if not is_agent_session(sandbox_id):
            continue
        gateway = state.gateway
        for (offer_id, _version), stored in list(getattr(gateway, "_offers", {}).items()):
            if offer_id in ids["offerId"] and stored.offer.customerId == customer:
                matches.add(sandbox_id)
        for proposal_id in ids["proposalId"]:
            try:
                if gateway.get_proposal(proposal_id).customerId == customer:
                    matches.add(sandbox_id)
            except ProposalNotFoundError:
                continue
    return next(iter(matches)) if len(matches) == 1 else None


async def try_state(request: Request) -> GatewayState | GatewayUnavailable:
    """For /health: report an unconfigured gateway instead of failing. Tests override it like get_state."""
    try:
        return await get_state(request)
    except GatewayUnavailable as exc:
        return exc


async def get_state(request: Request) -> GatewayState:
    """FastAPI dependency: which private session (sandbox) serves this request.

    - Browsers get a cookie session. `X-Demo-Sandbox` picks a session explicitly (any channel).
    - MCP clients use their `Mcp-Session-Id` (issued on `initialize`).
    - T116: any other cookieless client gets a fresh private session, returned in the `X-Demo-Sandbox` response
      header. Echoing it keeps later calls (offer → proposal, REST ↔ MCP) in that session; two clients never
      share totals, proposals or suspensions unless they send the same id.
    """
    sandbox_id = request_sandbox_id(request)
    if sandbox_id is None and request.url.path.rstrip("/") == "/mcp":
        session_id = request.headers.get("mcp-session-id")
        if session_id is None and request.method == "POST":
            try:
                body = await request.json()
            except ValueError:
                body = None
            messages = body if isinstance(body, list) else [body]
            if any(isinstance(message, dict) and message.get("method") == "initialize" for message in messages):
                session_id = "mcp-" + secrets.token_hex(16)
                request.state.mcp_session_id = session_id
        if session_id is not None and (not SANDBOX_ID_RE.fullmatch(session_id) or len(session_id) > 90):
            raise HTTPException(400, "Invalid MCP session id.")
        if session_id is not None:
            sandbox_id = session_id if is_agent_session(session_id) else "mcp-" + session_id
    registry = sandbox_registry()
    cookie_session = sandbox_id is not None and request.headers.get("x-demo-sandbox") is None and (
        request.url.path.rstrip("/") != "/mcp")
    if sandbox_id is None and (customer := _key_customer(request)) is not None:
        # T116: generic HTTP helpers do not echo X-Demo-Sandbox. A call naming its own offer/proposal continues in
        # the session that holds it, so offer → proposal works header-less too.
        ids = await _named_ids(request)
        if any(ids.values()):
            sandbox_id = _owning_session(registry, customer, ids)
    if sandbox_id is None:
        sandbox_id = new_agent_session_id()
        request.state.response_sandbox = sandbox_id
        if request.method in ("GET", "HEAD", "OPTIONS"):
            # A read in a brand-new session sees only the shipped state; do not keep an empty session for it.
            # Echoing the id later creates the same fresh session.
            return registry.factory(sandbox_id)
    if not cookie_session:
        request.state.response_sandbox = sandbox_id
    return registry.get(sandbox_id)
