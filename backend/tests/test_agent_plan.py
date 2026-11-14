"""T10 unit tests for `backend/app/agent/plan.py`: §7.6 ranking, Valio-substitute re-check, and agent substitutes.

All committed tests use synthetic hand-made records (patch P7).
"""

from datetime import date, datetime
from decimal import Decimal

from app.agent.plan import (
    CandidateObservation,
    build_shortlist_offer_items,
    check_valio_substitutes,
    is_compliant_substitute,
    normalise_item_for_planning,
    rank_candidates,
    requested_type_match,
)
from app.catalog.normalise import normalise_product
from app.catalog.search import search_products
from app.contracts import (
    Preference,
    Product,
    ReasonCode,
    RequestItem,
    Requirement,
    TrustLabel,
    TrustSource,
)
from app.policy import M1, M2
from tests.synthetic.catalog_records import synthetic_product_record

MONDAY = date(2026, 11, 16)
LATER_DAY = date(2026, 11, 18)
GLUTEN_FREE = Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")
LACTOSE_FREE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")


def _make_milk(
    product_id: str,
    *,
    name: str = "Synthetic Milk Drink 1 l lactose free",
    claims: list[str] | None = None,
    labels: list[str] | None = None,
    country: str = "fi",
    reliability: float | None = 1.0,
    co2: float | None = 1.1,
    declared_pack: bool = True,
    substitutions: list[str] | None = None,
) -> Product:
    unit_convs = (
        [{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}]
        if declared_pack
        else []
    )
    rec = synthetic_product_record(
        product_id=product_id,
        category="10400",
        names={"en": name},
        allergens=[("AM", "CONTAINS")],
        claims=claims if claims is not None else ["FREE_FROM_LACTOSE"],
        labels=labels or [],
        country_of_origin=country,
        unit_conversions=unit_convs,
        substitutions=substitutions or [],
    )
    return normalise_product(rec, reliability=reliability, co2=co2)


def _obs(
    product_id: str,
    price: str = "1.20",
    *,
    comp_price: str | None = None,
    stock_tier: str = "PLENTY",
    avail_qty: str | None = "1000",
    delivery_date: date = MONDAY,
    reasons: tuple[ReasonCode, ...] = (),
    unknown_live_fact: bool = False,
) -> CandidateObservation:
    up = Decimal(price)
    cp = Decimal(comp_price) if comp_price is not None else up
    aq = Decimal(avail_qty) if avail_qty is not None else None
    facts = ()
    if unknown_live_fact:
        facts = (
            TrustLabel(
                fact="custom.unknown",
                value=None,
                display="Unknown",
                level="UNKNOWN",
                source=TrustSource(system="valio-aimo.availability-api", field="x", retrievedAt=None),
            ),
        )
    return CandidateObservation(
        product_id=product_id,
        unit_price_excl_vat=up,
        comparison_price=cp,
        comparison_unit="l",
        vat_rate=Decimal("13.5"),
        stock_tier=stock_tier,  # type: ignore[arg-type]
        available_quantity=aq,
        delivery_date=delivery_date,
        closing_time=datetime.fromisoformat("2026-11-15T17:00:00+02:00"),
        reasons=reasons,
        facts=facts,
    )


def test_ranking_criterion_1_declared_requirements_beat_estimated_pack_content():
    """§7.6 #1: all requirements and pack content >= DECLARED ranks above ESTIMATE pack content."""
    p_declared = _make_milk("SYN-DECL", name="Synthetic Milk 1 l lactose free", declared_pack=True)
    p_estimate = _make_milk("SYN-EST", name="Synthetic Milk 1 l lactose free", declared_pack=False)
    assert p_declared.contentPerSalesUnit.level == "DECLARED"
    assert p_estimate.contentPerSalesUnit.level == "ESTIMATE"

    item = RequestItem(
        itemId="I1",
        what="milk",
        quantity=Decimal("100"),
        unit="l",
        requirements=[LACTOSE_FREE],
    )
    # Even if SYN-EST is slightly cheaper, SYN-DECL wins on Criterion 1 (>= DECLARED).
    observations = {
        "SYN-EST": _obs("SYN-EST", "0.95"),
        "SYN-DECL": _obs("SYN-DECL", "1.01"),
    }
    ranked = rank_candidates(
        [p_estimate, p_declared],
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations=observations,
    )
    assert [r.productId for r in ranked] == ["SYN-DECL", "SYN-EST"]
    assert ranked[0].allRequirementsDeclared is True
    assert ranked[1].allRequirementsDeclared is False


def test_ranking_criterion_2_deliverable_by_date_with_verified_quantity():
    """§7.6 #2: deliverable on time with VERIFIED stock beats supplier stock and later delivery."""
    p_verified = _make_milk("SYN-VER")
    p_supplier = _make_milk("SYN-SUP")
    p_later = _make_milk("SYN-LATER")

    item = RequestItem(
        itemId="I1",
        what="milk",
        quantity=Decimal("100"),
        unit="l",
        requirements=[LACTOSE_FREE],
    )
    observations = {
        "SYN-LATER": _obs("SYN-LATER", "0.80", stock_tier="PLENTY", delivery_date=LATER_DAY, reasons=(ReasonCode.DATE_LATER,)),
        "SYN-SUP": _obs("SYN-SUP", "0.90", stock_tier="SUPPLIER", avail_qty=None, reasons=(ReasonCode.SUPPLIER_STOCK_UNKNOWN,)),
        "SYN-VER": _obs("SYN-VER", "1.01", stock_tier="EXACT", avail_qty="200"),
    }
    ranked = rank_candidates(
        [p_later, p_supplier, p_verified],
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations=observations,
    )
    assert [r.productId for r in ranked] == ["SYN-VER", "SYN-SUP", "SYN-LATER"]
    assert ranked[0].deliverableOnTimeVerified is True
    assert ranked[1].deliverableOnTimeVerified is False
    assert ranked[2].deliverableOnTimeVerified is False


def test_ranking_criterion_3_lowest_comparison_price_wins():
    """§7.6 #3: lowest comparison price (€/l, €/kg) wins when Criteria 1 and 2 tie."""
    p_expensive = _make_milk("SYN-140", name="Synthetic Eila 3 % milk drink 1 l lactose free")
    p_mid = _make_milk("SYN-110", name="Synthetic Skimmed milk drink 1 l lactose free")
    p_cheap = _make_milk("SYN-101", name="Synthetic Arki semi skimmed milk drink 1 l lactose free")

    item = RequestItem(
        itemId="I1",
        what="milk",
        quantity=Decimal("1000"),
        unit="l",
        requirements=[LACTOSE_FREE],
    )
    observations = {
        "SYN-140": _obs("SYN-140", "1.40"),
        "SYN-110": _obs("SYN-110", "1.10"),
        "SYN-101": _obs("SYN-101", "1.01"),
    }
    ranked = rank_candidates(
        [p_expensive, p_mid, p_cheap],
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations=observations,
    )
    assert [r.productId for r in ranked] == ["SYN-101", "SYN-110", "SYN-140"]
    assert [r.tier for r in ranked] == ["RECOMMENDED", "BEST", "BETTER"]
    assert ranked[0].comparisonPriceExclVat == Decimal("1.01")
    assert "+€0.09/l" in ranked[1].tradeoff


def test_ranking_criteria_4_5_6_reliability_preferences_and_unknown_count():
    """§7.6 #4–#6: higher reliability -> matched preferences/labels -> fewer UNKNOWN facts."""
    p_low_rel = _make_milk("SYN-REL-85", reliability=0.85)
    p_high_rel = _make_milk("SYN-REL-99", reliability=0.99)

    item = RequestItem(
        itemId="I1",
        what="milk",
        quantity=Decimal("50"),
        unit="l",
        requirements=[LACTOSE_FREE],
        preferences=[Preference(kind="ORGANIC", value="organic"), Preference(kind="ORIGIN", value="FI")],
    )
    obs_rel = {
        "SYN-REL-85": _obs("SYN-REL-85", "1.01"),
        "SYN-REL-99": _obs("SYN-REL-99", "1.01"),
    }
    ranked_rel = rank_candidates(
        [p_low_rel, p_high_rel],
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations=obs_rel,
    )
    assert [r.productId for r in ranked_rel] == ["SYN-REL-99", "SYN-REL-85"]

    # Criterion 5: preferences and labels when reliability and price tie
    p_plain = _make_milk("SYN-PLAIN", reliability=1.0, labels=[], country="se")
    p_organic_fi = _make_milk(
        "SYN-ORG-FI",
        name="Synthetic Organic Milk Drink 1 l lactose free",
        reliability=1.0,
        labels=["EU_ORGANIC_FARMING", "GOODS_FROM_FINLAND_BLUE_SWAN"],
        country="fi",
    )
    obs_pref = {
        "SYN-PLAIN": _obs("SYN-PLAIN", "1.01"),
        "SYN-ORG-FI": _obs("SYN-ORG-FI", "1.01"),
    }
    ranked_pref = rank_candidates(
        [p_plain, p_organic_fi],
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations=obs_pref,
    )
    assert [r.productId for r in ranked_pref] == ["SYN-ORG-FI", "SYN-PLAIN"]
    assert ranked_pref[0].matchedPreferences == 2

    # Criterion 6: fewer UNKNOWN facts when price, reliability and preferences tie
    p_more_unk = _make_milk("SYN-UNK-2", reliability=1.0, co2=None, country="")
    p_fewer_unk = _make_milk("SYN-UNK-0", reliability=1.0, co2=1.1, country="fi")
    item_no_pref = item.model_copy(update={"preferences": []})
    obs_unk = {
        "SYN-UNK-2": _obs("SYN-UNK-2", "1.01", unknown_live_fact=True),
        "SYN-UNK-0": _obs("SYN-UNK-0", "1.01"),
    }
    ranked_unk = rank_candidates(
        [p_more_unk, p_fewer_unk],
        item=item_no_pref,
        mandate=M1,
        requested_date=MONDAY,
        observations=obs_unk,
    )
    assert [r.productId for r in ranked_unk] == ["SYN-UNK-0", "SYN-UNK-2"]
    assert ranked_unk[0].unknownFactCount < ranked_unk[1].unknownFactCount


def test_valio_substitute_check_rejects_non_compliant_substitute_and_marks_trust_card():
    """Patch P8 B: Valio-listed substitute that fails a hard requirement -> SUBSTITUTE_REJECTED."""
    oat_ok = normalise_product(
        synthetic_product_record(
            product_id="SYN-OAT-GF",
            category="10402",
            names={"en": "Synthetic Oddlygood Oat Drink 1 l gluten free"},
            allergens=[("GO", "CONTAINS")],
            claims=["FREE_FROM_GLUTEN", "VEGAN"],
            substitutions=["SYN-OAT-BAD"],
        )
    )
    oat_bad = normalise_product(
        synthetic_product_record(
            product_id="SYN-OAT-BAD",
            category="10402",
            names={"en": "Synthetic Oatly Regular Oat Drink 1 l"},
            allergens=[("AW", "CONTAINS"), ("GO", "CONTAINS")],
            claims=["VEGAN"],
        )
    )
    products = {"SYN-OAT-GF": oat_ok, "SYN-OAT-BAD": oat_bad}

    checks = check_valio_substitutes(
        oat_ok,
        requirements=[GLUTEN_FREE],
        mandate=M2,
        products_by_id=products,
        item_id="I2",
    )
    assert len(checks) == 1
    chk = checks[0]
    assert chk.passed is False
    assert chk.productId == "SYN-OAT-GF"
    assert chk.substituteProductId == "SYN-OAT-BAD"
    assert ReasonCode.CLAIM_MISSING in chk.reasons
    assert ReasonCode.SUBSTITUTE_REJECTED in chk.reasons
    assert chk.failedRequirements == ["gluten-free"]
    assert "SYN-OAT-BAD" in chk.summary and "gluten-free" in chk.summary
    assert chk.trustLabel.fact == "substitute.valio"
    assert chk.excludedEntry is not None
    assert chk.excludedEntry.substituteFor == "SYN-OAT-GF"
    assert ReasonCode.SUBSTITUTE_REJECTED in chk.excludedEntry.reasons


def test_agent_substitute_requires_every_hard_requirement_to_be_proven():
    """Patch P2.2/P2.3: an agent-proposed substitute is only allowed if every hard requirement is proven."""
    named = _make_milk("SYN-NAMED")
    compliant_sub = _make_milk("SYN-SUB-OK")
    non_compliant_sub = _make_milk("SYN-SUB-NO-CLAIM", claims=[])

    ok, reasons, _ = is_compliant_substitute(compliant_sub, requirements=[LACTOSE_FREE], mandate=M1)
    assert ok is True and reasons == []

    bad, bad_reasons, _ = is_compliant_substitute(non_compliant_sub, requirements=[LACTOSE_FREE], mandate=M1)
    assert bad is False and ReasonCode.CLAIM_MISSING in bad_reasons

    # When SYN-NAMED is CUTOFF_PASSED, rank_candidates proposes SYN-SUB-OK with AGENT_SUBSTITUTE,
    # and never includes SYN-SUB-NO-CLAIM.
    item = RequestItem(
        itemId="I1",
        what="milk drink",
        quantity=Decimal("50"),
        unit="l",
        productId="SYN-NAMED",
        requirements=[LACTOSE_FREE],
    )
    observations = {
        "SYN-NAMED": _obs("SYN-NAMED", "1.00", stock_tier="SUPPLIER", reasons=(ReasonCode.CUTOFF_PASSED,)),
        "SYN-SUB-OK": _obs("SYN-SUB-OK", "1.01"),
        "SYN-SUB-NO-CLAIM": _obs("SYN-SUB-NO-CLAIM", "0.50"),
    }
    ranked = rank_candidates(
        [named, compliant_sub, non_compliant_sub],
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations=observations,
        named_product=named,
    )
    assert [r.productId for r in ranked] == ["SYN-SUB-OK"]
    assert ranked[0].substitutionSource == "AGENT"
    assert ranked[0].substituteFor == "SYN-NAMED"
    assert ReasonCode.AGENT_SUBSTITUTE in ranked[0].reasons


def test_normalise_item_for_planning_converts_cases_and_pack_sizes():
    """Row 8 (10 cases x 5 l = 50 l) and Row 24 (40 packs x 250 g = 10 kg) normalise cleanly."""
    case_milk = normalise_product(
        synthetic_product_record(
            product_id="465039",
            category="10400",
            sales_unit="KI",
            base_unit="ST",
            units=[{"unitId": "KI", "sizeInBaseUnits": 5}, {"unitId": "ST", "sizeInBaseUnits": 1}],
            unit_conversions=[],
            names={"en": "Synthetic Eila milk drink 1 l lactose free"},
            allergens=[("AM", "CONTAINS")],
            claims=["FREE_FROM_LACTOSE"],
        )
    )
    piece_milk = _make_milk("464306")
    pool = [case_milk, piece_milk]
    by_id = {p.productId: p for p in pool}

    item_row8 = RequestItem(
        itemId="I1",
        what="light milk drink",
        quantity=Decimal("10"),
        unit="cases",
        productId="465039",
        requirements=[LACTOSE_FREE],
        preferences=[Preference(kind="PACK_SIZE", value="1 l")],
    )
    s_res = search_products(item_row8, mandate=M1, products=pool)
    norm_item, named, notes = normalise_item_for_planning(item_row8, search_result=s_res, products_by_id=by_id)
    assert (norm_item.quantity, norm_item.unit, norm_item.productId) == (Decimal("50"), "l", "465039")
    assert named is not None and named.productId == "465039"
    assert any("50 l" in n for n in notes)

    shortlist_items = build_shortlist_offer_items(
        norm_item,
        ranked_candidates=rank_candidates(
            [piece_milk], item=norm_item, mandate=M1, requested_date=MONDAY, named_product=named
        ),
        live_excluded_ids=["465039"],
    )
    assert [oi.productId for oi in shortlist_items] == ["465039", "464306"]
    assert len(shortlist_items) <= 20


# ---- T34: product-type precision ---------------------------------------------------------------------

WHIP = "Synthetic whipping cream 1 l lactose free"


def _make_cream(product_id: str, name_en: str, name_fi: str) -> Product:
    rec = synthetic_product_record(
        product_id=product_id,
        category="10401",
        names={"en": name_en, "fi": name_fi},
        allergens=[("AM", "CONTAINS")],
        claims=["FREE_FROM_LACTOSE"],
        unit_conversions=[{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}],
    )
    return normalise_product(rec, reliability=1.0, co2=1.0)


def _cream_item(what: str = "whipping cream") -> RequestItem:
    return RequestItem(itemId="I1", what=what, quantity=Decimal(20), unit="l", requirements=[LACTOSE_FREE])


def test_t34_exact_type_outranks_a_cheaper_different_type():
    whipping = _make_cream("SYN-WHIP", WHIP, "Synteettinen vispikerma 1 l laktoositon")
    cooking_alt = _make_cream(
        "SYN-COOK-ALT", "Synthetic cooking cream alternative 15 % 1 l lactose free", "Synteettinen Ruoka 15 % 1 l"
    )
    ranked = rank_candidates(
        [cooking_alt, whipping],
        item=_cream_item(),
        mandate=M1,
        requested_date=MONDAY,
        observations={"SYN-WHIP": _obs("SYN-WHIP", "4.00"), "SYN-COOK-ALT": _obs("SYN-COOK-ALT", "1.00")},
    )
    assert [r.productId for r in ranked] == ["SYN-WHIP", "SYN-COOK-ALT"]
    assert ranked[0].substitutionSource is None and ReasonCode.AGENT_SUBSTITUTE not in ranked[0].reasons
    assert ranked[1].substitutionSource == "AGENT"
    assert ReasonCode.AGENT_SUBSTITUTE in ranked[1].reasons
    assert "cooking cream, not whipping cream" in ranked[1].tradeoff
    assert "plant-based or blended alternative, not dairy" in ranked[1].tradeoff


def test_t34_without_a_deliverable_exact_type_the_substitute_is_never_exact():
    whipping = _make_cream("SYN-WHIP", WHIP, "Synteettinen kuohukerma 1 l")
    cooking = _make_cream("SYN-COOK", "Synthetic cooking cream 15 % 1 l lactose free", "Synteettinen ruokakerma 1 l")
    ranked = rank_candidates(
        [whipping, cooking],
        item=_cream_item(),
        mandate=M1,
        requested_date=MONDAY,
        observations={
            "SYN-WHIP": _obs("SYN-WHIP", stock_tier="UNAVAILABLE", avail_qty="0"),
            "SYN-COOK": _obs("SYN-COOK"),
        },
    )
    assert [r.productId for r in ranked] == ["SYN-COOK"]
    assert ranked[0].substitutionSource == "AGENT"
    assert ReasonCode.AGENT_SUBSTITUTE in ranked[0].reasons
    assert "different product type: cooking cream, not whipping cream" in ranked[0].tradeoff


def test_t34_alternatives_are_fine_when_the_buyer_asked_for_one():
    oat_cream = _make_cream("SYN-OAT", "Synthetic oat cooking cream alternative 2 dl", "Synteettinen kaurakerma")
    for what, reqs in (("oat cooking cream", []), ("cooking cream", [Requirement(kind="CLAIM", code="VEGAN")])):
        item = RequestItem(itemId="I1", what=what, quantity=Decimal(1), unit="l", requirements=reqs)
        assert requested_type_match(oat_cream, item).exact, what
    assert not requested_type_match(oat_cream, _cream_item("cooking cream")).exact


def test_t34_unqualified_request_keeps_every_cream_type_exact():
    cooking = _make_cream("SYN-COOK", "Synthetic cooking cream 15 % 1 l", "Synteettinen ruokakerma 1 l")
    assert requested_type_match(cooking, _cream_item("cream")).exact
