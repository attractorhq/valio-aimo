from copy import deepcopy
from datetime import datetime
from decimal import Decimal

import pytest
from synthetic.valio import DAY, NOW, availability_row, price_row

from app.contracts import Product, ReasonCode
from app.valio import availability_facts, price_facts


def fact(result, name):
    return next(item for item in result.facts if item.fact == name)


@pytest.mark.parametrize(("model", "amount", "tier", "quantity", "level"), [
    ("Stock", 0, "UNAVAILABLE", Decimal(0), "VERIFIED"),
    ("Stock", 19, "EXACT", Decimal(19), "VERIFIED"),
    ("Stock", 999999, "EXACT", Decimal(999999), "VERIFIED"),
    ("Stock", 1000000, "PLENTY", Decimal(1000000), "VERIFIED"),
    ("Stock", 9999999999, "PLENTY", Decimal(9999999999), "VERIFIED"),
    ("Terminal", 999999, "SUPPLIER", None, "UNKNOWN"),
    ("Unexpected", 24, "UNKNOWN", None, "UNKNOWN"),
])
def test_every_stock_tier(model, amount, tier, quantity, level):
    row = availability_row()
    row["logisticModel"] = model
    row["scheduleLines"][0]["availableAmount"] = amount
    result = availability_facts(row, DAY, NOW)
    assert result.stock_tier == tier
    assert result.available_quantity == quantity
    assert fact(result, "stock.quantity").level == level
    if tier == "PLENTY":
        assert fact(result, "stock.quantity").display == "Plenty in stock"
    if tier == "SUPPLIER":
        assert ReasonCode.SUPPLIER_STOCK_UNKNOWN in result.reasons
        assert fact(result, "stock.quantity").value is None  # Never expose 999999 as inventory.


@pytest.mark.parametrize(("closing", "passed"), [
    ("2026-11-13T11:59:59+02:00", True),
    ("2026-11-13T12:00:00+02:00", False),
    ("2026-11-13T12:00:01+02:00", False),
])
def test_cutoff_k3_uses_server_time_with_exact_boundary(closing, passed):
    row = availability_row()
    row["closingTime"] = closing
    result = availability_facts(row, DAY, NOW)
    conflict = fact(result, "delivery.cutoff").conflict
    assert (ReasonCode.CUTOFF_PASSED in result.reasons) is passed
    assert (conflict is not None) is passed
    if passed:
        assert conflict.code == "K3"
    assert result.available_quantity == 24  # Evidence retained, but the candidate must be excluded.


def test_terminal_with_passed_cutoff_is_not_approvable_supplier_stock():
    row = availability_row()
    row["logisticModel"] = "Terminal"
    row["closingTime"] = "2026-11-12T12:00:00+02:00"
    result = availability_facts(row, DAY, NOW)
    assert ReasonCode.CUTOFF_PASSED in result.reasons
    assert ReasonCode.SUPPLIER_STOCK_UNKNOWN not in result.reasons


def test_later_delivery_is_a_verified_date_not_an_out_of_stock_answer():
    result = availability_facts(availability_row(), NOW.date(), NOW)
    assert result.delivery_date == DAY
    assert result.stock_tier == "EXACT"
    assert result.reasons == (ReasonCode.DATE_LATER,)
    assert fact(result, "delivery.date").level == "VERIFIED"


@pytest.mark.parametrize("case", ["empty", "unknown", "negative", "invalid_date", "multiple"])
def test_unknown_invalid_and_empty_schedules_never_invent_stock(case):
    row = availability_row()
    if case == "empty":
        row["scheduleLines"] = []
    elif case == "unknown":
        row = {"productId": "SYN-UNKNOWN", "error": True, "scheduleLines": []}
    elif case == "negative":
        row["scheduleLines"][0]["availableAmount"] = -1
    elif case == "invalid_date":
        row["scheduleLines"][0]["availableDate"] = "not-a-date"
    else:
        row["scheduleLines"] *= 2
    result = availability_facts(row, DAY, NOW)
    assert result.stock_tier in ("UNKNOWN", "UNAVAILABLE")
    assert result.available_quantity in (None, Decimal(0))
    assert ReasonCode.NOT_AVAILABLE in result.reasons


@pytest.mark.parametrize("where", ["product", "line"])
def test_both_sales_block_locations_are_verified(where):
    row = availability_row()
    (row if where == "product" else row["scheduleLines"][0])["salesBlock"] = True
    result = availability_facts(row, DAY, NOW)
    assert result.sales_block is True
    assert ReasonCode.SALES_BLOCKED in result.reasons
    assert fact(result, "order.blocked").level == "VERIFIED"


def test_missing_cutoff_cannot_be_silently_promised():
    row = availability_row()
    row["closingTime"] = None
    result = availability_facts(row, DAY, NOW)
    assert fact(result, "delivery.cutoff").display == "Unknown"
    assert fact(result, "delivery.cutoff").level == "UNKNOWN"
    assert ReasonCode.NOT_AVAILABLE in result.reasons


def product(sales_unit="ST", units=None):
    return Product(productId="SYN-A", name="Synthetic product", category="synthetic", salesUnit=sales_unit,
                   baseUnit="ST", allowedLotSize="1", units=units or [])


def test_unconvertible_unit_is_k4_and_stock_is_not_sized():
    result = availability_facts(availability_row(), DAY, NOW, product("KI"))
    assert result.available_quantity is None
    assert fact(result, "unit.sales").conflict.code == "K4"
    assert ReasonCode.UNIT_MISMATCH in result.reasons


@pytest.mark.parametrize(("sales_unit", "availability_unit", "expected"), [
    ("KI", "ST", Decimal(2)), ("ST", "KI", Decimal(288)), ("ST", "ST", Decimal(24)),
])
def test_explicit_exact_unit_conversion(sales_unit, availability_unit, expected):
    row = availability_row()
    row["scheduleLines"][0]["salesUnit"] = availability_unit
    result = availability_facts(row, DAY, NOW, product(sales_unit, [{"unitId": "KI", "sizeInBaseUnits": "12"}]))
    assert result.available_quantity == expected
    assert ReasonCode.UNIT_MISMATCH not in result.reasons
    assert fact(result, "unit.sales").conflict is None


def test_nonterminating_conversion_is_not_rounded_into_evidence():
    row = availability_row()
    row["scheduleLines"][0]["availableAmount"] = 1
    result = availability_facts(row, DAY, NOW, product("KI", [{"unitId": "KI", "sizeInBaseUnits": "3"}]))
    assert ReasonCode.UNIT_MISMATCH in result.reasons


def test_price_decimal_customer_list_comparison_and_api_vat():
    result = price_facts(price_row(), datetime.combine(DAY, datetime.min.time()), NOW)
    assert result.unit_price == Decimal("2.5")
    assert result.vat_rate == Decimal("13.5")
    assert result.comparison_price == Decimal("1.25")
    assert result.list_price == Decimal(3)
    assert result.net_price_found
    assert "customer price (list €3.00)" in fact(result, "price.unit").display
    assert result.reasons == ()
    assert fact(result, "price.vatRate").source.field == "tax.taxRate"


@pytest.mark.parametrize("change", ["error", "missing", "negative", "expired", "future", "currency", "quantity", "tax"])
def test_invalid_price_is_a_per_product_exclusion(change):
    row = price_row()
    if change == "error":
        row["error"] = True
    elif change == "missing":
        row["catalogPrice"] = None
    elif change == "negative":
        row["catalogPrice"]["value"] = -1
    elif change == "expired":
        row["catalogPrice"]["validTo"] = "2026-11-15T23:59:59+02:00"
    elif change == "future":
        row["catalogPrice"]["validFrom"] = "2026-11-17T00:00:00+02:00"
    elif change == "currency":
        row["catalogPrice"]["currency"] = "USD"
    elif change == "quantity":
        row["quantity"] = 2
    else:
        row.pop("tax")
    result = price_facts(row, datetime.combine(DAY, datetime.min.time()), NOW)
    assert result.reasons == (ReasonCode.PRICE_UNAVAILABLE,)
    if change != "tax":
        assert result.unit_price is None
        assert fact(result, "price.unit").display == "Unknown"
    else:
        assert result.vat_rate is None  # No fallback to a product VAT code or a made-up tax rate.


def test_price_validity_uses_timestamp_not_just_calendar_date():
    row = price_row()
    row["catalogPrice"]["validFrom"] = "2026-11-16T12:00:00+02:00"
    result = price_facts(row, datetime.fromisoformat("2026-11-16T00:00:00+02:00"), NOW)
    assert result.unit_price is None


def test_invalid_optional_comparison_does_not_prove_a_price_ratio():
    row = price_row()
    row["comparisonPrice"]["validTo"] = "2020-01-01T00:00:00+02:00"
    row["netPriceFound"] = False
    result = price_facts(row, NOW, NOW)
    assert result.unit_price == Decimal("2.5")
    assert result.comparison_price is None
    assert "customer price" not in fact(result, "price.unit").display


def test_conversions_are_pure_and_retain_timestamp_provenance():
    row = availability_row()
    original = deepcopy(row)
    result = availability_facts(row, DAY, NOW)
    assert row == original
    assert all(item.source.retrievedAt == NOW for item in result.facts)
