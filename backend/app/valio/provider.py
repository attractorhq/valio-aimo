"""Live and recorded sources expose the same trust-labelled per-product observation."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from app.contracts import Product, ReasonCode, TrustLabel

from .client import RawBatch, ValioClient
from .facts import AvailabilityFacts, PriceFacts, availability_facts, price_facts
from .vendor import aware


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


class LiveValioProvider:
    def __init__(self, client: ValioClient | None = None):
        self.client = client or ValioClient()

    def lookup(self, shortlists, requested_date, customer_id, *, products=None, force_refresh=False):
        return normalise_batch(self.client.fetch(
            shortlists, requested_date, customer_id, force_refresh=force_refresh,
        ), products)
