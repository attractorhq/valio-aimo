"""Typed upstream models reused from the original real_valio adapter (0e74e98)."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

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


class VendorTax(VendorModel):
    taxRate: Decimal = Amount
    taxValue: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class VendorProductPrice(VendorModel):
    productId: str
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    catalogPrice: VendorPrice | None = None
    comparisonPrice: VendorPrice | None = None
    comparisonUnit: str | None = None
    listPrice: VendorPrice | None = None
    tax: VendorTax | None = None
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


class PriceEnvelope(VendorModel):
    """Validate batch binding first, then validate each product independently."""

    customerId: str
    currency: str
    pricingDate: datetime
    products: list[dict]


class AvailabilityEnvelope(VendorModel):
    timeNow: datetime
    requestedDate: datetime
    products: list[dict]


def aware(value: datetime) -> datetime:
    # The upstream contract defines naive server timestamps as Europe/Helsinki.
    return value.replace(tzinfo=HELSINKI) if value.tzinfo is None else value
