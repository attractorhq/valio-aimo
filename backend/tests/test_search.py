"""Tests for static searchProducts (§7.3, P2.4, P4): FI/EN/SV vocabulary, compounds, non-food filter, and excluded[]."""

import time

import pytest

from app.catalog.build import DEFAULT_ARTIFACT_PATH, DEFAULT_RAW_DIR, load_catalog
from app.catalog.normalise import normalise_product
from app.catalog.search import SearchProductsRequest, search_products
from app.contracts import ReasonCode, RequestItem, Requirement
from app.policy import M1, M2
from tests.synthetic.catalog_records import synthetic_product_record


def _build_synthetic_pool():
    return [
        # Compliant gluten-free oat drink (GO CONTAINS + FREE_FROM_GLUTEN) with Valio substitute SYN-OAT-SUB
        normalise_product(
            synthetic_product_record(
                product_id="SYN-OAT-OK",
                category="10402",
                names={
                    "en": "Synthetic Oat Drink 1 l gluten free",
                    "fi": "Synteettinen tuorekaurajuoma 1 l gluteeniton",
                    "sv": "Syntetisk färsk havredryck 1 l glutenfri",
                },
                allergens=[("GO", "CONTAINS")],
                claims=["FREE_FROM_GLUTEN", "VEGAN"],
                substitutions=["SYN-OAT-SUB"],
            )
        ),
        # Non-gluten-free oat drink listed as Valio substitute -> CLAIM_MISSING + SUBSTITUTE_REJECTED
        normalise_product(
            synthetic_product_record(
                product_id="SYN-OAT-SUB",
                category="10402",
                names={
                    "en": "Synthetic Regular Oat Drink 1 l",
                    "fi": "Synteettinen kaurajuoma kevyt 1 l",
                    "sv": "Syntetisk havredryck 1 l",
                },
                allergens=[("AW", "CONTAINS"), ("GO", "CONTAINS")],
                claims=["VEGAN"],
            )
        ),
        # Conflicting oat drink (AW CONTAINS + FREE_FROM_GLUTEN -> K1 DATA_CONFLICT)
        normalise_product(
            synthetic_product_record(
                product_id="SYN-OAT-K1",
                category="10402",
                names={
                    "en": "Synthetic Barista Oat Drink 0.5 l gluten free",
                    "fi": "Synteettinen Barista kaurajuoma 0,5 l gluteeniton",
                    "sv": "Syntetisk Barista havredryck 0,5 l glutenfri",
                },
                allergens=[("AW", "CONTAINS"), ("GO", "CONTAINS")],
                claims=["FREE_FROM_GLUTEN", "VEGAN"],
            )
        ),
        # Compliant lactose-free milk drink (contains AM milk)
        normalise_product(
            synthetic_product_record(
                product_id="SYN-MILK-LF",
                category="10400",
                names={
                    "en": "Synthetic Semi Skimmed Milk Drink 1 l lactose free",
                    "fi": "Synteettinen kevytmaitojuoma 1 l laktoositon",
                    "sv": "Syntetisk lättmjölksdryck 1 l laktosfri",
                },
                allergens=[("AM", "CONTAINS")],
                claims=["FREE_FROM_LACTOSE"],
            )
        ),
        # Low-lactose milk drink (LOW_LACTOSE != FREE_FROM_LACTOSE)
        normalise_product(
            synthetic_product_record(
                product_id="SYN-MILK-LOW",
                category="10400",
                names={
                    "en": "Synthetic Low Lactose Milk Drink 1 l",
                    "fi": "Synteettinen vähälaktoosinen maitojuoma 1 l",
                    "sv": "Syntetisk låglaktos mjölkdryck 1 l",
                },
                allergens=[("AM", "CONTAINS")],
                claims=["LOW_LACTOSE"],
            )
        ),
        # Conflicting milk drink (ML CONTAINS + FREE_FROM_LACTOSE -> K2 DATA_CONFLICT)
        normalise_product(
            synthetic_product_record(
                product_id="SYN-MILK-K2",
                category="10400",
                names={"en": "Synthetic Conflicting Milk Drink 1 l lactose free"},
                allergens=[("AM", "CONTAINS"), ("ML", "CONTAINS")],
                claims=["FREE_FROM_LACTOSE"],
            )
        ),
        # Non-food item with "milk" in name -> must be excluded from food queries
        normalise_product(
            synthetic_product_record(
                product_id="SYN-NON-FOOD",
                category="64000",
                names={
                    "en": "Synthetic Milk Carton Holder Plastic",
                    "fi": "Synteettinen maitoteline muovia",
                },
                allergens=[],
                non_allergens=[],
                claims=["FREE_FROM_LACTOSE"],
            )
        ),
    ]


def test_p2_4_allergen_branches_on_synthetic_products():
    products = [
        normalise_product(
            synthetic_product_record(
                product_id="P-EXPLICIT-FREE",
                category="21380",
                names={"en": "Synthetic Pasta Explicit Free"},
                allergens=[],
                non_allergens=[("AE", "FREE_FROM")],
            )
        ),
        normalise_product(
            synthetic_product_record(
                product_id="P-X99",
                category="21380",
                names={"en": "Synthetic Pasta X99"},
                allergens=[("X99", "CONTAINS")],
                non_allergens=[],
            )
        ),
        normalise_product(
            synthetic_product_record(
                product_id="P-IMPLICIT",
                category="21380",
                names={"en": "Synthetic Pasta Implicit"},
                allergens=[("AM", "CONTAINS")],
                non_allergens=[],
            )
        ),
        normalise_product(
            synthetic_product_record(
                product_id="P-CONTAINS",
                category="21380",
                names={"en": "Synthetic Pasta Contains Egg"},
                allergens=[("AE", "CONTAINS")],
                non_allergens=[],
            )
        ),
        normalise_product(
            synthetic_product_record(
                product_id="P-MAY-CONTAIN",
                category="21380",
                names={"en": "Synthetic Pasta May Contain Egg"},
                allergens=[("AE", "MAY_CONTAIN")],
                non_allergens=[],
            )
        ),
        normalise_product(
            synthetic_product_record(
                product_id="P-UNDECLARED",
                category="21380",
                names={"en": "Synthetic Pasta Undeclared Egg"},
                allergens=[("AE", "UNDECLARED"), ("AM", "CONTAINS")],
                non_allergens=[],
            )
        ),
        normalise_product(
            synthetic_product_record(
                product_id="P-NO-DECL",
                category="21380",
                names={"en": "Synthetic Pasta No Declaration"},
                allergens=[],
                non_allergens=[],
            )
        ),
    ]

    # 1. STANDARD mode: avoid AE (egg, EU-14)
    res_std = search_products(
        "pasta",
        requirements=[Requirement(kind="AVOID_ALLERGEN", code="AE")],
        allergen_mode="STANDARD",
        products=products,
    )
    assert [c.productId for c in res_std.candidates] == ["P-EXPLICIT-FREE", "P-IMPLICIT", "P-X99"]
    ex_std = {e.productId: e.reasons for e in res_std.excluded}
    assert ex_std["P-CONTAINS"] == [ReasonCode.ALLERGEN_CONTAINS]
    assert ex_std["P-MAY-CONTAIN"] == [ReasonCode.ALLERGEN_MAY_CONTAIN]
    assert ex_std["P-UNDECLARED"] == [ReasonCode.ALLERGEN_UNKNOWN]
    assert ex_std["P-NO-DECL"] == [ReasonCode.ALLERGEN_UNKNOWN]

    # 2. STRICT mode: avoid AE (implicit NOT_LISTED becomes ALLERGEN_IMPLICIT)
    res_strict = search_products(
        "pasta",
        requirements=[Requirement(kind="AVOID_ALLERGEN", code="AE")],
        allergen_mode="STRICT",
        products=products,
    )
    assert [c.productId for c in res_strict.candidates] == ["P-EXPLICIT-FREE", "P-X99"]
    ex_strict = {e.productId: e.reasons for e in res_strict.excluded}
    assert ex_strict["P-IMPLICIT"] == [ReasonCode.ALLERGEN_IMPLICIT]
    assert ex_strict["P-NO-DECL"] == [ReasonCode.ALLERGEN_UNKNOWN]

    # 3. X99 on non-EU-14 code (NM corn) -> ALLERGEN_UNKNOWN even in STANDARD mode (Patch P4)
    res_nm = search_products(
        "pasta",
        requirements=[Requirement(kind="AVOID_ALLERGEN", code="NM")],
        allergen_mode="STANDARD",
        products=products,
    )
    ex_nm = {e.productId: e.reasons for e in res_nm.excluded}
    assert ex_nm["P-X99"] == [ReasonCode.ALLERGEN_UNKNOWN]


def test_multilingual_queries_finnish_compounds_and_non_food_exclusion():
    pool = _build_synthetic_pool()

    # English, Finnish compound ("tuorekaurajuomaa"), and Swedish queries all match SYN-OAT-OK
    for q in ("gluten-free oat drink", "gluteenitonta tuorekaurajuomaa", "glutenfri havredryck"):
        res = search_products(q, products=pool)
        assert [c.productId for c in res.candidates] == ["SYN-OAT-OK"]
        ex = {e.productId: e for e in res.excluded}
        assert "SYN-OAT-SUB" in ex
        assert ReasonCode.CLAIM_MISSING in ex["SYN-OAT-SUB"].reasons
        assert ReasonCode.SUBSTITUTE_REJECTED in ex["SYN-OAT-SUB"].reasons
        assert ex["SYN-OAT-SUB"].substituteFor == "SYN-OAT-OK"
        assert ex["SYN-OAT-K1"].reasons == [ReasonCode.DATA_CONFLICT]

    # Lactose-free milk drink in EN, FI ("laktoositon kevytmaitojuoma"), SV ("laktosfri lättmjölksdryck")
    for q in ("lactose-free milk drink", "laktoositon kevytmaitojuoma", "laktosfri lättmjölksdryck"):
        res = search_products(q, products=pool)
        assert [c.productId for c in res.candidates] == ["SYN-MILK-LF"]
        ex = {e.productId: e.reasons for e in res.excluded}
        assert ex["SYN-MILK-LOW"] == [ReasonCode.CLAIM_MISSING]
        assert ex["SYN-MILK-K2"] == [ReasonCode.DATA_CONFLICT]
        # Non-food item is excluded from food requests
        assert "SYN-NON-FOOD" not in {c.productId for c in res.candidates}
        assert "SYN-NON-FOOD" not in ex

    # Milk-free != lactose-free: SYN-MILK-LF contains AM (milk) so it is excluded with ALLERGEN_CONTAINS
    milk_free = search_products("milk-free milk drink", products=pool)
    assert milk_free.candidates == []
    ex_mf = {e.productId: e.reasons for e in milk_free.excluded}
    assert ex_mf["SYN-MILK-LF"] == [ReasonCode.ALLERGEN_CONTAINS]


def test_request_item_and_search_request_contracts():
    pool = _build_synthetic_pool()
    item = RequestItem(
        itemId="I2",
        what="oat drink",
        quantity="80",
        unit="l",
        requirements=[Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")],
    )
    res_item = search_products(item, mandate=M2, products=pool)
    assert [c.productId for c in res_item.candidates] == ["SYN-OAT-OK"]
    assert all(e.itemId == "I2" for e in res_item.excluded)

    req = SearchProductsRequest(
        query="oat drink",
        requirements=[Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")],
        allergenMode="STRICT",
        itemId="I2",
    )
    res_req = search_products(req, products=pool)
    assert [c.productId for c in res_req.candidates] == ["SYN-OAT-OK"]


def test_real_catalog_gluten_free_oat_drink_strict_pasta_and_latency():
    if not DEFAULT_ARTIFACT_PATH.exists() and not (DEFAULT_RAW_DIR / "products.json").exists():
        pytest.skip("Real catalogue data not present on this machine")

    catalog = load_catalog()

    # Warm index once, then measure per-query latency (< 100 ms required)
    search_products("milk", mandate=M1, products=catalog)

    t0 = time.perf_counter()
    oat_res = search_products("gluten-free oat drink", mandate=M2, item_id="I2", products=catalog)
    oat_ms = (time.perf_counter() - t0) * 1000.0
    assert oat_ms < 100.0

    cand_ids = [c.productId for c in oat_res.candidates]
    assert "468465" in cand_ids
    assert "469040" in cand_ids
    assert "474136" in cand_ids

    oat_ex = {e.productId: e for e in oat_res.excluded}
    assert "460825" in oat_ex
    assert ReasonCode.CLAIM_MISSING in oat_ex["460825"].reasons
    assert "470863" in oat_ex
    assert ReasonCode.CLAIM_MISSING in oat_ex["470863"].reasons
    assert ReasonCode.SUBSTITUTE_REJECTED in oat_ex["470863"].reasons
    assert oat_ex["470863"].substituteFor == "468465"
    assert "471881" in oat_ex
    assert ReasonCode.DATA_CONFLICT in oat_ex["471881"].reasons

    # Scenario B item 1 in STRICT mode (M2): 470699 and 460302 excluded with ALLERGEN_UNKNOWN
    t1 = time.perf_counter()
    pasta_res = search_products("gluten-free macaroni", mandate=M2, item_id="I1", products=catalog)
    pasta_ms = (time.perf_counter() - t1) * 1000.0
    assert pasta_ms < 100.0

    pasta_cand_ids = [c.productId for c in pasta_res.candidates]
    assert "474235" in pasta_cand_ids
    assert "473895" in pasta_cand_ids
    assert "475795" in pasta_cand_ids
    assert "471108" in pasta_cand_ids

    pasta_ex = {e.productId: e for e in pasta_res.excluded}
    assert "470699" in pasta_ex
    assert ReasonCode.ALLERGEN_UNKNOWN in pasta_ex["470699"].reasons
    assert "460302" in pasta_ex
    assert ReasonCode.ALLERGEN_UNKNOWN in pasta_ex["460302"].reasons

    # Scenario A in STANDARD mode (M1): 464306 and 465039 in shortlist under 100 ms
    t2 = time.perf_counter()
    milk_res = search_products("lactose-free milk drink", mandate=M1, item_id="I1", products=catalog)
    milk_ms = (time.perf_counter() - t2) * 1000.0
    assert milk_ms < 100.0
    milk_cand_ids = [c.productId for c in milk_res.candidates]
    assert "464306" in milk_cand_ids
    assert "465039" in milk_cand_ids

    # T33: 'cheese' + avoid AM in STANDARD and STRICT excludes 471304 (DATA_CONFLICT)
    # and 473013 (ALLERGEN_CONTAINS), while plant-based alternatives 473985 and 466836 remain candidates.
    avoid_am = [Requirement(kind="AVOID_ALLERGEN", code="AM")]
    for mandate in (M1, M2):
        cheese_res = search_products("cheese", requirements=avoid_am, mandate=mandate, products=catalog)
        cheese_cand_ids = {c.productId for c in cheese_res.candidates}
        cheese_ex = {e.productId: e.reasons for e in cheese_res.excluded}
        assert "471304" not in cheese_cand_ids
        assert "473013" not in cheese_cand_ids
        assert ReasonCode.DATA_CONFLICT in cheese_ex["471304"]
        assert ReasonCode.ALLERGEN_CONTAINS in cheese_ex["473013"]
        assert "473985" in cheese_cand_ids
        assert "466836" in cheese_cand_ids
