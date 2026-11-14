"""T06: getOffer sizing, allocation, totals, validity and offer hash. Synthetic records only (patch P7)."""

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import ContentPerSalesUnit, GetOfferRequest, Offer, OfferItem, Product, ReasonCode
from app.gateway.offers import build_offer, get_offer, shortlists, size
from app.policy import M1, M2, ContentBasis
from app.valio import RawBatch, normalise_batch

HEL = "+02:00"
NOW = datetime.fromisoformat(f"2026-11-13T21:52:02{HEL}")
MONDAY = date(2026, 11, 16)
UPSTREAM = "UPSTREAM-SYN"


def product(code, *, sales_unit="ST", content=("1", "l"), method="unitConversions.BASE_UNIT_OR_EACH.volume x "
            "units.ST.sizeInBaseUnits", lot="1", level="DECLARED") -> Product:
    cps = None
    if content is not None:
        cps = ContentPerSalesUnit(value=Decimal(content[0]), unit=content[1], level=level, method=method)
    return Product(
        productId=code, name=f"Synthetic {code}", names={"fi": f"Synteettinen {code}"}, category="synthetic",
        isFood=True, salesUnit=sales_unit, baseUnit=sales_unit, allowedLotSize=Decimal(lot),
        contentPerSalesUnit=cps, hasAllergenDeclaration=True,  # STRICT mandates (M2) need a declaration
    )


def price_row(code, value, *, valid_to=f"2026-12-31T23:59:59{HEL}", vat=13.5, comparison=None, comparison_unit=None):
    def money(v):
        return {"value": v, "valueWithTax": v, "currency": "EUR", "validFrom": f"2026-01-01T00:00:00{HEL}",
                "validTo": valid_to}

    row = {"productId": code, "quantity": 1, "catalogPrice": money(value), "listPrice": money(value),
           "tax": {"taxRate": vat, "taxValue": 0}, "netPriceFound": True, "error": False}
    if comparison is not None:
        row["comparisonPrice"], row["comparisonUnit"] = money(comparison), comparison_unit
    return row


def stock_row(code, amount, *, sales_unit="ST", model="Stock", closing=f"2026-11-15T17:00:00{HEL}",
              day=MONDAY):
    return {"productId": code, "closingTime": closing, "salesBlock": False, "logisticModel": model,
            "scheduleLines": [{"salesBlock": False, "availableAmount": amount, "availableDate": day.isoformat(),
                               "salesUnit": sales_unit}]}


def snapshot(customer, prices, stock, products, *, captured=NOW, day=MONDAY):
    codes = tuple(sorted({row["productId"] for row in prices}))
    price_env = {"customerId": UPSTREAM, "currency": "EUR", "pricingDate": f"{day}T00:00:00{HEL}",
                 "products": prices}
    stock_env = {"timeNow": NOW.isoformat(), "requestedDate": f"{day}T00:00:00{HEL}", "products": stock}
    batch = RawBatch(customer, UPSTREAM, day, codes, json.dumps(price_env).encode(),
                     json.dumps(stock_env).encode(), captured, captured)
    return normalise_batch(batch, products)


def offer_request(customer, *entries, day=MONDAY):
    return GetOfferRequest(customerId=customer, requestedDate=day, items=[
        OfferItem(itemId=i, productId=p, quantity=Decimal(q), unit=u) for i, p, q, u in entries
    ])


# --------------------------------------------------------------------------- scenario-shaped fixtures

MILK = product("SYN-MILK")
PASTA = product("SYN-PASTA", sales_unit="PSS", content=("5", "kg"),
                method="unitConversions.BASE_UNIT_OR_EACH.netWeight x units.PSS.sizeInBaseUnits")
OAT = product("SYN-OAT")


def scenario_a():
    products = {"SYN-MILK": MILK}
    snap = snapshot(M1.customerId, [price_row("SYN-MILK", 1.01)], [stock_row("SYN-MILK", 9999999999)], products)
    return build_offer(offer_request(M1.customerId, ("I1", "SYN-MILK", "1000", "l")),
                       products=products, snapshot=snap, mandate=M1)


def scenario_b(pasta_price=22.92):
    products = {"SYN-PASTA": PASTA, "SYN-OAT": OAT}
    snap = snapshot(
        M2.customerId,
        [price_row("SYN-PASTA", pasta_price), price_row("SYN-OAT", 1.17)],
        [stock_row("SYN-PASTA", 19, sales_unit="PSS"), stock_row("SYN-OAT", 9999999999)],
        products,
    )
    return build_offer(
        offer_request(M2.customerId, ("I1", "SYN-PASTA", "90", "kg"), ("I2", "SYN-OAT", "80", "l")),
        products=products, snapshot=snap, mandate=M2,
    )


def test_scenario_a_shape():
    offer = scenario_a().offer
    (line,) = offer.lines
    assert (line.quantitySalesUnits, line.deliveredQuantity.value, line.deliveredQuantity.unit) == (
        Decimal(1000), Decimal(1000), "l")
    assert line.unitPriceExclVat == Decimal("1.01")
    assert line.lineTotalExclVat == Decimal("1010.00")
    assert (offer.totals.exclVat, offer.totals.vat, offer.totals.inclVat) == (
        Decimal("1010.00"), Decimal("136.35"), Decimal("1146.35"))
    assert line.salesUnitLabel == "pc"
    assert line.deliveryDate == MONDAY
    assert line.reasons == []
    assert offer.excluded == []
    Offer.model_validate_json(offer.model_dump_json())  # wire round trip


def test_scenario_b_shape():
    offer = scenario_b().offer
    pasta, oat = offer.lines
    assert (pasta.quantitySalesUnits, pasta.deliveredQuantity.value, pasta.lineTotalExclVat) == (
        Decimal(18), Decimal(90), Decimal("412.56"))
    assert (oat.quantitySalesUnits, oat.lineTotalExclVat) == (Decimal(80), Decimal("93.60"))
    # VAT is rounded once per rate on the total: 506.16 × 13.5 % = 68.33 (per line it would be 68.34).
    assert (offer.totals.exclVat, offer.totals.vat, offer.totals.inclVat) == (
        Decimal("506.16"), Decimal("68.33"), Decimal("574.49"))


# --------------------------------------------------------------------------- hash and validity


def test_hash_is_stable_across_runs():
    assert scenario_b().offer.offerHash == scenario_b().offer.offerHash
    assert scenario_a().offer.offerHash != scenario_b().offer.offerHash


def test_hash_changes_when_a_price_changes():
    assert scenario_b(22.92).offer.offerHash != scenario_b(22.93).offer.offerHash


def test_hash_ignores_read_timestamps_only():
    products = {"SYN-MILK": MILK}
    request = offer_request(M1.customerId, ("I1", "SYN-MILK", "10", "l"))
    first = snapshot(M1.customerId, [price_row("SYN-MILK", 1.01)], [stock_row("SYN-MILK", 500)], products)
    later = snapshot(M1.customerId, [price_row("SYN-MILK", 1.01)], [stock_row("SYN-MILK", 500)], products,
                     captured=NOW + timedelta(minutes=2))
    a = build_offer(request, products=products, snapshot=first, mandate=M1).offer
    b = build_offer(request, products=products, snapshot=later, mandate=M1).offer
    assert a.offerHash == b.offerHash


def test_hash_changes_when_cutoff_changes():
    products = {"SYN-MILK": MILK}
    request = offer_request(M1.customerId, ("I1", "SYN-MILK", "10", "l"))
    one = snapshot(M1.customerId, [price_row("SYN-MILK", 1.01)], [stock_row("SYN-MILK", 500)], products)
    two = snapshot(M1.customerId, [price_row("SYN-MILK", 1.01)],
                   [stock_row("SYN-MILK", 500, closing=f"2026-11-15T16:00:00{HEL}")], products)
    assert (build_offer(request, products=products, snapshot=one, mandate=M1).offer.offerHash
            != build_offer(request, products=products, snapshot=two, mandate=M1).offer.offerHash)


def test_valid_until_is_15_minutes():
    offer = scenario_a().offer
    assert offer.createdAt == NOW
    assert offer.validUntil == NOW + timedelta(minutes=15)


def test_valid_until_never_beyond_price_valid_to():
    products = {"SYN-MILK": MILK}
    valid_to = NOW + timedelta(minutes=5)
    today = NOW.date()  # a same-day price whose validity ends before the 15 minutes are up
    snap = snapshot(M1.customerId, [price_row("SYN-MILK", 1.01, valid_to=valid_to.isoformat())],
                    [stock_row("SYN-MILK", 500, day=today)], products, day=today)
    offer = build_offer(offer_request(M1.customerId, ("I1", "SYN-MILK", "10", "l"), day=today),
                        products=products, snapshot=snap, mandate=M1).offer
    assert offer.validUntil == valid_to


# --------------------------------------------------------------------------- sizing


@pytest.mark.parametrize(("needed", "content", "unit", "lot", "units", "delivered"), [
    ("90", "5", "kg", "1", 18, "90"),
    ("91", "5", "kg", "1", 19, "95"),         # ceil: at most one sales unit of over-delivery
    ("1000", "1", "kg", "6", 1002, "1002"),   # allowedLotSize multiples
    ("0.5", "1", "kg", "1", 1, "1"),
    ("20", "1.75", "l", "0.01", 12, "21"),    # T37: whole sales units first, even when lot is 0.01
    ("20", "1", "l", "6", 24, "24"),          # T37: whole sales units rounded up to lot 6
    ("10", "0.5", "kg", "1", 20, "10"),       # T37: lot 1, 0.5 kg content -> 20 units
    ("1000", "1", "l", "0.01", 1000, "1000"), # T37: Scenario A 1000 x 1 l -> 1000
])
def test_sizing(needed, content, unit, lot, units, delivered):
    p = product("SYN-X", content=(content, unit), lot=lot)
    sizing = size(p, Decimal(needed), unit)
    assert (sizing.quantity_sales_units, sizing.delivered.value) == (Decimal(units), Decimal(delivered))
    assert sizing.basis is ContentBasis.DECLARED


def test_pieces_count_sales_units():
    sizing = size(product("SYN-X", content=None), Decimal(40), "pcs")
    assert (sizing.quantity_sales_units, sizing.delivered.unit, sizing.unit_mismatch) == (Decimal(40), "pcs", False)


def test_unknown_content_cannot_be_sized():
    assert size(product("SYN-X", content=None), Decimal(10), "kg").basis is ContentBasis.UNKNOWN


def test_unit_mismatch_is_flagged():
    assert size(product("SYN-X", content=("5", "kg")), Decimal(10), "l").unit_mismatch


# --------------------------------------------------------------------------- allocation and exclusion


def test_allocation_takes_verified_stock_then_the_next_candidate():
    other = product("SYN-PASTA2", sales_unit="PSS", content=("5", "kg"))
    products = {"SYN-PASTA": PASTA, "SYN-PASTA2": other}
    snap = snapshot(
        M2.customerId,
        [price_row("SYN-PASTA", 22.92), price_row("SYN-PASTA2", 25.00)],
        [stock_row("SYN-PASTA", 15, sales_unit="PSS"), stock_row("SYN-PASTA2", 100, sales_unit="PSS")],
        products,
    )
    offer = build_offer(
        offer_request(M2.customerId, ("I1", "SYN-PASTA", "90", "kg"), ("I1", "SYN-PASTA2", "90", "kg")),
        products=products, snapshot=snap, mandate=M2,
    ).offer
    assert [(line.productId, line.quantitySalesUnits) for line in offer.lines] == [
        ("SYN-PASTA", Decimal(15)), ("SYN-PASTA2", Decimal(3))]
    assert sum(line.deliveredQuantity.value for line in offer.lines) == Decimal(90)


def test_partial_stock_without_another_candidate_stays_partial():
    products = {"SYN-PASTA": PASTA}
    snap = snapshot(M2.customerId, [price_row("SYN-PASTA", 22.92)], [stock_row("SYN-PASTA", 19, sales_unit="PSS")],
                    products)
    offer = build_offer(offer_request(M2.customerId, ("I1", "SYN-PASTA", "120", "kg")),
                        products=products, snapshot=snap, mandate=M2).offer
    (line,) = offer.lines
    assert (line.quantitySalesUnits, line.deliveredQuantity.value) == (Decimal(19), Decimal(95))


def test_partial_stock_floors_to_whole_sales_units_when_lot_is_fractional():
    cream = product("SYN-CREAM", sales_unit="ST", content=("1.75", "l"), lot="0.01")
    products = {"SYN-CREAM": cream}
    snap = snapshot(M1.customerId, [price_row("SYN-CREAM", 5.83)], [stock_row("SYN-CREAM", 7.43, sales_unit="ST")],
                    products)
    offer = build_offer(offer_request(M1.customerId, ("I1", "SYN-CREAM", "20", "l")),
                        products=products, snapshot=snap, mandate=M1).offer
    (line,) = offer.lines
    assert (line.quantitySalesUnits, line.deliveredQuantity.value) == (Decimal(7), Decimal("12.25"))
    stock_fact = next(f for f in line.facts if f.fact == "stock.quantity")
    assert "12.25 l = 7 × 1.75 l packs; 7.43 in stock" in (stock_fact.display or "")


def test_t51_partial_quantity_below_shown_stock_explains_pack_lot_and_shortfall():
    salmon = product(
        "SYN-SALMON-5KG",
        sales_unit="KG",
        content=("1", "kg"),
        method="salesUnit.KG",
        lot="5",
    )
    products = {"SYN-SALMON-5KG": salmon}
    snap = snapshot(
        M1.customerId,
        [price_row("SYN-SALMON-5KG", 14.11)],
        [stock_row("SYN-SALMON-5KG", 43.0, sales_unit="KG")],
        products,
    )
    offer = build_offer(
        offer_request(M1.customerId, ("I1", "SYN-SALMON-5KG", "400", "kg")),
        products=products,
        snapshot=snap,
        mandate=M1,
    ).offer
    (line,) = offer.lines
    assert (line.quantitySalesUnits, line.deliveredQuantity.value) == (Decimal(40), Decimal(40))
    stock_fact = next(f for f in line.facts if f.fact == "stock.quantity")
    assert "40 kg = 8 × 5 kg packs; 43 kg in stock" in (stock_fact.display or "")
    assert "40 kg now; 360 kg cannot be met by Mon 16 Nov" in (stock_fact.display or "")


def test_unorderable_candidates_are_excluded_with_reasons():
    cut = product("SYN-CUT", sales_unit="PSS", content=("5", "kg"))
    out = product("SYN-OUT", sales_unit="PSS", content=("5", "kg"))
    products = {"SYN-CUT": cut, "SYN-OUT": out, "SYN-PASTA": PASTA}
    snap = snapshot(
        M2.customerId,
        [price_row("SYN-CUT", 20.0), price_row("SYN-OUT", 21.0), price_row("SYN-PASTA", 22.92)],
        [stock_row("SYN-CUT", 50, sales_unit="PSS", model="Terminal", closing=f"2026-11-12T14:00:00{HEL}"),
         stock_row("SYN-OUT", 0, sales_unit="PSS"), stock_row("SYN-PASTA", 19, sales_unit="PSS")],
        products,
    )
    offer = build_offer(
        offer_request(M2.customerId, ("I1", "SYN-CUT", "90", "kg"), ("I1", "SYN-OUT", "90", "kg"),
                      ("I1", "SYN-PASTA", "90", "kg")),
        products=products, snapshot=snap, mandate=M2,
    ).offer
    assert [line.productId for line in offer.lines] == ["SYN-PASTA"]
    reasons = {e.productId: e.reasons for e in offer.excluded}
    assert ReasonCode.CUTOFF_PASSED in reasons["SYN-CUT"]
    assert ReasonCode.NOT_AVAILABLE in reasons["SYN-OUT"]
    assert offer.lines[0].lineId == "L1"


def test_price_unavailable_is_excluded():
    products = {"SYN-MILK": MILK}
    snap = snapshot(M1.customerId, [price_row("SYN-MILK", 1.01, valid_to=f"2026-01-31T00:00:00{HEL}")],
                    [stock_row("SYN-MILK", 500)], products)
    offer = build_offer(offer_request(M1.customerId, ("I1", "SYN-MILK", "10", "l")),
                        products=products, snapshot=snap, mandate=M1).offer
    assert offer.lines == []
    assert offer.excluded[0].reasons == [ReasonCode.PRICE_UNAVAILABLE]
    assert offer.totals.exclVat == Decimal("0.00")


def test_one_stock_pool_per_product_across_items():
    products = {"SYN-MILK": MILK}
    snap = snapshot(M1.customerId, [price_row("SYN-MILK", 1.01)], [stock_row("SYN-MILK", 30)], products)
    offer = build_offer(
        offer_request(M1.customerId, ("I1", "SYN-MILK", "20", "l"), ("I2", "SYN-MILK", "20", "l")),
        products=products, snapshot=snap, mandate=M1,
    ).offer
    assert [(line.itemId, line.quantitySalesUnits) for line in offer.lines] == [
        ("I1", Decimal(20)), ("I2", Decimal(10))]


def test_supplier_stock_and_later_date_are_carried_as_line_reasons():
    products = {"SYN-MILK": MILK}
    snap = snapshot(M1.customerId, [price_row("SYN-MILK", 1.01)],
                    [stock_row("SYN-MILK", 999999, model="Terminal", day=MONDAY + timedelta(days=1))], products)
    offer = build_offer(offer_request(M1.customerId, ("I1", "SYN-MILK", "10", "l")),
                        products=products, snapshot=snap, mandate=M1).offer
    (line,) = offer.lines
    assert {ReasonCode.DATE_LATER, ReasonCode.SUPPLIER_STOCK_UNKNOWN} <= set(line.reasons)


def test_name_only_content_is_offered_but_flagged_weak():
    weak = product("SYN-WEAK", content=("1", "l"), method="name", level="ESTIMATE")
    products = {"SYN-WEAK": weak}
    snap = snapshot(M1.customerId, [price_row("SYN-WEAK", 1.0)], [stock_row("SYN-WEAK", 500)], products)
    offer = build_offer(offer_request(M1.customerId, ("I1", "SYN-WEAK", "10", "l")),
                        products=products, snapshot=snap, mandate=M1).offer
    assert offer.lines[0].reasons == [ReasonCode.QUANTITY_BASIS_WEAK]


def test_price_ratio_fills_a_missing_content():
    unknown = product("SYN-RATIO", content=None)
    products = {"SYN-RATIO": unknown}
    snap = snapshot(M1.customerId, [price_row("SYN-RATIO", 3.0, comparison=1.5, comparison_unit="L")],
                    [stock_row("SYN-RATIO", 500)], products)
    offer = build_offer(offer_request(M1.customerId, ("I1", "SYN-RATIO", "10", "l")),
                        products=products, snapshot=snap, mandate=M1).offer
    (line,) = offer.lines
    assert (line.contentPerSalesUnit.value, line.quantitySalesUnits) == (Decimal(2), Decimal(5))
    assert line.contentPerSalesUnit.level == "ESTIMATE"


# --------------------------------------------------------------------------- binding and the single lookup


def test_customer_and_date_binding_is_enforced():
    products = {"SYN-MILK": MILK}
    snap = snapshot(M1.customerId, [price_row("SYN-MILK", 1.01)], [stock_row("SYN-MILK", 500)], products)
    with pytest.raises(ValueError):
        build_offer(offer_request(M2.customerId, ("I1", "SYN-MILK", "10", "l")),
                    products=products, snapshot=snap, mandate=M2)
    with pytest.raises(ValueError):
        build_offer(offer_request(M1.customerId, ("I1", "SYN-MILK", "10", "l")),
                    products=products, snapshot=replace(snap, requested_date=MONDAY + timedelta(days=1)),
                    mandate=M1)


def test_get_offer_makes_one_batched_lookup():
    products = {"SYN-PASTA": PASTA, "SYN-OAT": OAT}
    calls = []

    class FakeProvider:
        def lookup(self, lists, requested_date, customer_id, *, products=None, force_refresh=False):
            calls.append(lists)
            return snapshot(customer_id, [price_row("SYN-PASTA", 22.92), price_row("SYN-OAT", 1.17)],
                            [stock_row("SYN-PASTA", 19, sales_unit="PSS"), stock_row("SYN-OAT", 9999999999)],
                            products)

    request = offer_request(M2.customerId, ("I1", "SYN-PASTA", "90", "kg"), ("I2", "SYN-OAT", "80", "l"))
    result = get_offer(request, products=products, provider=FakeProvider(), mandate=M2)
    assert calls == [{"I1": ["SYN-PASTA"], "I2": ["SYN-OAT"]}]
    assert result.offer.totals.exclVat == Decimal("506.16")
    assert shortlists(request) == calls[0]


def test_unknown_product_is_rejected_before_any_lookup():
    class NoCall:
        def lookup(self, *args, **kwargs):
            raise AssertionError("must not call the API")

    with pytest.raises(ValueError):
        get_offer(offer_request(M1.customerId, ("I1", "NOPE", "1", "l")), products={}, provider=NoCall(), mandate=M1)


def test_strict_mandate_excludes_an_undeclared_product():
    undeclared = product("SYN-UNDECLARED", sales_unit="PSS", content=("5", "kg")).model_copy(
        update={"hasAllergenDeclaration": False})
    products = {"SYN-UNDECLARED": undeclared, "SYN-PASTA": PASTA}
    snap = snapshot(M2.customerId, [price_row("SYN-UNDECLARED", 9.0), price_row("SYN-PASTA", 22.92)],
                    [stock_row("SYN-UNDECLARED", 100, sales_unit="PSS"), stock_row("SYN-PASTA", 19, sales_unit="PSS")],
                    products)
    offer = build_offer(
        offer_request(M2.customerId, ("I1", "SYN-UNDECLARED", "90", "kg"), ("I1", "SYN-PASTA", "90", "kg")),
        products=products, snapshot=snap, mandate=M2,
    ).offer
    assert [line.productId for line in offer.lines] == ["SYN-PASTA"]
    assert offer.excluded[0].reasons == [ReasonCode.ALLERGEN_UNKNOWN]


def test_offer_lines_feed_the_policy_decision():
    from app.contracts import RequestItem, StructuredRequest
    from app.policy import decide

    result = scenario_b()
    request = StructuredRequest(
        requestText="Synthetic request by Monday.", requestedDate=MONDAY,
        items=[RequestItem(itemId="I1", what="pasta", quantity=Decimal(90), unit="kg"),
               RequestItem(itemId="I2", what="oat drink", quantity=Decimal(80), unit="l")],
    )
    decision = decide(mandate=M2, request=request, lines=[v.line for v in result.verdicts], now=NOW)
    assert decision.outcome == "ASK"
    assert list(decision.reasons) == [ReasonCode.OVER_AUTO_LIMIT]
    assert decision.total_excl_vat == result.offer.totals.exclVat
