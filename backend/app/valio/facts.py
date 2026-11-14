"""Pure, deterministic conversion of one upstream observation into evidence, not decisions."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, Inexact, localcontext
from typing import Literal

from pydantic import ValidationError

from app.contracts import (
    Conflict,
    Product,
    ReasonCode,
    TrustLabel,
    TrustSource,
    format_date,
    format_money,
    format_time,
)

from .vendor import HELSINKI, VendorAvailability, VendorPrice, VendorProductPrice, aware


def fact(name, value, display, level, system, field, retrieved_at, *, conflict=None, method=None):
    return TrustLabel(
        fact=name, value=value, display=display, level=level,
        source=TrustSource(system=f"valio-aimo.{system}-api", field=field, retrievedAt=retrieved_at),
        conflict=conflict, method=method,
    )


@dataclass(frozen=True)
class PriceFacts:
    unit_price: Decimal | None
    vat_rate: Decimal | None
    comparison_price: Decimal | None
    comparison_unit: str | None
    list_price: Decimal | None
    net_price_found: bool
    valid_from: datetime | None
    valid_to: datetime | None
    facts: tuple[TrustLabel, ...]
    reasons: tuple[ReasonCode, ...]


def _valid_price(price: VendorPrice | None, pricing_at: datetime) -> bool:
    return bool(price and price.currency == "EUR" and aware(price.validFrom) <= pricing_at <= aware(price.validTo))


def price_facts(row: dict, pricing_at: datetime, retrieved_at: datetime) -> PriceFacts:
    pricing_at, retrieved_at = aware(pricing_at), aware(retrieved_at)
    try:
        parsed = VendorProductPrice.model_validate(row)
    except ValidationError:
        parsed = None
    valid = bool(parsed and not parsed.error and parsed.quantity == 1 and _valid_price(parsed.catalogPrice, pricing_at))
    price = parsed.catalogPrice if valid else None
    tax = parsed.tax.taxRate if valid and parsed.tax else None
    comparison = parsed.comparisonPrice if valid and _valid_price(parsed.comparisonPrice, pricing_at) else None
    listed = parsed.listPrice if valid and _valid_price(parsed.listPrice, pricing_at) else None
    customer_price = bool(valid and parsed.netPriceFound)
    display = f"{format_money(price.value)} / sales unit excl. VAT" if price else "Unknown"
    if customer_price:
        display += " · customer price"
        if listed:
            display += f" (list {format_money(listed.value)})"
    facts = (
        fact("price.unit", str(price.value) if price else None, display,
             "VERIFIED" if price else "UNKNOWN", "price", "catalogPrice.value", retrieved_at),
        fact("price.vatRate", str(tax) if tax is not None else None,
             f"VAT {tax} %" if tax is not None else "Unknown", "VERIFIED" if tax is not None else "UNKNOWN",
             "price", "tax.taxRate", retrieved_at),
        fact("price.comparison", str(comparison.value) if comparison else None,
             f"{format_money(comparison.value)} / {parsed.comparisonUnit.lower()}"
             if comparison and parsed.comparisonUnit else "Unknown",
             "VERIFIED" if comparison and parsed.comparisonUnit else "UNKNOWN", "price", "comparisonPrice.value",
             retrieved_at),
    )
    return PriceFacts(
        price.value if price else None, tax,
        comparison.value if comparison and parsed.comparisonUnit else None,
        parsed.comparisonUnit if comparison else None, listed.value if listed else None, customer_price,
        aware(price.validFrom) if price else None, aware(price.validTo) if price else None, facts,
        () if price and tax is not None else (ReasonCode.PRICE_UNAVAILABLE,),
    )


# Valio Aimo (14 Nov): an available amount of 999999 or more means Valio does not track availability for the
# product, and its ordering logic allows any quantity. It is no proof of a stock count, so it is DECLARED,
# never VERIFIED. (Empty means not available.)
STOCK_SENTINEL = Decimal(999_999)
# T78: the display says exactly what Valio said; the method carries Valio's statement and its source.
SENTINEL_METHOD = (
    "Valio Aimo Q&A, 14 Nov: an available amount of 999999 or more means availability is unknown, "
    "and Valio accepts any quantity"
)
NO_STOCK_LIMIT = "Stock not tracked · Valio accepts any quantity"


def is_stock_sentinel(amount: Decimal | None) -> bool:
    return amount is not None and amount >= STOCK_SENTINEL


@dataclass(frozen=True)
class AvailabilityFacts:
    available_quantity: Decimal | None
    stock_tier: Literal["EXACT", "PLENTY", "SUPPLIER", "UNAVAILABLE", "UNKNOWN"]
    delivery_date: date | None
    closing_time: datetime | None
    sales_block: bool | None
    sales_unit: str | None
    logistics_model: str | None
    time_now: datetime
    facts: tuple[TrustLabel, ...]
    reasons: tuple[ReasonCode, ...]


def _converted_quantity(amount: Decimal, source: str, product: Product) -> Decimal | None:
    factors = {unit.unitId: unit.sizeInBaseUnits for unit in product.units}
    factors[product.baseUnit] = Decimal(1)
    if source == product.salesUnit:
        return amount
    if source not in factors or product.salesUnit not in factors:
        return None
    try:
        with localcontext() as context:
            context.prec = 64
            context.traps[Inexact] = True
            return amount * factors[source] / factors[product.salesUnit]
    except Inexact:
        return None


def availability_facts(row: dict, requested_date: date, time_now: datetime,
                       product: Product | None = None) -> AvailabilityFacts:
    time_now = aware(time_now)
    reasons: list[ReasonCode] = []
    try:
        parsed = VendorAvailability.model_validate(row)
    except ValidationError:
        parsed = None
    # An explicit unknown ID is a negative answer, not an invented quantity of zero.
    if row.get("error") is True or parsed is None or len(parsed.scheduleLines) > 1:
        return AvailabilityFacts(
            None, "UNKNOWN", None, None, None, None, None, time_now,
            (fact("stock.quantity", None, "Unknown", "UNKNOWN", "availability", "scheduleLines", time_now),),
            (ReasonCode.NOT_AVAILABLE,),
        )
    line = parsed.scheduleLines[0] if parsed.scheduleLines else None
    closing = aware(parsed.closingTime) if parsed.closingTime else None
    delivery = line.availableDate if line else None
    blocked = parsed.salesBlock or bool(line and line.salesBlock)
    quantity, tier, stock_level, stock_display = None, "UNKNOWN", "UNKNOWN", "Unknown"
    unit_conflict = None
    conversion_method = None
    stock_method = None
    if blocked:
        reasons.append(ReasonCode.SALES_BLOCKED)
    if line is None or line.availableAmount == 0 or delivery is None:
        tier, stock_level, stock_display = "UNAVAILABLE", "VERIFIED", "Not deliverable on any date"
        quantity = Decimal(0)
        if line and line.availableAmount == 0:
            stock_display = "Out of stock"
        reasons.append(ReasonCode.NOT_AVAILABLE)
    elif parsed.logisticModel == "Terminal":
        tier, stock_display = "SUPPLIER", "Ships from supplier's warehouse — quantity not visible to Valio Aimo"
        if closing is not None and closing >= time_now:
            reasons.append(ReasonCode.SUPPLIER_STOCK_UNKNOWN)
    elif parsed.logisticModel == "Stock":
        if is_stock_sentinel(line.availableAmount):
            # Any quantity may be ordered (same allocation as before), but the count itself is not evidence.
            tier, quantity, stock_level, stock_display = "PLENTY", line.availableAmount, "DECLARED", NO_STOCK_LIMIT
            stock_method = SENTINEL_METHOD
        else:
            stock_level = "VERIFIED"
            tier, quantity = "EXACT", line.availableAmount
            stock_display = f"{quantity:,f} in stock"
    else:
        reasons.append(ReasonCode.NOT_AVAILABLE)
    if line and product:
        converted = _converted_quantity(line.availableAmount, line.salesUnit, product)
        if converted is None:
            unit_conflict = Conflict(code="K4", message=(
                f"Product data says {product.salesUnit}; availability says {line.salesUnit}; no exact conversion."
            ))
            reasons.append(ReasonCode.UNIT_MISMATCH)
            quantity, tier, stock_level, stock_display = None, "UNKNOWN", "UNKNOWN", "Unknown"
        elif tier in ("EXACT", "PLENTY"):
            quantity = converted
            if line.salesUnit != product.salesUnit:
                conversion_method = "exact sales-unit conversion via Product.units.sizeInBaseUnits"
            if tier == "EXACT":
                stock_display = f"{quantity:,f} in stock"
    cutoff_conflict = None
    if delivery is not None:
        if delivery > requested_date:
            reasons.append(ReasonCode.DATE_LATER)
        if closing is not None and closing < time_now:
            cutoff_conflict = Conflict(code="K3", message="The cut-off has passed while delivery is still offered.")
            reasons.append(ReasonCode.CUTOFF_PASSED)
        elif closing is None:
            # No fabricated cut-off. A known delivery alone is insufficient to promise it.
            reasons.append(ReasonCode.NOT_AVAILABLE)
    facts = [
        fact("stock.quantity", str(quantity) if quantity is not None else None, stock_display, stock_level,
             "availability", "scheduleLines[0].availableAmount", time_now, method=stock_method or conversion_method),
        fact("delivery.date", delivery.isoformat() if delivery else None,
             format_date(delivery) if delivery else "Unknown", "VERIFIED" if delivery else "UNKNOWN",
             "availability", "scheduleLines[0].availableDate", time_now),
        fact("delivery.cutoff", closing.isoformat() if closing else None,
             format_time(closing.astimezone(HELSINKI)) if closing else "Unknown",
             "VERIFIED" if closing else "UNKNOWN", "availability", "closingTime", time_now,
             conflict=cutoff_conflict),
        fact("order.blocked", blocked, "Sales blocked" if blocked else "No sales block", "VERIFIED",
             "availability", "salesBlock, scheduleLines[0].salesBlock", time_now),
        fact("unit.sales", line.salesUnit if line else None, line.salesUnit if line else "Unknown",
             "VERIFIED" if line else "UNKNOWN", "availability", "scheduleLines[0].salesUnit", time_now,
             conflict=unit_conflict),
    ]
    return AvailabilityFacts(
        quantity, tier, delivery, closing, blocked, line.salesUnit if line else None,
        parsed.logisticModel, time_now, tuple(facts), tuple(dict.fromkeys(reasons)),
    )
