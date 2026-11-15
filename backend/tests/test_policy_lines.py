"""Line rules L1–L14: one test per rule, both sides of each threshold (brief §6.4, effects per P2)."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import Deposit
from app.policy import M1, M2, ContentBasis, StockKind, evaluate_line
from tests.synthetic.policy import (
    CUTOFF_OPEN,
    CUTOFF_PASSED,
    GLUTEN_FREE,
    MILK_FREE,
    MONDAY,
    NOW,
    item,
    label,
    line,
    product,
)


def verdict(ln=None, *, it=None, mandate=M1, requested=MONDAY, now=NOW):
    return evaluate_line(ln or line(), item=it or item(), mandate=mandate, requested_date=requested, now=now)


def found(ln=None, **kwargs):
    return [(f.code, f.effect, f.rule) for f in verdict(ln, **kwargs).findings]


def test_clean_line_is_ok():
    v = verdict()
    assert v.findings == () and v.status == "OK" and v.exclusion_codes == ()


def test_l1_sales_block():
    assert found(line(sales_blocked=True)) == [("SALES_BLOCKED", "EXCLUDE", "L1")]
    assert found(line(sales_blocked=False)) == []


@pytest.mark.parametrize(
    ("kwargs", "excluded"),
    [
        ({"stock": StockKind.NOT_DELIVERABLE, "available_date": None}, True),
        ({"stock": StockKind.OUT, "available_date": None}, True),
        ({"stock": StockKind.EXACT, "stock_amount": "0"}, True),
        ({"stock": StockKind.PLENTY, "available_date": None}, True),
        ({"stock": StockKind.EXACT, "stock_amount": "1"}, False),
        ({"stock": StockKind.PLENTY}, False),
    ],
)
def test_l2_not_available(kwargs, excluded):
    assert (("NOT_AVAILABLE", "EXCLUDE", "L2") in found(line(**kwargs))) is excluded


def test_l3_price_unavailable():
    assert found(line(price_valid=False)) == [("PRICE_UNAVAILABLE", "EXCLUDE", "L3")]
    assert found(line(price_valid=True)) == []


def test_l3_unpriced_deposit_makes_the_total_incomplete():
    unpriced = product(deposits=(Deposit(productId="DEP", quantity=Decimal(1)),), allergens={"AE": "FREE_FROM"})
    priced = product(deposits=(Deposit(productId="DEP", quantity=Decimal(1), unitPriceExclVat=Decimal("0.10")),),
                     allergens={"AE": "FREE_FROM"})
    assert found(line(prod=unpriced)) == [("DEPOSIT_COST_UNVERIFIED", "EXCLUDE", "L3")]
    assert found(line(prod=priced)) == []


def test_l4_requirement_failure_excludes():
    milk = product(allergens={"AM": "CONTAINS"})
    v = verdict(line(prod=milk), it=item(requirements=[MILK_FREE]))
    assert v.status == "EXCLUDED" and v.exclusion_codes == ("ALLERGEN_CONTAINS",)
    assert verdict(line(prod=product(allergens={"AM": "FREE_FROM"})), it=item(requirements=[MILK_FREE])).status == "OK"


def test_l4_named_product_with_forbidden_allergen_is_blocked_not_swapped():
    mandate = M1.model_copy(update={"forbiddenAllergens": ["AP"]})
    named = product("NAMED", allergens={"AP": "CONTAINS"})
    v = verdict(line(prod=named), it=item(product_id="NAMED"), mandate=mandate)
    assert v.status == "BLOCKED" and v.exclusion_codes == ("ALLERGEN_CONTAINS",)


@pytest.mark.parametrize(
    ("available", "requested", "late"),
    [(MONDAY, MONDAY, False), (MONDAY, MONDAY - timedelta(days=1), True), (MONDAY, MONDAY + timedelta(days=1), False)],
)
def test_l5_date_later(available, requested, late):
    assert (("DATE_LATER", "ASK", "L5") in found(line(available_date=available), requested=requested)) is late


def test_l5_early_delivery_never_emits_date_later_and_early_detail_names_days_before():
    from app.policy.lines import early_delivery_detail

    # Delivery before the requested date (including 4 days early: Mon 16 Nov for Fri 20 Nov) is never DATE_LATER.
    assert found(line(available_date=MONDAY), requested=MONDAY + timedelta(days=2)) == []
    assert found(line(available_date=MONDAY), requested=MONDAY + timedelta(days=4)) == []
    assert early_delivery_detail(MONDAY, MONDAY + timedelta(days=4)) == (
        "Arrives Mon 16 Nov, 4 days before your Fri 20 Nov"
    )
    assert early_delivery_detail(MONDAY, MONDAY) is None
    assert early_delivery_detail(MONDAY, MONDAY - timedelta(days=1)) is None


@pytest.mark.parametrize(
    ("closing", "now", "passed"),
    [
        (CUTOFF_OPEN, CUTOFF_OPEN - timedelta(seconds=1), False),
        (CUTOFF_OPEN, CUTOFF_OPEN, False),  # closing exactly now: not yet passed
        (CUTOFF_OPEN, CUTOFF_OPEN + timedelta(seconds=1), True),
        (None, NOW, False),
    ],
)
def test_l6_cutoff_passed_is_excluded_not_approvable(closing, now, passed):
    assert (("CUTOFF_PASSED", "EXCLUDE", "L6") in found(line(closing_time=closing), now=now)) is passed


def test_l6_cutoff_from_k3_label_and_enforce_switch():
    assert ("CUTOFF_PASSED", "EXCLUDE", "L6") in found(line(facts=(label("delivery.cutoff", "K3"),)))
    lenient = M1.model_copy(update={"enforceCutoff": False})
    assert found(line(closing_time=CUTOFF_PASSED), mandate=lenient) == []


def test_l7_supplier_stock():
    assert found(line(stock=StockKind.SUPPLIER)) == [("SUPPLIER_STOCK_UNKNOWN", "ASK", "L7")]
    auto = M1.model_copy(update={"autoAcceptSupplierStock": True})
    assert found(line(stock=StockKind.SUPPLIER), mandate=auto) == []
    # A supplier line whose cut-off passed is excluded; the ASK can never be signed off on its own.
    assert verdict(line(stock=StockKind.SUPPLIER, closing_time=CUTOFF_PASSED)).status == "EXCLUDED"


def test_l9_k1_conflict_on_a_decisive_fact():
    k1 = product(allergens={"AW": "CONTAINS"}, claims=("FREE_FROM_GLUTEN",))
    assert found(line(prod=k1), it=item(requirements=[GLUTEN_FREE])) == [("DATA_CONFLICT", "EXCLUDE", "L9")]
    assert found(line(prod=k1), it=item()) == []  # not relied on: not decisive


def test_l9_k5_pack_content_conflict_is_always_decisive():
    ln = line(prod=product(allergens={"AE": "FREE_FROM"}, facts=(label("content.perSalesUnit", "K5"),)))
    assert found(ln) == [("DATA_CONFLICT", "EXCLUDE", "L9")]


def test_l10_agent_substitute():
    assert found(line(substitution="AGENT")) == [("AGENT_SUBSTITUTE", "ASK", "L10")]
    assert found(line(product_type_differs=True)) == [("AGENT_SUBSTITUTE", "ASK", "L10")]
    # A different product than the one the buyer named is never a silent swap.
    assert found(line(), it=item(product_id="OTHER")) == [("AGENT_SUBSTITUTE", "ASK", "L10")]
    assert found(line(), it=item(product_id="SYN-1")) == []


def test_l10_agent_substitute_must_prove_every_requirement():
    v = verdict(line(substitution="AGENT", prod=product(allergens={"AM": "MAY_CONTAIN"})),
                it=item(requirements=[MILK_FREE]))
    assert v.status == "EXCLUDED" and v.exclusion_codes == ("ALLERGEN_MAY_CONTAIN",)


def test_l11_valio_substitute():
    ok = product(allergens={"AE": "FREE_FROM"}, claims=("FREE_FROM_GLUTEN",))
    bad = product(allergens={"AW": "CONTAINS"})
    gf = item(requirements=[GLUTEN_FREE])
    assert found(line(prod=ok, substitution="VALIO"), it=gf, mandate=M1) == [("VALIO_SUBSTITUTE", "INFO", "L11")]
    assert found(line(prod=ok, substitution="VALIO"), it=gf, mandate=M2) == [("VALIO_SUBSTITUTE", "ASK", "L11")]
    rejected = verdict(line(prod=bad, substitution="VALIO", substitute_for="ORIG"), it=gf, mandate=M2)
    assert rejected.exclusion_codes == ("CLAIM_MISSING", "SUBSTITUTE_REJECTED")
    assert rejected.findings[-1].detail == "FREE_FROM_GLUTEN"


@pytest.mark.parametrize(
    ("basis", "expected"),
    [
        (ContentBasis.DECLARED, []),
        (ContentBasis.PRICE_RATIO, []),
        (ContentBasis.PER_KG, []),
        (ContentBasis.NAME, [("QUANTITY_BASIS_WEAK", "ASK", "L12")]),
        (ContentBasis.UNKNOWN, [("QUANTITY_BASIS_UNKNOWN", "EXCLUDE", "L12")]),
    ],
)
def test_l12_quantity_basis(basis, expected):
    assert found(line(content=basis)) == expected


@pytest.mark.parametrize(
    ("reliability", "low"), [("0.79", True), ("0.80", False), ("0.81", False), (None, False)]
)
def test_l13_low_reliability(reliability, low):
    assert (("LOW_RELIABILITY", "ASK", "L13") in found(line(reliability=reliability))) is low


def test_l14_unit_mismatch():
    assert found(line(unit_mismatch=True)) == [("UNIT_MISMATCH", "EXCLUDE", "L14")]
    assert found(line(facts=(label("unit.sales", "K4"),))) == [("UNIT_MISMATCH", "EXCLUDE", "L14")]
    assert found(line(unit_mismatch=False)) == []


def test_excluded_line_lists_only_exclusion_codes():
    v = verdict(line(stock=StockKind.NOT_DELIVERABLE, available_date=None, reliability="0.5"))
    assert v.status == "EXCLUDED" and v.exclusion_codes == ("NOT_AVAILABLE",)
    assert v.reasons == ("NOT_AVAILABLE", "LOW_RELIABILITY")


def test_covered_quantity():
    assert line(qty="18", delivered="90", unit="kg", stock=StockKind.EXACT, stock_amount="19").covered_quantity() == 90
    short = line(qty="20", delivered="100", unit="kg", stock=StockKind.EXACT, stock_amount="19")
    assert short.covered_quantity() == 95
    assert line(stock=StockKind.EXACT, stock_amount="-3").covered_quantity() == 0
    assert line(stock=StockKind.SUPPLIER).covered_quantity() == 10
    assert line(stock=StockKind.OUT).covered_quantity() == 0


def test_input_validation():
    with pytest.raises(ValueError, match="stock_amount"):
        line(stock=StockKind.EXACT)
    with pytest.raises(ValueError, match="positive"):
        line(qty="0", delivered="1")
    with pytest.raises(ValueError, match="aware"):
        verdict(now=datetime(2026, 11, 13, 21, 50))
    with pytest.raises(ValueError, match="belongs to item"):
        verdict(line(item_id="I2"))
