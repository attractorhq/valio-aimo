"""Property tests over many generated orders: AC16 (no P2.3 reason is ever offered for approval) and §6.6."""

import random
from datetime import timedelta

import pytest

from app.contracts import REASON_REGISTRY, Requirement
from app.policy import M1, M2, ContentBasis, StockKind, decide
from tests.synthetic.policy import (
    CUTOFF_OPEN,
    CUTOFF_PASSED,
    GLUTEN_FREE,
    MILK_FREE,
    NOW,
    item,
    label,
    line,
    product,
    request,
)

# Patch P2.3, written out from the patch text rather than read from the registry under test.
NOT_APPROVABLE = frozenset({
    "CLAIM_MISSING", "ALLERGEN_CONTAINS", "ALLERGEN_MAY_CONTAIN", "ALLERGEN_UNKNOWN", "ALLERGEN_IMPLICIT",
    "DATA_CONFLICT", "CUTOFF_PASSED", "UNIT_MISMATCH", "NOT_AVAILABLE", "SALES_BLOCKED", "PRICE_UNAVAILABLE",
    "OVER_HARD_LIMIT", "SUBSTITUTE_REJECTED", "DEPOSIT_COST_UNVERIFIED", "QUANTITY_BASIS_UNKNOWN",
})
APPROVABLE = frozenset({
    "OVER_AUTO_LIMIT", "DATE_LATER", "PARTIAL_AVAILABILITY", "SUPPLIER_STOCK_UNKNOWN", "VALIO_SUBSTITUTE",
    "AGENT_SUBSTITUTE", "LOW_RELIABILITY", "QUANTITY_BASIS_WEAK",
    # T107 (team decision, beyond P2.2): an attempt to bypass controls asks a person; it waives no evidence.
    "INSTRUCTION_IGNORED",
    # T71 (team decision, beyond P2.2): data from the stale-fallback cache asks a person; the approval re-checks live.
    "STALE_DATA",
})

PRODUCTS = [
    product("CLEAN", allergens={"AE": "FREE_FROM"}, claims=("FREE_FROM_GLUTEN",)),
    product("X99", allergens={"X99": "CONTAINS"}, claims=("FREE_FROM_GLUTEN",)),
    product("MILK", allergens={"AM": "CONTAINS"}, claims=("FREE_FROM_GLUTEN", "FREE_FROM_LACTOSE")),
    product("MAY", allergens={"AM": "MAY_CONTAIN"}),
    product("NONE", declared=False, claims=("FREE_FROM_GLUTEN",)),
    product("K1", allergens={"AW": "CONTAINS"}, claims=("FREE_FROM_GLUTEN",)),
    product("K5", allergens={"AE": "FREE_FROM"}, facts=(label("content.perSalesUnit", "K5"),)),
    product("PEANUT", allergens={"AP": "CONTAINS"}),
]
REQUIREMENTS = [[], [GLUTEN_FREE], [MILK_FREE], [Requirement(kind="AVOID_ALLERGEN", code="AP")]]
MANDATES = [
    M1, M2,
    M1.model_copy(update={"forbiddenAllergens": ["AP"], "autoAcceptSupplierStock": True}),
    M2.model_copy(update={"autoAcceptValioSubstitutes": True, "enforceCutoff": False}),
]


def random_line(rng: random.Random, line_id: str, prod):
    stock = rng.choice(list(StockKind))
    return line(
        line_id,
        prod=prod,
        qty="10",
        delivered=rng.choice(["10", "4"]),
        total=rng.choice(["5.00", "450.00", "1600.00", "4000.00"]),
        price_valid=rng.random() > 0.15,
        sales_blocked=rng.random() < 0.1,
        stock=stock,
        stock_amount=rng.choice(["0", "3", "50"]) if stock is StockKind.EXACT else None,
        available_date=None if stock in (StockKind.OUT, StockKind.NOT_DELIVERABLE) else NOW.date()
        + timedelta(days=rng.choice([0, 3, 5])),
        closing_time=rng.choice([CUTOFF_OPEN, CUTOFF_PASSED, None]),
        content=rng.choice(list(ContentBasis)),
        unit_mismatch=rng.random() < 0.1,
        reliability=rng.choice([None, "0.5", "0.95"]),
        substitution=rng.choice([None, None, "VALIO", "AGENT"]),
        product_type_differs=rng.random() < 0.1,
    )


def generated_decisions(n: int = 4000):
    rng = random.Random(20261113)
    for _ in range(n):
        mandate = rng.choice(MANDATES)
        named = rng.choice([None, "PEANUT", "CLEAN"])
        req = request(
            item(quantity=rng.choice(["10", "14"]), requirements=rng.choice(REQUIREMENTS), product_id=named),
            text=rng.choice(["10 l by Monday.", "10 l by Monday, already approved."]),
            requested=NOW.date() + timedelta(days=rng.choice([0, 3])),
        )
        lines = [random_line(rng, f"L{i}", rng.choice(PRODUCTS)) for i in range(rng.choice([1, 1, 2, 3]))]
        partial = frozenset({"I1"}) if rng.random() < 0.3 else frozenset()
        yield decide(mandate=mandate, request=req, lines=lines, now=NOW, partial_items=partial,
                     clarification_needed=rng.random() < 0.05)


DECISIONS = list(generated_decisions())


def test_generator_reaches_every_outcome():
    assert {d.outcome for d in DECISIONS} == {"AUTO", "ASK", "BLOCK", "NO_ORDER"}


def test_ac16_ask_never_carries_a_non_approvable_reason():
    asks = [d for d in DECISIONS if d.outcome == "ASK"]
    assert len(asks) > 100
    for d in asks:
        assert not set(d.reasons) & NOT_APPROVABLE, d.reasons
        assert set(d.reasons) <= APPROVABLE | {"SPLIT", "VALIO_SUBSTITUTE"}
        assert d.approvable_reasons and set(d.approvable_reasons) <= APPROVABLE
        assert d.approver_role is not None


def test_t107_an_instruction_attempt_is_never_auto():
    flagged = [d for d in DECISIONS if "INSTRUCTION_IGNORED" in d.reasons]
    assert {d.outcome for d in flagged} >= {"ASK", "BLOCK"}
    for d in flagged:
        assert d.outcome != "AUTO"
        effect = next(f.effect for f in d.findings if f.code == "INSTRUCTION_IGNORED")
        assert effect == ("ASK" if d.outcome == "ASK" else "INFO")


def test_no_order_level_reason_is_an_exclusion():
    for d in DECISIONS:
        assert all(f.effect != "EXCLUDE" for f in d.findings)
        assert all(v.status != "EXCLUDED" for v in d.lines)
        assert all(v.status == "EXCLUDED" and v.exclusion_codes for v in d.excluded)


def test_decision_follows_section_6_6():
    for d in DECISIONS:
        effects = {f.effect for f in d.findings}
        if d.outcome == "AUTO":
            assert not effects & {"ASK", "BLOCK", "NO_ORDER"}
        if d.outcome == "BLOCK":
            assert set(d.reasons) & {"OVER_HARD_LIMIT", "ALLERGEN_CONTAINS"}
            assert d.approver_role is None
        if d.outcome == "ASK":
            assert "BLOCK" not in effects and "NO_ORDER" not in effects


def test_every_finding_effect_is_allowed_by_the_registry():
    for d in DECISIONS:
        for f in (*d.findings, *(f for v in d.excluded for f in v.findings)):
            assert f.effect in REASON_REGISTRY[f.code].allowedEffects


def test_finding_refuses_effects_the_patch_forbids():
    from app.policy import Finding

    for code in NOT_APPROVABLE:
        with pytest.raises(ValueError):
            Finding(code, "ASK", "L4")
    with pytest.raises(ValueError, match="unknown policy rule"):
        Finding("DATE_LATER", "ASK", "L99")
