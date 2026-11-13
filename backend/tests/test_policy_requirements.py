"""L4 hard requirements per patch P2.4, X99 per P4, STRICT declaration (§6.3), O2 named-product block."""

import pytest

from app.contracts import Requirement
from app.policy import M1, M2, allergen_status, check_requirements, hard_requirements
from app.policy.requirements import claim_conflicts, conflict_is_decisive
from tests.synthetic.policy import GLUTEN_FREE, LACTOSE_FREE, MILK_FREE, label, product

STANDARD = M1
STRICT = M2


def codes(findings):
    return [(f.code, f.effect, f.detail) for f in findings]


def avoid(code: str) -> Requirement:
    return Requirement(kind="AVOID_ALLERGEN", code=code)


# --- allergen evidence ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("allergens", "declared", "code", "expected"),
    [
        ({"AM": "CONTAINS"}, None, "AM", "CONTAINS"),
        ({"AM": "MAY_CONTAIN"}, None, "AM", "MAY_CONTAIN"),
        ({"AM": "FREE_FROM"}, None, "AM", "FREE_FROM"),
        ({"AM": "UNDECLARED"}, None, "AM", "UNKNOWN"),
        ({"AM": "UNKNOWN"}, None, "AM", "UNKNOWN"),
        ({"AM": "NOT_LISTED"}, None, "AM", "NOT_LISTED"),
        ({"AE": "FREE_FROM"}, None, "AM", "NOT_LISTED"),  # declaration exists, AM not mentioned
        ({}, False, "AM", "UNKNOWN"),  # no declaration at all
        # X99 (P4): proves the EU-14 groups and their subcodes, nothing else.
        ({"X99": "CONTAINS"}, None, "AM", "FREE_FROM"),
        ({"X99": "FREE_FROM"}, None, "AW", "FREE_FROM"),
        ({"X99": "FREE_FROM"}, None, "SA", "FREE_FROM"),
        ({"X99": "FREE_FROM"}, None, "ML", "NOT_LISTED"),  # lactose is not EU-14
        ({"X99": "FREE_FROM"}, None, "NM", "NOT_LISTED"),
        ({"X99": "FREE_FROM", "AM": "CONTAINS"}, None, "AM", "CONTAINS"),  # explicit wins
        # Group/subcode relations.
        ({"UW": "CONTAINS"}, None, "AW", "CONTAINS"),
        ({"GO": "MAY_CONTAIN"}, None, "AW", "MAY_CONTAIN"),
        ({"AW": "FREE_FROM", "UW": "MAY_CONTAIN"}, None, "AW", "MAY_CONTAIN"),
        ({"AW": "CONTAINS", "UW": "MAY_CONTAIN"}, None, "AW", "CONTAINS"),
        ({"AW": "MAY_CONTAIN", "GO": "CONTAINS"}, None, "AW", "CONTAINS"),
        ({"SA": "FREE_FROM"}, None, "AN", "NOT_LISTED"),  # a free member does not prove the group
        ({"AW": "FREE_FROM"}, None, "UW", "FREE_FROM"),
        ({"AW": "CONTAINS"}, None, "UW", "UNKNOWN"),  # gluten cereals declared, which one is open
        ({"AW": "CONTAINS", "X99": "FREE_FROM"}, None, "UW", "UNKNOWN"),
    ],
)
def test_allergen_status(allergens, declared, code, expected):
    assert allergen_status(product(allergens=allergens, declared=declared), code) == expected


@pytest.mark.parametrize("order", [("FREE_FROM", "MAY_CONTAIN"), ("MAY_CONTAIN", "FREE_FROM")])
def test_repeated_code_keeps_the_most_severe_status(order):
    declarations = [d for status in order for d in product(allergens={"AM": status}).allergens]
    p = product(allergens={"AE": "FREE_FROM"}).model_copy(update={"allergens": declarations})
    assert allergen_status(p, "AM") == "MAY_CONTAIN"


# --- avoid allergen: STANDARD vs STRICT (P2.4) ---------------------------------------------------

@pytest.mark.parametrize(
    ("allergens", "declared", "standard", "strict"),
    [
        ({"AM": "CONTAINS"}, None, "ALLERGEN_CONTAINS", "ALLERGEN_CONTAINS"),
        ({"AM": "MAY_CONTAIN"}, None, "ALLERGEN_MAY_CONTAIN", "ALLERGEN_MAY_CONTAIN"),  # both modes (P2.3)
        ({"AM": "UNDECLARED"}, None, "ALLERGEN_UNKNOWN", "ALLERGEN_UNKNOWN"),
        ({}, False, "ALLERGEN_UNKNOWN", "ALLERGEN_UNKNOWN"),  # both modes (P2.3)
        ({"AE": "CONTAINS"}, None, None, "ALLERGEN_IMPLICIT"),  # STANDARD: declared and not listed = OK
        ({"AM": "FREE_FROM"}, None, None, None),
        ({"X99": "CONTAINS"}, None, None, None),
    ],
)
def test_avoid_allergen_by_mode(allergens, declared, standard, strict):
    p = product(allergens=allergens, declared=declared)
    for mandate, expected in ((STANDARD, standard), (STRICT, strict)):
        found = check_requirements(p, [MILK_FREE], mandate)
        assert [f.code for f in found] == ([expected] if expected else [])
        assert all(f.effect == "EXCLUDE" and f.rule == "L4" for f in found)


def test_lactose_free_is_not_milk_free():
    # Lactose-free and contains milk: passes "lactose-free", fails "milk-free".
    p = product(allergens={"AM": "CONTAINS"}, claims=("FREE_FROM_LACTOSE",))
    assert check_requirements(p, [LACTOSE_FREE], STANDARD) == ()
    assert [f.code for f in check_requirements(p, [MILK_FREE], STANDARD)] == ["ALLERGEN_CONTAINS"]


def test_lactose_free_needs_the_claim_not_x99_or_low_lactose():
    for p in (product(allergens={"X99": "FREE_FROM"}), product(allergens={"AE": "FREE_FROM"}, claims=("LOW_LACTOSE",))):
        assert codes(check_requirements(p, [LACTOSE_FREE], STANDARD)) == [
            ("CLAIM_MISSING", "EXCLUDE", "FREE_FROM_LACTOSE")
        ]


def test_claim_can_come_from_a_declared_label():
    assert check_requirements(product(allergens={"AE": "FREE_FROM"}, labels=("VEGAN",)),
                              [Requirement(kind="CLAIM", code="VEGAN")], STANDARD) == ()


def test_gluten_free_is_a_claim_rule_so_declared_oats_pass():
    oat = product(allergens={"GO": "CONTAINS"}, claims=("FREE_FROM_GLUTEN",))
    assert check_requirements(oat, [GLUTEN_FREE], STRICT) == ()
    plain_oat = product(allergens={"AW": "CONTAINS", "GO": "CONTAINS"})
    assert [f.code for f in check_requirements(plain_oat, [GLUTEN_FREE], STRICT)] == ["CLAIM_MISSING"]


def test_strict_requires_a_declaration_on_food_whatever_the_request():
    undeclared = product(declared=False, claims=("FREE_FROM_GLUTEN",))
    assert codes(check_requirements(undeclared, [GLUTEN_FREE], STRICT)) == [
        ("ALLERGEN_UNKNOWN", "EXCLUDE", "NO_DECLARATION")
    ]
    assert check_requirements(undeclared, [GLUTEN_FREE], STANDARD) == ()
    assert check_requirements(product(declared=False, is_food=False), [], STRICT) == ()
    # Unknown food status is treated as food.
    assert len(check_requirements(product(declared=False, is_food=None), [], STRICT)) == 1
    # One ALLERGEN_UNKNOWN is enough; the declaration rule does not repeat it.
    assert [f.detail for f in check_requirements(undeclared, [MILK_FREE], STRICT)] == ["AM"]


def test_mandate_forbidden_allergens_are_hard_requirements():
    mandate = STANDARD.model_copy(update={"forbiddenAllergens": ["AP"]})
    assert hard_requirements([MILK_FREE, MILK_FREE], mandate) == (MILK_FREE, avoid("AP"))
    peanut = product(allergens={"AP": "CONTAINS"})
    assert codes(check_requirements(peanut, [], mandate)) == [("ALLERGEN_CONTAINS", "EXCLUDE", "AP")]


def test_named_product_with_forbidden_allergen_blocks_o2():
    mandate = STANDARD.model_copy(update={"forbiddenAllergens": ["AP"]})
    peanut = product(allergens={"AP": "CONTAINS", "AM": "CONTAINS"})
    found = check_requirements(peanut, [MILK_FREE], mandate, named=True)
    # Request requirement: EXCLUDE (offer an alternative). Mandate-forbidden: BLOCK, never swap silently.
    assert [(f.code, f.effect, f.rule, f.detail) for f in found] == [
        ("ALLERGEN_CONTAINS", "EXCLUDE", "L4", "AM"),
        ("ALLERGEN_CONTAINS", "BLOCK", "O2", "AP"),
    ]
    may = product(allergens={"AP": "MAY_CONTAIN"})
    assert [f.effect for f in check_requirements(may, [], mandate, named=True)] == ["EXCLUDE"]


# --- K1/K2 ---------------------------------------------------------------------------------------

def test_claim_conflicts_detected_from_data_and_labels():
    k1 = product(allergens={"AW": "CONTAINS"}, claims=("FREE_FROM_GLUTEN",))
    k2 = product(allergens={"ML": "CONTAINS"}, claims=("FREE_FROM_LACTOSE",))
    oat = product(allergens={"GO": "CONTAINS"}, claims=("FREE_FROM_GLUTEN",))  # oats alone: not K1
    labelled = product(allergens={"AE": "FREE_FROM"}, facts=(label("claim.X", "K2"), label("x", "K5"), label("y")))
    assert claim_conflicts(k1) == {"K1"}
    assert claim_conflicts(k2) == {"K2"}
    assert claim_conflicts(oat) == frozenset()
    assert claim_conflicts(labelled) == {"K2"}


@pytest.mark.parametrize(
    ("conflict", "requirement", "decisive"),
    [
        ("K1", GLUTEN_FREE, True),
        ("K1", avoid("AW"), True),
        ("K1", avoid("UW"), True),
        ("K1", LACTOSE_FREE, False),
        ("K1", MILK_FREE, False),
        ("K2", LACTOSE_FREE, True),
        ("K2", avoid("ML"), True),
        ("K2", GLUTEN_FREE, False),
    ],
)
def test_conflict_is_decisive_only_for_the_relied_on_fact(conflict, requirement, decisive):
    assert conflict_is_decisive(conflict, [requirement]) is decisive
