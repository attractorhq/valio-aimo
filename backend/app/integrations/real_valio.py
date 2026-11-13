"""Typed adapter for the authenticated Junction Valio contract, verified 2026-11-13.

These are live calls to the challenge's synthetic backend, NOT production Valio systems.
The supplier contract has no order endpoint. Never silently fall back to local fixtures.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings, get_settings
from app.integrations.provider import AvailabilityCheck, IntegrationError, PriceCheck

HELSINKI = ZoneInfo("Europe/Helsinki")
Amount = Field(ge=0, allow_inf_nan=False)


class VendorModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class VendorPrice(VendorModel):
    value: Decimal = Amount
    valueWithTax: Decimal = Amount
    validFrom: datetime
    validTo: datetime
    currency: str


class VendorProductPrice(VendorModel):
    productId: str
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    catalogPrice: VendorPrice | None = None
    netPriceFound: bool
    error: bool


class PriceResponse(VendorModel):
    customerId: str
    currency: str
    pricingDate: datetime
    products: list[VendorProductPrice]


class ScheduleLine(VendorModel):
    salesBlock: bool
    availableAmount: Decimal = Amount
    availableDate: date | None = None
    salesUnit: str


class VendorAvailability(VendorModel):
    productId: str
    closingTime: datetime | None = None
    salesBlock: bool
    logisticModel: str | None = None
    scheduleLines: list[ScheduleLine]


class AvailabilityResponse(VendorModel):
    timeNow: datetime
    requestedDate: datetime
    products: list[VendorAvailability]


def aware(value: datetime) -> datetime:
    # Contract explicitly defines server times as Europe/Helsinki.
    return value.replace(tzinfo=HELSINKI) if value.tzinfo is None else value


class RealValioProvider:
    mock = False

    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        self.transport = transport

    def search_products(self, products, query):
        terms = query.casefold().replace("-", " ").split()
        return [
            p
            for p in products
            if all(
                t
                in f"{p.name} {' '.join(p.names.values())} {p.code} {p.category}".casefold().replace("-", " ")
                for t in terms
            )
        ]

    def _post(self, path, codes, delivery_date, buyer_id, model):
        if not self.settings.valio_api_key:
            raise IntegrationError("VALIO_API_KEY is required for live challenge API reads.")
        customer = self.settings.valio_customer_ids.get(buyer_id, self.settings.valio_customer_id)
        if not codes or len(codes) > 1000 or len(set(codes)) != len(codes):
            raise IntegrationError("Valio requests require 1–1,000 unique shortlisted product codes.")
        try:
            # No redirects for dynamic operations: never forward credentials to another host.
            with httpx.Client(
                base_url=self.settings.valio_base_url,
                timeout=self.settings.valio_timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    path,
                    headers={"X-API-Key": self.settings.valio_api_key},
                    json={
                        "customer": customer,
                        "requestedDate": delivery_date.isoformat(),
                        "products": codes,
                    },
                )
            if response.status_code != 200:
                raise IntegrationError(
                    f"Valio {path} returned HTTP {response.status_code}; no mock fallback was used."
                )
            result = model.model_validate(json.loads(response.text, parse_float=Decimal))
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise IntegrationError(
                f"Valio {path} failed or returned invalid data; retry the inquiry."
            ) from exc
        actual = [p.productId for p in result.products]
        if len(actual) != len(codes) or set(actual) != set(codes):
            raise IntegrationError(f"Valio {path} returned missing, duplicate or unexpected product codes.")
        return result, customer

    def get_prices(self, codes, delivery_date, buyer_id=None):
        response, customer = self._post("/api/price", codes, delivery_date, buyer_id, PriceResponse)
        if (
            response.currency != "EUR"
            or response.customerId != customer
            or response.pricingDate.date() != delivery_date
        ):
            raise IntegrationError(
                "Valio price customer, currency or pricing date does not match the inquiry."
            )
        checks = {}
        for p in response.products:
            price = p.catalogPrice
            if (
                p.error
                or price is None
                or price.currency != "EUR"
                or p.quantity != 1
                or not price.validFrom.date() <= delivery_date <= price.validTo.date()
            ):
                raise IntegrationError(
                    f"No valid EUR sales-unit price for product {p.productId} on the requested date."
                )
            checks[p.productId] = PriceCheck(
                unit_price=price.value,
                currency="EUR",
                verified_at=datetime.now(UTC),
                source="Valio Junction API /api/price (synthetic challenge backend; net price per supplier sales unit)",
            )
        return checks

    def check_availability(self, codes, delivery_date, buyer_id=None):
        response, _ = self._post("/api/availability", codes, delivery_date, buyer_id, AvailabilityResponse)
        if response.requestedDate.date() != delivery_date:
            raise IntegrationError("Valio availability response does not match the requested delivery date.")
        verified = aware(response.timeNow)
        checks = {}
        for p in response.products:
            if len(p.scheduleLines) > 1:
                raise IntegrationError(
                    "Valio returned multiple schedule lines; contract expects at most one."
                )
            line = p.scheduleLines[0] if p.scheduleLines else None
            cutoff = aware(p.closingTime) if p.closingTime else None
            amount = line.availableAmount if line else Decimal(0)
            earliest = line.availableDate if line else None
            status, note = (
                "CONFIRMED",
                "Supplier stock checked for the requested delivery date; not reserved.",
            )
            if p.salesBlock or (line and line.salesBlock):
                status, note, amount = "UNAVAILABLE", "Supplier sales block prevents ordering.", Decimal(0)
            elif not line or amount == 0 or not earliest or earliest > delivery_date:
                status, note = "UNAVAILABLE", "No sufficient delivery schedule for the requested date."
            elif cutoff is not None and cutoff < verified:
                status, note, amount = "UNAVAILABLE", "Supplier order cut-off has passed.", Decimal(0)
            elif p.logisticModel != "Stock":
                # Terminal's 999999 is an explicitly documented unknown-stock sentinel.
                status, note, amount = (
                    "UNKNOWN",
                    "Terminal/supplier stock quantity is not confirmed; placeholder is not inventory.",
                    None,
                )
            elif cutoff is None:
                status, note = (
                    "UNKNOWN",
                    "Supplier order cut-off is unknown; ordering eligibility is not confirmed.",
                )
            checks[p.productId] = AvailabilityCheck(
                available_quantity=amount,
                earliest_delivery=earliest,
                verified_at=verified,
                source="Valio Junction API /api/availability (synthetic challenge backend)",
                status=status,
                note=note,
                sales_unit=line.salesUnit if line else None,
                order_cutoff=cutoff,
                logistics_model=p.logisticModel,
            )
        return checks
