"""Tests for static searchProducts (§7.3, P2.4, P4): FI/EN/SV vocabulary, compounds, non-food filter, and excluded[]."""

import time
from datetime import date
from decimal import Decimal

import pytest

from app.catalog.build import DEFAULT_ARTIFACT_PATH, DEFAULT_RAW_DIR, load_catalog
from app.catalog.normalise import normalise_product
from app.catalog.search import SearchProductsRequest, search_products
from app.contracts import Preference, ReasonCode, RequestItem, Requirement
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


# ---- T34: product-type precision ---------------------------------------------------------------------

from app.agent.plan import CandidateObservation, rank_candidates, requested_type_match  # noqa: E402
from app.catalog.categories import product_type_match  # noqa: E402


@pytest.mark.parametrize(
    ("query", "names", "concept", "exact", "difference"),
    [
        ("whipping cream", ("Synthetic cooking cream 15 % 1 l",), "cream", False, "cooking cream, not whipping cream"),
        ("whipping cream", ("Synthetic whipping cream 1 l UHT",), "cream", True, None),
        ("kuohukerma", ("Synteettinen vispikerma 2 dl",), "cream", True, None),
        ("vispikerma", ("Synteettinen ruokakerma 2 dl",), "cream", False, "cooking cream, not whipping cream"),
        ("whipping cream", ("Synthetic low fat cream 1 l",), "cream", False, "low-fat cream, not whipping cream"),
        ("whipping cream", ("Synthetic cream 1 l",), "cream", False, "not whipping cream"),
        ("cream", ("Synthetic coffee cream 1 l",), "cream", True, None),
        ("fresh whipping cream", ("Synthetic whipping cream 1 l UHT",), "cream", False, "UHT, not fresh (not UHT)"),
        ("UHT milk", ("Synthetic milk 1 l",), "milk", False, "not UHT"),
        ("oat drink", ("Synthetic oat cooking base 2 dl",), "oat_drink", False, "not drink"),
        ("cooking cream", ("Synthetic cooking cream alternative 15 %",), "cream", False,
         "plant-based or blended alternative, not dairy"),
        ("tuorejuusto", ("Synteettinen tuorejuusto UHT",), "cheese", True, None),
        ("macaroni", ("Synthetic oat macaroni",), "macaroni", True, None),  # not a dairy concept
    ],
)
def test_t34_product_type_match(query, names, concept, exact, difference):
    match = product_type_match(query, names, concept_id=concept)
    assert match.exact is exact
    if difference:
        assert difference in match.differences


def test_t34_milk_free_or_vegan_request_accepts_alternatives():
    names = ("Synthetic oat whipping alternative 2 dl",)
    assert not product_type_match("whipping cream", names, concept_id="cream").exact
    assert product_type_match("whipping cream", names, concept_id="cream", requirement_codes=("AM",)).exact
    assert product_type_match("whipping cream", names, concept_id="cream", requirement_codes=("VEGAN",)).exact
    assert product_type_match("plant-based whipping cream", names, concept_id="cream").exact


def _cream(product_id: str, en: str, fi: str):
    return normalise_product(
        synthetic_product_record(
            product_id=product_id,
            category="10401",
            names={"en": en, "fi": fi},
            allergens=[("AM", "CONTAINS")],
            claims=["FREE_FROM_LACTOSE"],
            unit_conversions=[{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}],
        )
    )


def test_t34_search_lists_exact_types_before_other_creams():
    pool = [
        _cream("SYN-A-COOK", "Synthetic cooking cream 1 l lactose free", "Synteettinen ruokakerma 1 l laktoositon"),
        _cream("SYN-B-ALT", "Synthetic cream alternative 1 l lactose free", "Synteettinen Ruoka 1 l laktoositon"),
        _cream("SYN-C-WHIP", "Synthetic whipping cream 1 l lactose free", "Synteettinen kuohukerma 1 l laktoositon"),
    ]
    res = search_products("lactose-free whipping cream", mandate=M1, products=pool)
    assert [c.productId for c in res.candidates][0] == "SYN-C-WHIP"


def _real_catalog_or_skip():
    if not DEFAULT_ARTIFACT_PATH.exists() and not (DEFAULT_RAW_DIR / "products.json").exists():
        pytest.skip("Real catalogue data not present on this machine")
    return load_catalog()


def test_t34_real_catalog_whipping_cream_is_never_a_cooking_cream_or_alternative():
    catalog = _real_catalog_or_skip()
    item = RequestItem(itemId="I1", what="whipping cream", quantity="20", unit="l",
                       requirements=[Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")])
    res = search_products("lactose-free whipping cream", mandate=M1, item_id="I1", products=catalog)
    exact = [c for c in res.candidates if requested_type_match(c, item).exact]
    others = [c for c in res.candidates if not requested_type_match(c, item).exact]
    assert exact, "the catalogue has lactose-free whipping creams"
    assert res.candidates[: len(exact)] == exact  # exact types fill the shortlist first

    def obs(product, *, deliverable):
        return CandidateObservation(
            product_id=product.productId, unit_price_excl_vat=Decimal("1.00"), comparison_price=Decimal("1.00"),
            comparison_unit="l", vat_rate=Decimal("13.5"), stock_tier="PLENTY" if deliverable else "UNAVAILABLE",
            available_quantity=Decimal(1000) if deliverable else Decimal(0), delivery_date=date(2026, 11, 16),
            closing_time=None, reasons=(), facts=(),
        )

    # Whipping cream deliverable: it is recommended, even when a cooking cream would be cheaper.
    observations = {c.productId: obs(c, deliverable=True) for c in res.candidates}
    ranked = rank_candidates(res.candidates, item=item, mandate=M1, requested_date=date(2026, 11, 16),
                             observations=observations)
    assert requested_type_match(next(c for c in res.candidates if c.productId == ranked[0].productId), item).exact
    assert ranked[0].substitutionSource is None

    # No whipping cream deliverable: whatever is offered is an agent substitute (ASK), never exact (AUTO).
    observations = {c.productId: obs(c, deliverable=c in others) for c in res.candidates}
    ranked = rank_candidates(res.candidates, item=item, mandate=M1, requested_date=date(2026, 11, 16),
                             observations=observations)
    if ranked:
        assert all(r.substitutionSource == "AGENT" for r in ranked)
        assert all(ReasonCode.AGENT_SUBSTITUTE in r.reasons for r in ranked)


def test_t34_real_catalog_gluten_free_macaroni_ranks_macaroni_before_fusilli():
    catalog = _real_catalog_or_skip()
    res = search_products("gluten-free macaroni", mandate=M2, item_id="I1", products=catalog)
    item = RequestItem(itemId="I1", what="macaroni", quantity="90", unit="kg",
                       requirements=[Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")])
    observations = {
        c.productId: CandidateObservation(
            product_id=c.productId, unit_price_excl_vat=Decimal("1.00"), comparison_price=Decimal("1.00"),
            comparison_unit="kg", vat_rate=Decimal("13.5"), stock_tier="PLENTY", available_quantity=Decimal(1000),
            delivery_date=date(2026, 11, 16), closing_time=None, reasons=(), facts=(),
        )
        for c in res.candidates
    }
    ranked = rank_candidates(res.candidates, item=item, mandate=M2, requested_date=date(2026, 11, 16),
                             observations=observations)
    assert ranked and "macaroni" in ranked[0].name.lower() and ranked[0].substitutionSource is None
    for r in ranked:
        if "fusilli" in r.name.lower():
            assert r.substitutionSource == "AGENT"


# ---- T24: fat % preference narrowing -----------------------------------------------------------------


def _milk_variant(product_id: str, en: str, fi: str, claims: list[str] | None = None):
    return normalise_product(
        synthetic_product_record(
            product_id=product_id,
            category="10400",
            names={"en": en, "fi": fi},
            allergens=[("AM", "CONTAINS")],
            claims=claims if claims is not None else ["FREE_FROM_LACTOSE"],
            unit_conversions=[{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}],
        )
    )


def test_t24_synthetic_fat_percent_preference_drops_proven_different_and_ranks_unknown_after_matches():
    pool = [
        _milk_variant("SYN-MILK-3PCT", "Synthetic 3 % milk drink 1 l lactose free", "Synteettinen 3 % maitojuoma 1 l"),
        _milk_variant("SYN-MILK-WHOLE", "Synthetic whole milk 1 l lactose free", "Synteettinen täysmaito 1 l"),
        _milk_variant(
            "SYN-MILK-SKIM",
            "Synthetic skimmed milk drink 1 l lactose free",
            "Synteettinen rasvaton maitojuoma 1 l",
            claims=["FREE_FROM_LACTOSE", "FREE_FROM_FAT"],
        ),
        _milk_variant(
            "SYN-MILK-SEMI",
            "Synthetic semi skimmed milk drink 1 l lactose free",
            "Synteettinen kevytmaitojuoma 1 l laktoositon",
        ),
        _milk_variant(
            "SYN-MILK-15",
            "Synthetic milk drink 1.5 % 1 l lactose free",
            "Synteettinen maitojuoma 1,5 % 1 l laktoositon",
        ),
        _milk_variant(
            "SYN-MILK-UNK",
            "Synthetic milk drink 1 l lactose free",
            "Synteettinen maitojuoma 1 l laktoositon",
        ),
    ]

    item_any = RequestItem(
        itemId="I1",
        what="milk",
        quantity="1000",
        unit="l",
        requirements=[Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")],
    )
    res_any = search_products(item_any, mandate=M1, products=pool)
    assert "SYN-MILK-3PCT" in [c.productId for c in res_any.candidates]

    item_15 = item_any.model_copy(update={"preferences": [Preference(kind="FAT_PERCENT", value="1.5")]})
    res_15 = search_products(item_15, mandate=M1, products=pool)
    cand_ids = [c.productId for c in res_15.candidates]
    assert cand_ids == ["SYN-MILK-15", "SYN-MILK-SEMI", "SYN-MILK-UNK"]
    assert "SYN-MILK-3PCT" not in cand_ids
    assert "SYN-MILK-WHOLE" not in cand_ids
    assert "SYN-MILK-SKIM" not in cand_ids


def test_t24_real_catalog_fat_percent_1_5_drops_463335_and_keeps_464306():
    catalog = _real_catalog_or_skip()
    item_any = RequestItem(
        itemId="I1",
        what="milk",
        quantity="1000",
        unit="l",
        requirements=[Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")],
    )
    res_any = search_products(item_any, mandate=M1, products=catalog)
    any_ids = [c.productId for c in res_any.candidates]
    assert "464306" in any_ids
    assert "463335" in any_ids

    item_15 = item_any.model_copy(update={"preferences": [Preference(kind="FAT_PERCENT", value="1.5")]})
    res_15 = search_products(item_15, mandate=M1, products=catalog)
    ids_15 = [c.productId for c in res_15.candidates]
    assert "464306" in ids_15
    assert "465039" in ids_15
    assert "463335" not in ids_15
    assert "464401" not in ids_15
    assert "464073" not in ids_15


# ---- T39: product-type precision II ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "names", "concept", "difference"),
    [
        ("fresh salmon fillet", ("Synthetic salmon fillet 2 pcs frozen",), None, "frozen, not fresh"),
        ("tuoretta lohifileetä", ("Synteettinen lohifilee pakaste",), None, "frozen, not fresh"),
        ("fresh salmon fillet", ("Synthetic cold smoked salmon fillet",), None, "smoked, not fresh"),
        ("fresh salmon fillet", ("Synthetic marinated salmon fillet",), None, "cured or marinated, not fresh"),
        ("frozen salmon fillet", ("Synthetic salmon fillet 10 kg",), None, "not frozen"),
        ("Emmental cheese", ("Synthetic mozzarella-fontal crumbled cheese",), "cheese", "mozzarella, not Emmental"),
        ("Emmental cheese", ("Synthetic Emmental-Edam grated cheese mix",), "cheese", "mixed with Edam, not only Emmental"),
        ("Emmental cheese", ("Synthetic processed cheese slices Emmentaler",), "cheese", "processed cheese, not Emmental"),
        ("voita", ("Synthetic catering margarine 60 400 g",), "butter", "margarine or fat spread, not butter"),
        ("butter", ("Synthetic butter-blend salted 400 g",), "butter",
         "mixed with margarine or fat spread, not only butter"),
    ],
)
def test_t39_type_differences(query, names, concept, difference):
    match = product_type_match(query, names, concept_id=concept)
    assert not match.exact and difference in match.differences


@pytest.mark.parametrize(
    ("query", "names", "concept"),
    [
        ("fresh salmon fillet", ("Synthetic salmon fillet C, ASC, apx. 10 kg",), None),
        ("frozen salmon fillet", ("Synthetic salmon fillet 2 pcs frozen",), None),
        ("smoked salmon", ("Synthetic kylmäsavulohi siivu",), None),
        ("Emmental cheese", ("Synthetic Emmental cheese slices 1 kg",), "cheese"),
        ("emmentaalia", ("Synteettinen emmentaljuusto",), "cheese"),
        ("voita", ("Synteettinen voi 500 g suolaamaton",), "butter"),
        ("margarine", ("Synthetic catering margarine 60",), "butter"),
        ("salmon fillet", ("Synthetic salmon fillet 2 pcs frozen",), None),  # no qualifier stated: any state
    ],
)
def test_t39_exact_types(query, names, concept):
    assert product_type_match(query, names, concept_id=concept).exact


def test_t39_concept_less_requests_need_every_product_noun():
    def what(text):
        return RequestItem(itemId="I1", what=text, quantity="60", unit="kg")

    flounder = _cream("SYN-FLOUNDER", "Synthetic flounder fillet fresh 5 kg", "Synteettinen punakampelafile tuore")
    salmon = _cream("SYN-SALMON", "Synthetic salmon fillet C 10 kg", "Synteettinen lohifilee C 10 kg")
    assert requested_type_match(salmon, what("fresh salmon fillet")).exact
    assert requested_type_match(flounder, what("fresh salmon fillet")).differences == ("not salmon",)


def test_t39_real_catalog_fresh_salmon_emmental_and_butter():
    catalog = _real_catalog_or_skip()

    def recommended(query, what, unit="kg"):
        item = RequestItem(itemId="I1", what=what, quantity="60", unit=unit)
        res = search_products(query, mandate=M1, item_id="I1", products=catalog)
        observations = {
            c.productId: CandidateObservation(
                product_id=c.productId, unit_price_excl_vat=Decimal("1.00"), comparison_price=Decimal("1.00"),
                comparison_unit="kg", vat_rate=Decimal("14"), stock_tier="PLENTY", available_quantity=Decimal(1000),
                delivery_date=date(2026, 11, 16), closing_time=None, reasons=(), facts=(),
            )
            for c in res.candidates
        }
        ranked = rank_candidates(res.candidates, item=item, mandate=M1, requested_date=date(2026, 11, 16),
                                 observations=observations)
        by_id = {c.productId: c for c in res.candidates}
        return item, ranked, by_id

    item, ranked, by_id = recommended("fresh salmon fillet", "fresh salmon fillet")
    assert ranked and ranked[0].exactType and ranked[0].substitutionSource is None
    for r in ranked:
        names = " ".join((by_id[r.productId].name, *by_id[r.productId].names.values())).lower()
        if "frozen" in names or "pakaste" in names:
            assert not r.exactType and r.substitutionSource == "AGENT" and "frozen, not fresh" in r.tradeoff

    item, ranked, by_id = recommended("Emmental cheese", "Emmental cheese")
    assert ranked and "emmental" in ranked[0].name.lower() and ranked[0].exactType

    for query in ("butter", "voita"):
        item, ranked, by_id = recommended(query, query)
        top = by_id[ranked[0].productId]
        top_text = " ".join((top.name, *top.names.values())).lower()
        assert ranked[0].exactType and "margari" not in top_text, query
