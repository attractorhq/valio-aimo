"""Live and recorded sources expose the same trust-labelled per-product observation."""

import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Literal
from zoneinfo import ZoneInfo

from app.contracts import Product, ReasonCode, TrustLabel

from .client import RawBatch, ValioClient, decode, product_ids, utc_now
from .errors import IntegrationError
from .facts import AvailabilityFacts, PriceFacts, availability_facts, price_facts
from .vendor import aware

HELSINKI = ZoneInfo("Europe/Helsinki")


@dataclass(frozen=True)
class ProductFacts:
    product_id: str
    price: PriceFacts
    availability: AvailabilityFacts

    @property
    def facts(self) -> tuple[TrustLabel, ...]:
        return self.price.facts + self.availability.facts

    @property
    def reasons(self) -> tuple[ReasonCode, ...]:
        return tuple(dict.fromkeys(self.price.reasons + self.availability.reasons))


@dataclass(frozen=True)
class ValioSnapshot:
    customer_id: str
    requested_date: date
    products: dict[str, ProductFacts]
    now: datetime
    captured_at: datetime
    mode: Literal["LIVE", "RECORDED"]
    recorded_at: datetime | None


def normalise_batch(batch: RawBatch, products: dict[str, Product] | None = None) -> ValioSnapshot:
    price, availability = batch.envelopes()
    prices = {row["productId"]: row for row in price.products}
    stock = {row["productId"]: row for row in availability.products}
    now = aware(availability.timeNow)
    return ValioSnapshot(
        batch.customer_id, batch.requested_date,
        {code: ProductFacts(
            code, price_facts(prices[code], price.pricingDate, batch.price_retrieved_at),
            availability_facts(stock[code], batch.requested_date, now, (products or {}).get(code)),
        ) for code in batch.product_ids},
        now, batch.captured_at, batch.mode, batch.recorded_at,
    )


STALE_WINDOW = timedelta(minutes=15)
STALE_METHOD = "stale fallback"


@dataclass(frozen=True)
class _Cached:
    """The last successful price and availability rows for one product, with their envelopes."""

    upstream_customer_id: str
    price_envelope: dict
    price_row: dict
    availability_envelope: dict
    availability_row: dict
    retrieved_at: datetime


def _stale(label: TrustLabel, cached_at: datetime) -> TrustLabel:
    """A cached fact is an estimate of today's value, never verified: UNKNOWN stays UNKNOWN."""
    level = label.level if label.level == "UNKNOWN" else "ESTIMATE"
    clock = cached_at.astimezone(HELSINKI).strftime("%H:%M")
    return label.model_copy(update={
        "level": level,
        "method": f"{STALE_METHOD} · Valio API, cached at {clock}",
        "source": label.source.model_copy(update={"retrievedAt": cached_at}),
    })


def mark_stale(snapshot: ValioSnapshot, cached_at: datetime) -> ValioSnapshot:
    """T71: every price and availability fact of a snapshot built from the cache is labelled as such."""
    products = {
        code: ProductFacts(
            code,
            replace(facts.price, facts=tuple(_stale(f, cached_at) for f in facts.price.facts)),
            replace(facts.availability, facts=tuple(_stale(f, cached_at) for f in facts.availability.facts)),
        )
        for code, facts in snapshot.products.items()
    }
    return replace(snapshot, products=products)


class LiveValioProvider:
    """Live reads. With `stale_fallback`, the last successful rows (≤ 15 min old) answer a failed read (T71),
    labelled ESTIMATE / "stale fallback" so the policy never decides AUTO on them. A forced refresh (the re-check
    before confirmation) never falls back: it confirms only from a live read."""

    def __init__(self, client: ValioClient | None = None, *, stale_fallback: bool = False,
                 clock=utc_now, stale_window: timedelta = STALE_WINDOW):
        self.client = client or ValioClient()
        self.stale_fallback = stale_fallback
        self.clock = clock
        self.stale_window = stale_window
        self._last: dict[tuple[str, date, str], _Cached] = {}
        self._lock = Lock()

    def lookup(self, shortlists, requested_date, customer_id, *, products=None, force_refresh=False):
        try:
            batch = self.client.fetch(shortlists, requested_date, customer_id, force_refresh=force_refresh)
        except IntegrationError:
            if not self.stale_fallback or force_refresh:
                raise
            stale = self._stale_batch(shortlists, requested_date, customer_id)
            if stale is None:
                raise
            return mark_stale(normalise_batch(stale, products), stale.captured_at)
        if self.stale_fallback:
            self._remember(batch)
        return normalise_batch(batch, products)

    def _remember(self, batch: RawBatch) -> None:
        price, availability = (decode(batch.price_body), decode(batch.availability_body))
        prices = {row["productId"]: row for row in price["products"]}
        stock = {row["productId"]: row for row in availability["products"]}
        price_meta = {k: v for k, v in price.items() if k != "products"}
        stock_meta = {k: v for k, v in availability.items() if k != "products"}
        with self._lock:
            for code in batch.product_ids:
                if code in prices and code in stock:
                    self._last[(batch.customer_id, batch.requested_date, code)] = _Cached(
                        batch.upstream_customer_id, price_meta, prices[code], stock_meta, stock[code],
                        batch.price_retrieved_at,
                    )

    def _stale_batch(self, shortlists, requested_date, customer_id) -> RawBatch | None:
        """Every shortlisted product from one fresh-enough copy, or None (then the original error stands)."""
        ids = product_ids(shortlists)
        oldest_allowed = self.clock() - self.stale_window
        with self._lock:
            cached = [self._last.get((customer_id, requested_date, code)) for code in ids]
        if any(c is None or c.retrieved_at < oldest_allowed for c in cached):
            return None
        newest = max(cached, key=lambda c: c.retrieved_at)
        oldest = min(c.retrieved_at for c in cached)
        price = {**newest.price_envelope, "products": [c.price_row for c in cached]}
        availability = {**newest.availability_envelope, "products": [c.availability_row for c in cached]}
        return RawBatch(
            customer_id, newest.upstream_customer_id, requested_date, ids,
            json.dumps(price, default=str).encode(), json.dumps(availability, default=str).encode(),
            oldest, oldest,
        )
