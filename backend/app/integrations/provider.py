from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from app.schemas import ProductOut


class IntegrationError(Exception):
    pass


@dataclass(frozen=True)
class PriceCheck:
    unit_price: Decimal
    currency: str
    source: str
    verified_at: datetime


@dataclass(frozen=True)
class AvailabilityCheck:
    available_quantity: Decimal | None
    earliest_delivery: date | None
    source: str
    verified_at: datetime
    status: str = "CONFIRMED"
    note: str | None = None
    sales_unit: str | None = None
    order_cutoff: datetime | None = None
    logistics_model: str | None = None


class ValioProvider(Protocol):
    mock: bool

    def search_products(self, products: list[ProductOut], query: str) -> list[ProductOut]: ...
    def get_prices(
        self, codes: list[str], delivery_date: date, buyer_id: str | None = None
    ) -> dict[str, PriceCheck]: ...
    def check_availability(
        self, codes: list[str], delivery_date: date, buyer_id: str | None = None
    ) -> dict[str, AvailabilityCheck]: ...
