"""Shortlist-only, bounded, authenticated reads; no redirects, bodies, keys or headers in errors."""

import json
import math
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Literal

import httpx
from pydantic import ValidationError

from app.config import Settings, get_settings

from .errors import IntegrationError, ValioRateLimited
from .vendor import HELSINKI, AvailabilityEnvelope, PriceEnvelope, aware


def utc_now() -> datetime:
    return datetime.now(UTC)


class ValioRateLimiter:
    """Process-wide, thread-safe one-token bucket: 5/s, initially empty, no bursts."""

    def __init__(self, *, monotonic=time.monotonic, sleep=time.sleep):
        self.monotonic, self.sleep = monotonic, sleep
        self._lock = Lock()
        self._next_request_at = monotonic() + 0.2
        self._blocked_until = 0.0

    def _check_cooldown(self, now: float) -> None:
        if now < self._blocked_until:
            raise ValioRateLimited(math.ceil(self._blocked_until - now))

    def check_cooldown(self) -> None:
        with self._lock:
            self._check_cooldown(self.monotonic())

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self.monotonic()
                self._check_cooldown(now)
                wait = self._next_request_at - now
                if wait <= 0:
                    self._next_request_at = now + 0.2
                    return
            # Never hold the lock while waiting: a concurrent 429 must publish its cooldown.
            self.sleep(wait)

    def block(self, seconds: float) -> None:
        with self._lock:
            now = self.monotonic()
            self._blocked_until = max(self._blocked_until, now + max(30.0, seconds))
            self._check_cooldown(now)


_RATE_LIMITER = ValioRateLimiter()


def cooldown_seconds(retry_after: str, now: datetime) -> float:
    """Honour both Retry-After forms without trusting invalid/nonfinite header values."""
    try:
        delay = float(retry_after)
    except ValueError:
        try:
            delay = (aware(parsedate_to_datetime(retry_after)) - aware(now)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 30.0
    return max(30.0, delay) if math.isfinite(delay) else 30.0


def product_ids(shortlists: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    if not isinstance(shortlists, Mapping) or not shortlists:
        raise IntegrationError("At least one item shortlist is required.")
    ids: set[str] = set()
    for codes in shortlists.values():
        if not isinstance(codes, Sequence) or isinstance(codes, str) or not codes or len(codes) > 20:
            raise IntegrationError("Each item requires 1–20 shortlisted product IDs.")
        if any(not isinstance(code, str) or not code.strip() for code in codes):
            raise IntegrationError("Shortlisted product IDs must be nonempty strings.")
        ids.update(codes)
    if len(ids) > 1000:
        raise IntegrationError("A batch may contain at most 1,000 unique products.")
    return tuple(sorted(ids))


def decode(body: bytes) -> dict:
    try:
        def reject_constant(_):
            raise ValueError("Non-finite JSON number")

        result = json.loads(body, parse_float=Decimal, parse_constant=reject_constant)
        if not isinstance(result, dict):
            raise ValueError("Expected an object")
        return result
    except (ValueError, UnicodeError):
        raise IntegrationError("Supplier returned invalid JSON; retry the inquiry.") from None


def validate_envelope(path: str, payload: dict, customer: str, day: date, ids: Sequence[str]):
    try:
        if path == "/api/price":
            result = PriceEnvelope.model_validate(payload)
            if (result.customerId != customer or result.currency != "EUR"
                    or aware(result.pricingDate).astimezone(HELSINKI).date() != day):
                raise ValueError("Unbound price batch")
        else:
            result = AvailabilityEnvelope.model_validate(payload)
            if aware(result.requestedDate).astimezone(HELSINKI).date() != day:
                raise ValueError("Unbound availability batch")
        actual = [row.get("productId") for row in result.products]
        if any(not isinstance(code, str) for code in actual):
            raise ValueError("Invalid product identity")
        if len(actual) != len(ids) or set(actual) != set(ids):
            raise ValueError("Missing, duplicate or unexpected IDs")
        return result
    except (ValueError, ValidationError):
        raise IntegrationError("Supplier batch has invalid customer, currency, date or product binding.") from None


def validate_binding(price: dict, availability: dict, customer: str, day: date, ids: Sequence[str]):
    return (validate_envelope("/api/price", price, customer, day, ids),
            validate_envelope("/api/availability", availability, customer, day, ids))


@dataclass(frozen=True)
class RawBatch:
    customer_id: str
    upstream_customer_id: str
    requested_date: date
    product_ids: tuple[str, ...]
    price_body: bytes
    availability_body: bytes
    price_retrieved_at: datetime
    captured_at: datetime
    mode: Literal["LIVE", "RECORDED"] = "LIVE"
    recorded_at: datetime | None = None

    def envelopes(self):
        return validate_binding(
            decode(self.price_body), decode(self.availability_body),
            self.upstream_customer_id, self.requested_date, self.product_ids,
        )


class ValioClient:
    def __init__(
        self, settings: Settings | None = None, *, transport: httpx.BaseTransport | None = None,
        ttl_seconds: float = 45, max_retries: int = 2, max_cache_entries: int = 128,
        clock: Callable[[], datetime] = utc_now, monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rate_limiter: ValioRateLimiter | None = None,
    ):
        if not 30 <= ttl_seconds <= 60 or not 0 <= max_retries <= 4 or max_cache_entries < 1:
            raise ValueError("Use a 30–60 second TTL, 0–4 retries and a positive cache capacity.")
        self.settings = settings or get_settings()
        self.transport = transport
        self.ttl_seconds = ttl_seconds
        self.max_retries = max_retries
        self.max_cache_entries = max_cache_entries
        self.clock, self.monotonic, self.sleep = clock, monotonic, sleep
        self.rate_limiter = rate_limiter if rate_limiter is not None else _RATE_LIMITER
        self._cache: OrderedDict[tuple, tuple[float, bytes, datetime]] = OrderedDict()
        self._cache_lock = Lock()

    def _post(self, path, customer, day, ids, *, force_refresh=False):
        if not self.settings.valio_api_key:
            raise IntegrationError("VALIO_API_KEY is required for live challenge API reads.")
        self.rate_limiter.check_cooldown()
        key = (path, customer, day, ids)
        with self._cache_lock:
            cached = self._cache.get(key)
            if not force_refresh and cached is not None and self.monotonic() < cached[0]:
                self._cache.move_to_end(key)
                return cached[1], cached[2]
        if path not in ("/api/price", "/api/availability"):
            raise IntegrationError("Only shortlisted price and availability reads are supported.")
        try:
            with httpx.Client(
                base_url=self.settings.valio_base_url, transport=self.transport,
                timeout=self.settings.valio_timeout_seconds, follow_redirects=False,
            ) as client:
                for attempt in range(self.max_retries + 1):
                    self.rate_limiter.acquire()
                    response = client.post(
                        path, headers={"X-API-Key": self.settings.valio_api_key},
                        json={"customer": customer, "requestedDate": day.isoformat(), "products": list(ids)},
                    )
                    if response.status_code == 200:
                        # Validate JSON before caching; never cache error responses or credentials.
                        validate_envelope(path, decode(response.content), customer, day, ids)
                        retrieved_at = aware(self.clock())
                        value = (self.monotonic() + self.ttl_seconds, response.content, retrieved_at)
                        with self._cache_lock:
                            self._cache[key] = value
                            self._cache.move_to_end(key)
                            while len(self._cache) > self.max_cache_entries:
                                self._cache.popitem(last=False)
                        return value[1], value[2]
                    if response.status_code == 429:
                        self.rate_limiter.block(cooldown_seconds(response.headers.get("Retry-After", ""), self.clock()))
                    if 500 <= response.status_code < 600 and attempt < self.max_retries:
                        try:
                            wait = min(5.0, max(0.0, float(response.headers.get("Retry-After", ""))))
                            if wait != wait:
                                raise ValueError("NaN delay")
                        except ValueError:
                            wait = 0.25 * 2 ** attempt
                        self.sleep(wait)
                        continue
                    raise IntegrationError(f"Valio {path} returned HTTP {response.status_code}; no fallback used.")
        except httpx.HTTPError:
            raise IntegrationError("Valio read failed; retry the inquiry. No fallback was used.") from None

    def fetch(self, shortlists, requested_date: date, customer_id: str, *, force_refresh=False) -> RawBatch:
        ids = product_ids(shortlists)
        if not isinstance(customer_id, str) or not customer_id.strip():
            raise IntegrationError("A customer identity is required.")
        customer = self.settings.valio_customer_ids.get(customer_id, self.settings.valio_customer_id)
        price, retrieved_at = self._post("/api/price", customer, requested_date, ids, force_refresh=force_refresh)
        availability, _ = self._post("/api/availability", customer, requested_date, ids, force_refresh=force_refresh)
        result = RawBatch(
            customer_id, customer, requested_date, ids, price, availability, retrieved_at, aware(self.clock()),
        )
        result.envelopes()
        return result


def subset_batch(batch: RawBatch, ids: tuple[str, ...]) -> RawBatch:
    """Project a captured batch onto a shortlist, retaining capture-time evidence."""
    from dataclasses import replace

    def subset(body):
        payload = deepcopy(decode(body))
        payload["products"] = [row for row in payload["products"] if row["productId"] in ids]
        return json.dumps(payload, default=str).encode()

    return replace(batch, product_ids=ids, price_body=subset(batch.price_body),
                   availability_body=subset(batch.availability_body))
