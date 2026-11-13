"""Process-wide gateway wiring: catalogue, mandates, Valio source and the proposal state machine.

Built lazily on the first request so `/health` and `/docs` work before the catalogue exists. State lives in
memory and resets on restart/deploy (accepted, docs/WORKFLOW.md → Decisions).
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal

from app.catalog.build import ROOT_DIR, get_cached_catalog
from app.config import get_settings
from app.contracts import Product
from app.gateway.proposals import ProposalGateway
from app.policy import DEMO_MANDATES, MandateBook
from app.valio import LiveValioProvider, RecordedValioProvider

Mode = Literal["LIVE", "RECORDED"]
DEFAULT_FIXTURES_DIR = ROOT_DIR / "data/fixtures"


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
        provider = LiveValioProvider()
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
        signing_key=os.environ.get("APPROVAL_SIGNING_KEY") or None,
    )
    return GatewayState(gateway=gateway, catalog=list(artifact.products), mode=mode)


_state: GatewayState | None = None
_lock = Lock()


def try_state() -> GatewayState | GatewayUnavailable:
    """For /health: report an unconfigured gateway instead of failing. Tests override it like get_state."""
    try:
        return get_state()
    except GatewayUnavailable as exc:
        return exc


def get_state() -> GatewayState:
    """FastAPI dependency. Tests override it with a synthetic state."""
    global _state
    if _state is None:
        with _lock:
            if _state is None:
                _state = build_state()
    return _state
