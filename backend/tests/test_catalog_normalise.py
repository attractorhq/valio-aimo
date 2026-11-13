"""Synthetic unit tests for catalogue normalisation, TrustLabels, allergens, X99 (P4), K1/K2/K5, and categories."""

from decimal import Decimal

import pytest

from app.catalog.categories import (
    get_category_profile,
    is_food_category,
    matching_categories_for_type,
)
from app.catalog.normalise import (
    allergen_declaration,
    apply_price_ratio_content,
    is_proven_free_from_allergen,
    normalise_product,
    parse_content_from_name,
    product_fact,
    satisfies_claim,
)
from app.contracts.reasons import ReasonCode
from app.trust import (
    EU14_ALLERGEN_CODES,
    NON_EU14_ALLERGEN_CODES,
    is_eu14_allergen,
    trust_summary,
    x99_proves_free_from,
)
from tests.synthetic.catalog_records import synthetic_product_record


def test_multilingual_names_and_ingredients_normalisation():
    rec = synthetic_product_record(
        product_id="SYN-LANG",
        names={
            "en": "Synthetic Oat Drink 1 l",
            "fi": "Synteettinen kaurajuoma 1 l",
            "sv": "Syntetisk havredryck 1 l",
        },
        key_ingredients={"en": "water, oats 10%", "fi": "vesi, kaura 10%"},
        ingredients=[
            {"sequence": 1, "names": [{"language": "sv", "value": "vatten"}]},
            {"sequence": 2, "names": [{"language": "sv", "value": "havre 10%"}]},
        ],
    )
    prod = normalise_product(rec)
    assert prod.productId == "SYN-LANG"
    assert prod.name == "Synthetic Oat Drink 1 l"
    assert prod.names == {
        "en": "Synthetic Oat Drink 1 l",
        "fi": "Synteettinen kaurajuoma 1 l",
        "sv": "Syntetisk havredryck 1 l",
    }
    assert prod.ingredients["en"] == "water, oats 10%"
    assert prod.ingredients["fi"] == "vesi, kaura 10%"
    assert prod.ingredients["sv"] == "vatten, havre 10%"


@pytest.mark.parametrize("code", sorted(EU14_ALLERGEN_CODES))
def test_x99_proves_free_from_for_every_eu14_code_and_subcode(code: str):
    assert is_eu14_allergen(code)
    assert x99_proves_free_from(code)
    rec = synthetic_product_record(
        product_id="SYN-X99-EU14",
        allergens=[("X99", "CONTAINS")],
        non_allergens=[],
    )
    prod = normalise_product(rec)
    assert prod.hasAllergenDeclaration is True
    decl = allergen_declaration(prod, code)
    assert decl.status == "FREE_FROM"
    assert decl.evidence.level == "DECLARED"
    assert decl.evidence.method == "X99"

    ok_std, reason_std, _ = is_proven_free_from_allergen(prod, code, mode="STANDARD")
    ok_strict, reason_strict, _ = is_proven_free_from_allergen(prod, code, mode="STRICT")
    assert ok_std is True and reason_std is None
    assert ok_strict is True and reason_strict is None


@pytest.mark.parametrize(
    "code",
    ["ML", "NM", "NE", "NP", "NC", "CM", "BF", "PO", "NW", "NK", "BP", "PN", "SO", "UN", "ABD", "ADQ"],
)
def test_x99_proves_nothing_for_non_eu14_codes_per_p4(code: str):
    assert code in NON_EU14_ALLERGEN_CODES
    assert not is_eu14_allergen(code)
    assert not x99_proves_free_from(code)
    rec = synthetic_product_record(
        product_id="SYN-X99-NON-EU14",
        allergens=[],
        non_allergens=[("X99", "FREE_FROM")],
    )
    prod = normalise_product(rec)
    decl = allergen_declaration(prod, code)
    assert decl.status == "UNKNOWN"
    assert decl.evidence.level == "UNKNOWN"
    assert decl.evidence.display == "Unknown"

    ok_std, reason_std, _ = is_proven_free_from_allergen(prod, code, mode="STANDARD")
    ok_strict, reason_strict, _ = is_proven_free_from_allergen(prod, code, mode="STRICT")
    assert ok_std is False and reason_std == ReasonCode.ALLERGEN_UNKNOWN
    assert ok_strict is False and reason_strict == ReasonCode.ALLERGEN_UNKNOWN


def test_allergen_statuses_contains_may_contain_undeclared_free_from_implicit_and_unknown():
    rec = synthetic_product_record(
        product_id="SYN-ALG-MIX",
        allergens=[
            ("AM", "CONTAINS"),
            ("AN", "MAY_CONTAIN"),
            ("AS", "UNDECLARED"),
        ],
        non_allergens=[
            ("AE", "FREE_FROM"),
        ],
    )
    prod = normalise_product(rec)
    assert prod.hasAllergenDeclaration is True

    # CONTAINS -> EXCLUDE in both modes
    am = allergen_declaration(prod, "AM")
    assert am.status == "CONTAINS" and am.evidence.level == "DECLARED"
    assert is_proven_free_from_allergen(prod, "AM", "STANDARD")[:2] == (False, ReasonCode.ALLERGEN_CONTAINS)
    assert is_proven_free_from_allergen(prod, "AM", "STRICT")[:2] == (False, ReasonCode.ALLERGEN_CONTAINS)

    # MAY_CONTAIN -> EXCLUDE in both modes per Patch P2.3 / P2.4
    an = allergen_declaration(prod, "AN")
    assert an.status == "MAY_CONTAIN" and an.evidence.level == "DECLARED"
    assert is_proven_free_from_allergen(prod, "AN", "STANDARD")[:2] == (False, ReasonCode.ALLERGEN_MAY_CONTAIN)
    assert is_proven_free_from_allergen(prod, "AN", "STRICT")[:2] == (False, ReasonCode.ALLERGEN_MAY_CONTAIN)

    # UNDECLARED -> UNKNOWN -> EXCLUDE in both modes
    as_decl = allergen_declaration(prod, "AS")
    assert as_decl.status == "UNDECLARED" and as_decl.evidence.level == "UNKNOWN"
    assert is_proven_free_from_allergen(prod, "AS", "STANDARD")[:2] == (False, ReasonCode.ALLERGEN_UNKNOWN)
    assert is_proven_free_from_allergen(prod, "AS", "STRICT")[:2] == (False, ReasonCode.ALLERGEN_UNKNOWN)

    # Explicit FREE_FROM -> OK in both modes
    ae = allergen_declaration(prod, "AE")
    assert ae.status == "FREE_FROM" and ae.evidence.level == "DECLARED"
    assert is_proven_free_from_allergen(prod, "AE", "STANDARD")[:2] == (True, None)
    assert is_proven_free_from_allergen(prod, "AE", "STRICT")[:2] == (True, None)

    # Implicit NOT_LISTED -> OK in STANDARD, EXCLUDE (ALLERGEN_IMPLICIT) in STRICT per P2.4
    ay = allergen_declaration(prod, "AY")
    assert ay.status == "NOT_LISTED" and ay.evidence.level == "DECLARED"
    assert is_proven_free_from_allergen(prod, "AY", "STANDARD")[:2] == (True, None)
    assert is_proven_free_from_allergen(prod, "AY", "STRICT")[:2] == (False, ReasonCode.ALLERGEN_IMPLICIT)


def test_zero_allergen_declarations_and_all_undeclared_yield_unknown():
    rec_empty = synthetic_product_record(product_id="SYN-NO-ALG", allergens=[], non_allergens=[])
    prod_empty = normalise_product(rec_empty)
    assert prod_empty.hasAllergenDeclaration is False
    decl = allergen_declaration(prod_empty, "AW")
    assert decl.status == "UNKNOWN" and decl.evidence.level == "UNKNOWN"
    assert is_proven_free_from_allergen(prod_empty, "AW", "STANDARD")[:2] == (False, ReasonCode.ALLERGEN_UNKNOWN)
    assert is_proven_free_from_allergen(prod_empty, "AW", "STRICT")[:2] == (False, ReasonCode.ALLERGEN_UNKNOWN)

    rec_undecl = synthetic_product_record(
        product_id="SYN-ONLY-UNDECL",
        allergens=[("AM", "UNDECLARED"), ("AW", "UNDECLARED")],
        non_allergens=[],
    )
    prod_undecl = normalise_product(rec_undecl)
    assert prod_undecl.hasAllergenDeclaration is False
    assert is_proven_free_from_allergen(prod_undecl, "AE", "STANDARD")[:2] == (False, ReasonCode.ALLERGEN_UNKNOWN)


def test_lactose_free_vs_milk_free_and_low_lactose_traps():
    # Lactose-free milk contains milk (AM) and is proven lactose-free only by FREE_FROM_LACTOSE
    lf_milk = normalise_product(
        synthetic_product_record(
            product_id="SYN-LF-MILK",
            allergens=[("AM", "CONTAINS")],
            claims=["FREE_FROM_LACTOSE"],
        )
    )
    assert satisfies_claim(lf_milk, "FREE_FROM_LACTOSE")[:2] == (True, None)
    assert is_proven_free_from_allergen(lf_milk, "AM", "STANDARD")[:2] == (False, ReasonCode.ALLERGEN_CONTAINS)

    # LOW_LACTOSE never satisfies FREE_FROM_LACTOSE
    low_lac = normalise_product(
        synthetic_product_record(
            product_id="SYN-LOW-LAC",
            allergens=[("AM", "CONTAINS")],
            claims=["LOW_LACTOSE"],
        )
    )
    assert satisfies_claim(low_lac, "FREE_FROM_LACTOSE")[:2] == (False, ReasonCode.CLAIM_MISSING)
    assert is_proven_free_from_allergen(low_lac, "ML", "STANDARD")[:2] == (False, ReasonCode.CLAIM_MISSING)


def test_gluten_free_oats_allowed_while_aw_contains_triggers_k1_and_ml_contains_triggers_k2():
    # GO (oats) CONTAINS + FREE_FROM_GLUTEN is valid gluten-free oats, NOT K1
    gf_oat = normalise_product(
        synthetic_product_record(
            product_id="SYN-GF-OAT",
            category="10402",
            allergens=[("GO", "CONTAINS")],
            claims=["FREE_FROM_GLUTEN", "VEGAN"],
        )
    )
    assert satisfies_claim(gf_oat, "FREE_FROM_GLUTEN")[:2] == (True, None)
    assert all(f.conflict is None for f in gf_oat.facts)
    assert is_proven_free_from_allergen(gf_oat, "AW", "STANDARD")[:2] == (True, None)

    # AW CONTAINS + FREE_FROM_GLUTEN -> K1 conflict -> DATA_CONFLICT
    k1_prod = normalise_product(
        synthetic_product_record(
            product_id="SYN-K1",
            category="10402",
            allergens=[("AW", "CONTAINS"), ("GO", "CONTAINS")],
            claims=["FREE_FROM_GLUTEN"],
        )
    )
    assert satisfies_claim(k1_prod, "FREE_FROM_GLUTEN")[:2] == (False, ReasonCode.DATA_CONFLICT)
    assert is_proven_free_from_allergen(k1_prod, "AW", "STANDARD")[:2] == (False, ReasonCode.DATA_CONFLICT)
    assert trust_summary(k1_prod.facts)["CONFLICT"] >= 1

    # ML CONTAINS + FREE_FROM_LACTOSE -> K2 conflict -> DATA_CONFLICT
    k2_prod = normalise_product(
        synthetic_product_record(
            product_id="SYN-K2",
            category="17200",
            allergens=[("ML", "CONTAINS")],
            claims=["FREE_FROM_LACTOSE"],
        )
    )
    assert satisfies_claim(k2_prod, "FREE_FROM_LACTOSE")[:2] == (False, ReasonCode.DATA_CONFLICT)
    assert is_proven_free_from_allergen(k2_prod, "ML", "STANDARD")[:2] == (False, ReasonCode.DATA_CONFLICT)


def test_pack_content_methods_1_2_3_kg_variable_weight_and_k5_conflict():
    # Method 1: BASE_UNIT_OR_EACH volume × units[salesUnit].sizeInBaseUnits -> DECLARED
    m1_prod = normalise_product(
        synthetic_product_record(
            product_id="SYN-M1",
            sales_unit="KI",
            base_unit="ST",
            units=[{"unitId": "KI", "sizeInBaseUnits": 10}],
            unit_conversions=[
                {"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "MLT", "value": 500}}
            ],
        )
    )
    assert m1_prod.contentPerSalesUnit is not None
    assert m1_prod.contentPerSalesUnit.value == Decimal("5")
    assert m1_prod.contentPerSalesUnit.unit == "l"
    assert m1_prod.contentPerSalesUnit.level == "DECLARED"

    # Method 2 hook: agrees within 5% -> no K5 conflict
    agreed = apply_price_ratio_content(m1_prod, catalog_price="10.00", comparison_price="2.00", comparison_unit="L")
    content_fact = next(f for f in agreed.facts if f.fact == "content.perSalesUnit")
    assert content_fact.conflict is None

    # Method 2 hook: disagrees by > 5% -> K5 conflict
    disagreed = apply_price_ratio_content(m1_prod, catalog_price="10.00", comparison_price="1.00", comparison_unit="L")
    k5_fact = next(f for f in disagreed.facts if f.fact == "content.perSalesUnit")
    assert k5_fact.conflict is not None
    assert k5_fact.conflict.code == "K5"

    # Method 3: parsed from product name -> ESTIMATE
    m3_prod = normalise_product(
        synthetic_product_record(
            product_id="SYN-M3",
            names={"en": "12x Synthetic Gluten-Free Macaroni 400g"},
            sales_unit="KI",
            units=[{"unitId": "KI", "sizeInBaseUnits": 12}],
            unit_conversions=None,
        )
    )
    assert m3_prod.contentPerSalesUnit is not None
    assert m3_prod.contentPerSalesUnit.value == Decimal("4.8")
    assert m3_prod.contentPerSalesUnit.unit == "kg"
    assert m3_prod.contentPerSalesUnit.level == "ESTIMATE"
    assert m3_prod.contentPerSalesUnit.method == "name"

    assert parse_content_from_name("Oat cream 2,5 dl") == m3_prod.contentPerSalesUnit.model_copy(
        update={"value": Decimal("0.25"), "unit": "l"}
    )

    # Method 2 overrides Method 3 when price ratio is supplied
    upgraded = apply_price_ratio_content(m3_prod, catalog_price="24.00", comparison_price="5.00", comparison_unit="KG")
    assert upgraded.contentPerSalesUnit is not None
    assert upgraded.contentPerSalesUnit.value == Decimal("4.8")
    assert upgraded.contentPerSalesUnit.method == "catalogPrice.value / comparisonPrice.value"

    # salesUnit KG and variableUnit count as 1 kg DECLARED
    kg_prod = normalise_product(
        synthetic_product_record(product_id="SYN-KG", sales_unit="KG", base_unit="KG")
    )
    assert kg_prod.contentPerSalesUnit is not None
    assert (kg_prod.contentPerSalesUnit.value, kg_prod.contentPerSalesUnit.unit) == (Decimal("1"), "kg")
    assert kg_prod.contentPerSalesUnit.level == "DECLARED"

    var_prod = normalise_product(
        synthetic_product_record(product_id="SYN-VAR", sales_unit="ST", variable_unit=True)
    )
    assert var_prod.variableWeight is True
    assert var_prod.contentPerSalesUnit is not None
    assert (var_prod.contentPerSalesUnit.value, var_prod.contentPerSalesUnit.unit) == (Decimal("1"), "kg")

    # Unknown content -> None + UNKNOWN TrustLabel with display "Unknown"
    unk_prod = normalise_product(
        synthetic_product_record(
            product_id="SYN-UNK",
            names={"en": "Synthetic Box With No Size"},
            unit_conversions=None,
            country_of_origin=None,
        )
    )
    assert unk_prod.contentPerSalesUnit is None
    unk_fact = next(f for f in unk_prod.facts if f.fact == "content.perSalesUnit")
    assert unk_fact.level == "UNKNOWN" and unk_fact.display == "Unknown"
    assert product_fact(unk_prod, "reliability").level == "UNKNOWN"
    assert product_fact(unk_prod, "reliability").display == "Unknown"
    assert product_fact(unk_prod, "co2").level == "UNKNOWN"
    assert product_fact(unk_prod, "co2").display == "Unknown"
    assert product_fact(unk_prod, "origin").level == "UNKNOWN"
    assert product_fact(unk_prod, "origin").display == "Unknown"


def test_reliability_co2_origin_substitutions_deposits_and_categories():
    rec = synthetic_product_record(
        product_id="SYN-FACTS",
        category="10402",
        country_of_origin="fi",
        labels=["GOODS_FROM_FINLAND_BLUE_SWAN"],
        substitutions=["SYN-SUB-1"],
        deposits=[{"material": "SYN-DEP-1", "quantity": 1, "unit": "ST"}],
    )
    prod = normalise_product(rec, reliability=0.997, co2=1.1234)
    assert prod.isFood is True
    assert prod.countryOfOrigin == "fi"
    assert prod.substitutions == ["SYN-SUB-1"]
    assert len(prod.deposits) == 1
    assert prod.deposits[0].productId == "SYN-DEP-1"
    assert prod.deposits[0].quantity == Decimal("1")

    by_fact = {f.fact: f for f in prod.facts}
    assert by_fact["reliability"].level == "ESTIMATE"
    assert by_fact["reliability"].display == "Delivered in full 99.7 % historically"
    assert by_fact["co2"].level == "ESTIMATE"
    assert by_fact["co2"].display == "1.123 kg CO2e/kg · product-group average"
    assert by_fact["origin"].level == "DECLARED"
    assert by_fact["substitute.valio"].level == "VERIFIED"
    assert by_fact["label.GOODS_FROM_FINLAND_BLUE_SWAN"].level == "DECLARED"

    # Food vs non-food and category profiles
    assert is_food_category("10400") is True
    assert is_food_category("21380") is True
    assert is_food_category("64000") is False
    assert is_food_category("90010") is False
    assert get_category_profile("21380").productType == "pasta"
    assert "10402" in matching_categories_for_type("kaurajuoma")
    assert "21380" in matching_categories_for_type("macaroni")
