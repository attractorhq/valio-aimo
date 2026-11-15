"""T69: Split top-up for partial stock: second compliant product with price-rise guard.

All committed tests use synthetic hand-made records (patch P7).
"""

from decimal import Decimal

from test_proposals_gateway import (
    GLUTEN_FREE,
    LACTOSE_FREE,
    MONDAY,
    NOW,
    MutableSyntheticProvider,
    _decl,
    price_row,
    stock_row,
    syn_product,
)

from app.agent.plan import (
    CandidateObservation,
    build_shortlist_offer_items,
    normalise_item_for_planning,
    rank_candidates,
)
from app.catalog.search import search_products
from app.contracts import (
    GetOfferRequest,
    OfferItem,
    Product,
    ReasonCode,
    RequestItem,
    StructuredRequest,
)
from app.eval.runner import ReferenceFixtureProvider, build_reference_products, run_eval_suite
from app.gateway.proposals import (
    ProposalGateway,
    format_approval_statement,
    split_detail_lines,
)
from app.policy import DEMO_MANDATES, M1, MandateBook

PASTA_METHOD = "unitConversions.BASE_UNIT_OR_EACH.netWeight x units.PSS.sizeInBaseUnits"
CREAM_METHOD = "unitConversions.BASE_UNIT_OR_EACH.volume x units.ST.sizeInBaseUnits"


def _named_product(
    code: str,
    name: str,
    *,
    sales_unit: str = "PSS",
    content: tuple[str, str] = ("5", "kg"),
    method: str = PASTA_METHOD,
    claims: tuple[str, ...] = ("FREE_FROM_GLUTEN",),
    category: str = "synthetic",
) -> Product:
    prod = syn_product(
        code,
        sales_unit=sales_unit,
        content=content,
        method=method,
        claims=claims,
        allergens=(_decl("AW", "FREE_FROM"),),
    )
    fi_name = f"{name} pasta" if method == PASTA_METHOD else name
    return prod.model_copy(update={"name": name, "names": {"en": name, "fi": fi_name}, "category": category})


def _obs(
    code: str,
    *,
    unit_price: str,
    comp_price: str,
    unit: str = "kg",
    stock_tier: str = "EXACT",
    avail_qty: int | str = 100,
) -> CandidateObservation:
    return CandidateObservation(
        product_id=code,
        unit_price_excl_vat=Decimal(unit_price),
        comparison_price=Decimal(comp_price),
        comparison_unit=unit,
        vat_rate=Decimal("14"),
        stock_tier=stock_tier,
        available_quantity=Decimal(str(avail_qty)),
        delivery_date=MONDAY,
        closing_time=None,
        reasons=(),
        facts=(),
    )


def test_partial_a_plus_compliant_b_produces_split_with_split_and_agent_substitute():
    """Partial A (70 kg in stock) + compliant B (sufficient stock) for 120 kg request ->
    split version with SPLIT and AGENT_SUBSTITUTE on line 2 and plain approval statement."""
    prod_a = _named_product("SYN-A", "A", content=("5", "kg"))
    prod_b = _named_product("SYN-B", "B", content=("5", "kg"))
    products = {"SYN-A": prod_a, "SYN-B": prod_b}

    provider = MutableSyntheticProvider()
    # A: €10.00 / 5 kg bag = €2.00/kg, 14 bags = 70 kg in stock
    # B: €12.00 / 5 kg bag = €2.40/kg (+€0.40/kg), 30 bags = 150 kg in stock
    provider.prices["SYN-A"] = price_row("SYN-A", "10.00")
    provider.stocks["SYN-A"] = stock_row("SYN-A", 14, sales_unit="PSS")
    provider.prices["SYN-B"] = price_row("SYN-B", "12.00")
    provider.stocks["SYN-B"] = stock_row("SYN-B", 30, sales_unit="PSS")

    item = RequestItem(
        itemId="I1",
        what="pasta",
        quantity=Decimal("120"),
        unit="kg",
        requirements=[GLUTEN_FREE],
    )
    observations = {
        "SYN-A": _obs("SYN-A", unit_price="10.00", comp_price="2.00", avail_qty=14),
        "SYN-B": _obs("SYN-B", unit_price="12.00", comp_price="2.40", avail_qty=30),
    }
    ranked = rank_candidates(
        [prod_a, prod_b],
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations=observations,
    )
    offer_items = build_shortlist_offer_items(item, ranked_candidates=ranked)
    assert [oi.productId for oi in offer_items] == ["SYN-A", "SYN-B"]

    gateway = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer = gateway.get_offer(
        GetOfferRequest(customerId=M1.customerId, requestedDate=MONDAY, items=offer_items)
    ).offer
    req = StructuredRequest(
        requestText="120 kg gluten-free pasta by Monday.",
        requestedDate=MONDAY,
        items=[item],
    )
    proposal = gateway.create_proposal(M1.customerId, offer.offerId, offer.offerVersion, req, now=NOW)

    # Split is ASK (never AUTO), with SPLIT and AGENT_SUBSTITUTE on the second line and proposal
    assert proposal.decision == "ASK"
    assert proposal.state == "PENDING_APPROVAL"
    assert ReasonCode.SPLIT in proposal.reasons
    assert ReasonCode.AGENT_SUBSTITUTE in proposal.reasons
    assert ReasonCode.PARTIAL_AVAILABILITY not in proposal.reasons

    assert len(proposal.lines) == 2
    line_a, line_b = proposal.lines
    assert (line_a.productId, line_a.quantitySalesUnits, line_a.deliveredQuantity.value) == (
        "SYN-A",
        Decimal("14"),
        Decimal("70"),
    )
    assert (line_b.productId, line_b.quantitySalesUnits, line_b.deliveredQuantity.value) == (
        "SYN-B",
        Decimal("10"),
        Decimal("50"),
    )
    assert line_b.substitutionSource == "AGENT"
    assert line_b.substituteFor == "SYN-A"
    assert ReasonCode.SPLIT in line_b.reasons
    assert ReasonCode.AGENT_SUBSTITUTE in line_b.reasons

    statement = format_approval_statement(proposal, mandate=M1, today=NOW.date())
    assert statement is not None
    assert "Approving means 70 kg of A + 50 kg of B (€0.40/kg more)" in statement


def test_split_rounds_second_line_up_to_whole_sales_units():
    """When the shortfall is not an exact multiple of B's pack size, line 2 rounds up to whole sales units."""
    prod_a = _named_product("SYN-A", "A", content=("0.5", "kg"))
    prod_b = _named_product("SYN-B", "B", content=("5", "kg"))
    products = {"SYN-A": prod_a, "SYN-B": prod_b}

    provider = MutableSyntheticProvider()
    # A has 399 bags × 0.5 kg = 199.5 kg; shortfall for 300 kg is 100.5 kg -> B needs ceil(100.5 / 5) = 21 bags = 105 kg
    provider.prices["SYN-A"] = price_row("SYN-A", "2.50")
    provider.stocks["SYN-A"] = stock_row("SYN-A", 399, sales_unit="PSS")
    provider.prices["SYN-B"] = price_row("SYN-B", "25.00")
    provider.stocks["SYN-B"] = stock_row("SYN-B", 50, sales_unit="PSS")

    item = RequestItem(
        itemId="I1",
        what="penne pasta",
        quantity=Decimal("300"),
        unit="kg",
        requirements=[GLUTEN_FREE],
    )
    gateway = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer = gateway.get_offer(
        GetOfferRequest(
            customerId=M1.customerId,
            requestedDate=MONDAY,
            items=[
                OfferItem(itemId="I1", productId="SYN-A", quantity=Decimal("300"), unit="kg"),
                OfferItem(itemId="I1", productId="SYN-B", quantity=Decimal("300"), unit="kg"),
            ],
        )
    ).offer
    req = StructuredRequest(requestText="300 kg gluten-free penne pasta.", requestedDate=MONDAY, items=[item])
    proposal = gateway.create_proposal(M1.customerId, offer.offerId, offer.offerVersion, req, now=NOW)

    assert proposal.decision == "ASK"
    assert [(ln.productId, ln.quantitySalesUnits, ln.deliveredQuantity.value) for ln in proposal.lines] == [
        ("SYN-A", Decimal("399"), Decimal("199.5")),
        ("SYN-B", Decimal("21"), Decimal("105")),
    ]


def test_b_lacking_a_claim_produces_no_split_version():
    """When B lacks a required claim (FREE_FROM_GLUTEN), there is no split version — only partial A."""
    prod_a = _named_product("SYN-A", "A", content=("5", "kg"), claims=("FREE_FROM_GLUTEN",))
    prod_b_no_claim = _named_product("SYN-B-NOCLAIM", "B", content=("5", "kg"), claims=())
    products = {"SYN-A": prod_a, "SYN-B-NOCLAIM": prod_b_no_claim}

    provider = MutableSyntheticProvider()
    provider.prices["SYN-A"] = price_row("SYN-A", "10.00")
    provider.stocks["SYN-A"] = stock_row("SYN-A", 14, sales_unit="PSS")
    provider.prices["SYN-B-NOCLAIM"] = price_row("SYN-B-NOCLAIM", "10.00")
    provider.stocks["SYN-B-NOCLAIM"] = stock_row("SYN-B-NOCLAIM", 30, sales_unit="PSS")

    item = RequestItem(
        itemId="I1",
        what="pasta",
        quantity=Decimal("120"),
        unit="kg",
        requirements=[GLUTEN_FREE],
    )
    # 1. Planner path: rank_candidates drops B because it lacks FREE_FROM_GLUTEN
    ranked = rank_candidates(
        [prod_a, prod_b_no_claim],
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations={
            "SYN-A": _obs("SYN-A", unit_price="10.00", comp_price="2.00", avail_qty=14),
            "SYN-B-NOCLAIM": _obs("SYN-B-NOCLAIM", unit_price="10.00", comp_price="2.00", avail_qty=30),
        },
    )
    assert [rc.productId for rc in ranked] == ["SYN-A"]

    # 2. Gateway path: even if both A and B are sent in GetOfferRequest, create_proposal excludes B
    #    with CLAIM_MISSING and offers no split version (only partial A).
    gateway = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer = gateway.get_offer(
        GetOfferRequest(
            customerId=M1.customerId,
            requestedDate=MONDAY,
            items=[
                OfferItem(itemId="I1", productId="SYN-A", quantity=Decimal("120"), unit="kg"),
                OfferItem(itemId="I1", productId="SYN-B-NOCLAIM", quantity=Decimal("120"), unit="kg"),
            ],
        )
    ).offer
    req = StructuredRequest(requestText="120 kg gluten-free pasta.", requestedDate=MONDAY, items=[item])
    proposal = gateway.create_proposal(M1.customerId, offer.offerId, offer.offerVersion, req, now=NOW)

    assert [ln.productId for ln in proposal.lines] == ["SYN-A"]
    assert ReasonCode.SPLIT not in proposal.reasons
    assert ReasonCode.PARTIAL_AVAILABILITY in proposal.reasons
    assert any(
        e.productId == "SYN-B-NOCLAIM" and ReasonCode.CLAIM_MISSING in e.reasons for e in proposal.excluded
    )


def test_price_guard_adds_detail_line_when_second_product_is_over_10_pct_per_kg():
    """Price guard: B at +15 % per kg above A adds a detail line showing the percentage; +5 % does not."""
    prod_a = _named_product("SYN-A", "A", content=("1", "kg"))
    prod_b = _named_product("SYN-B", "B", content=("1", "kg"))
    products = {"SYN-A": prod_a, "SYN-B": prod_b}
    item = RequestItem(
        itemId="I1",
        what="pasta",
        quantity=Decimal("120"),
        unit="kg",
        requirements=[GLUTEN_FREE],
    )
    req = StructuredRequest(requestText="120 kg gluten-free pasta.", requestedDate=MONDAY, items=[item])

    # Case 1: B is +15 % per kg (€11.50/kg vs €10.00/kg) -> detail line present
    provider_15 = MutableSyntheticProvider()
    provider_15.prices["SYN-A"] = price_row("SYN-A", "10.00")
    provider_15.stocks["SYN-A"] = stock_row("SYN-A", 70, sales_unit="PSS")
    provider_15.prices["SYN-B"] = price_row("SYN-B", "11.50")
    provider_15.stocks["SYN-B"] = stock_row("SYN-B", 100, sales_unit="PSS")

    gw_15 = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider_15)
    offer_15 = gw_15.get_offer(
        GetOfferRequest(
            customerId=M1.customerId,
            requestedDate=MONDAY,
            items=[
                OfferItem(itemId="I1", productId="SYN-A", quantity=Decimal("120"), unit="kg"),
                OfferItem(itemId="I1", productId="SYN-B", quantity=Decimal("120"), unit="kg"),
            ],
        )
    ).offer
    prop_15 = gw_15.create_proposal(M1.customerId, offer_15.offerId, offer_15.offerVersion, req, now=NOW)

    assert prop_15.decision == "ASK"
    details_15 = split_detail_lines(prop_15)
    assert any("+15 % per kg" in d for d in details_15)
    guard_fact = next((f for f in prop_15.lines[1].facts if f.fact == "split.priceGuard"), None)
    assert guard_fact is not None and "+15 % per kg" in (guard_fact.display or "")
    stmt_15 = format_approval_statement(prop_15, mandate=M1, today=NOW.date())
    assert stmt_15 is not None
    assert "Approving means 70 kg of A + 50 kg of B (€1.50/kg more)" in stmt_15
    assert "+15 % per kg" in stmt_15

    # Case 2: B is +5 % per kg (€10.50/kg vs €10.00/kg) -> no price-guard detail line
    provider_5 = MutableSyntheticProvider()
    provider_5.prices["SYN-A"] = price_row("SYN-A", "10.00")
    provider_5.stocks["SYN-A"] = stock_row("SYN-A", 70, sales_unit="PSS")
    provider_5.prices["SYN-B"] = price_row("SYN-B", "10.50")
    provider_5.stocks["SYN-B"] = stock_row("SYN-B", 100, sales_unit="PSS")

    gw_5 = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider_5)
    offer_5 = gw_5.get_offer(
        GetOfferRequest(
            customerId=M1.customerId,
            requestedDate=MONDAY,
            items=[
                OfferItem(itemId="I1", productId="SYN-A", quantity=Decimal("120"), unit="kg"),
                OfferItem(itemId="I1", productId="SYN-B", quantity=Decimal("120"), unit="kg"),
            ],
        )
    ).offer
    prop_5 = gw_5.create_proposal(M1.customerId, offer_5.offerId, offer_5.offerVersion, req, now=NOW)

    # T100: B is the same product type within the 10 % price guard: a second supplier, not a substitute.
    assert prop_5.decision == "AUTO"
    assert ReasonCode.AGENT_SUBSTITUTE not in prop_5.reasons and ReasonCode.SPLIT in prop_5.reasons
    assert split_detail_lines(prop_5) == []
    assert all(f.fact != "split.priceGuard" for f in prop_5.lines[1].facts)
    assert format_approval_statement(prop_5, mandate=M1, today=NOW.date()) is None  # nothing to approve


def test_fresh_and_frozen_split_adds_variant_detail_line_and_stays_ask():
    """A fresh + frozen split names the trade-off ('Line 2 is frozen; line 1 is fresh') and stays ASK."""
    fresh_salmon = _named_product(
        "SYN-SAL-FRESH",
        "Synthetic Fresh Salmon Fillet 1 kg",
        sales_unit="KG",
        content=("1", "kg"),
        method="salesUnit.KG",
        claims=(),
        category="18701",
    )
    frozen_salmon = _named_product(
        "SYN-SAL-FROZEN",
        "Synthetic Salmon Fillet Frozen 1 kg",
        sales_unit="KG",
        content=("1", "kg"),
        method="salesUnit.KG",
        claims=(),
        category="41720",
    )
    products = {"SYN-SAL-FRESH": fresh_salmon, "SYN-SAL-FROZEN": frozen_salmon}

    provider = MutableSyntheticProvider()
    provider.prices["SYN-SAL-FRESH"] = price_row("SYN-SAL-FRESH", "14.00")
    provider.stocks["SYN-SAL-FRESH"] = stock_row("SYN-SAL-FRESH", 100, sales_unit="KG")
    provider.prices["SYN-SAL-FROZEN"] = price_row("SYN-SAL-FROZEN", "14.00")
    provider.stocks["SYN-SAL-FROZEN"] = stock_row("SYN-SAL-FROZEN", 150, sales_unit="KG")

    item = RequestItem(itemId="I1", what="salmon fillet", quantity=Decimal("200"), unit="kg")
    req = StructuredRequest(requestText="200 kg lohifileetä tiistaiksi", requestedDate=MONDAY, items=[item])

    gateway = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer = gateway.get_offer(
        GetOfferRequest(
            customerId=M1.customerId,
            requestedDate=MONDAY,
            items=[
                OfferItem(itemId="I1", productId="SYN-SAL-FRESH", quantity=Decimal("200"), unit="kg"),
                OfferItem(itemId="I1", productId="SYN-SAL-FROZEN", quantity=Decimal("200"), unit="kg"),
            ],
        )
    ).offer
    proposal = gateway.create_proposal(M1.customerId, offer.offerId, offer.offerVersion, req, now=NOW)

    assert proposal.decision == "ASK"
    assert ReasonCode.SPLIT in proposal.reasons
    assert ReasonCode.AGENT_SUBSTITUTE in proposal.reasons
    details = split_detail_lines(proposal, products=products)
    assert "Line 2 is frozen; line 1 is fresh" in details
    variant_fact = next((f for f in proposal.lines[1].facts if f.fact == "split.variant"), None)
    assert variant_fact is not None and variant_fact.display == "Line 2 is frozen; line 1 is fresh"


def test_penne_and_arla_pro_whipping_cream_shortlist_splits():
    """Observed evidence from judge run:
    1) 300 kg gluten-free penne with 199.5 kg penne in stock + compliant macaroni/spaghetti -> includes top-up.
    2) 200 l Arla Pro lactose-free whipping cream with 48 l Arla Pro in stock + Valio whipping cream -> includes top-up.
    """
    # 1. Penne (199.5 kg) + Macaroni (sufficient stock)
    penne = _named_product("SYN-PENNE", "Synthetic Penne 500g glutenfree pasta", content=("0.5", "kg"))
    macaroni = _named_product("SYN-MAC", "Synthetic 5kg macaroni glutenfree pasta", content=("5", "kg"))
    penne_item = RequestItem(
        itemId="I1",
        what="penne pasta",
        quantity=Decimal("300"),
        unit="kg",
        requirements=[GLUTEN_FREE],
    )
    ranked_pasta = rank_candidates(
        [penne, macaroni],
        item=penne_item,
        mandate=M1,
        requested_date=MONDAY,
        observations={
            "SYN-PENNE": _obs("SYN-PENNE", unit_price="2.60", comp_price="5.20", avail_qty=399),
            "SYN-MAC": _obs("SYN-MAC", unit_price="22.92", comp_price="4.58", avail_qty=50),
        },
    )
    pasta_offer_items = build_shortlist_offer_items(penne_item, ranked_candidates=ranked_pasta)
    assert [oi.productId for oi in pasta_offer_items] == ["SYN-PENNE", "SYN-MAC"]

    # 2. Arla Pro lactose-free whipping cream (48 l) + Valio lactose-free whipping cream + excluded non-LF Arla
    arla_pro_lf = _named_product(
        "SYN-ARLA-PRO",
        "Arla Pro Whipping cream 1 L UHT lactose free",
        sales_unit="ST",
        content=("1", "l"),
        method=CREAM_METHOD,
        claims=("FREE_FROM_LACTOSE",),
        category="10401",
    )
    valio_lf = _named_product(
        "SYN-VALIO-WHIP",
        "Valio whipping cream 1 l UHT lactose free",
        sales_unit="ST",
        content=("1", "l"),
        method=CREAM_METHOD,
        claims=("FREE_FROM_LACTOSE",),
        category="10401",
    )
    arla_non_lf = _named_product(
        "SYN-ARLA-NONLF",
        "Arla Lempi heavy whipping cream organic 2 dl",
        sales_unit="ST",
        content=("0.2", "l"),
        method=CREAM_METHOD,
        claims=(),
        category="10401",
    )
    cream_pool = [arla_pro_lf, valio_lf, arla_non_lf]
    cream_by_id = {p.productId: p for p in cream_pool}
    cream_item = RequestItem(
        itemId="I1",
        what="Arla Pro whipping cream",
        quantity=Decimal("200"),
        unit="l",
        requirements=[LACTOSE_FREE],
    )
    s_res = search_products(
        cream_item.what,
        requirements=cream_item.requirements,
        mandate=M1,
        products=cream_pool,
        item_id=cream_item.itemId,
    )
    norm_item, named_prod, _ = normalise_item_for_planning(
        cream_item, search_result=s_res, products_by_id=cream_by_id
    )
    assert named_prod is None
    assert norm_item.productId is None

    ranked_cream = rank_candidates(
        s_res.candidates,
        item=norm_item,
        mandate=M1,
        requested_date=MONDAY,
        observations={
            "SYN-ARLA-PRO": _obs("SYN-ARLA-PRO", unit_price="4.20", comp_price="4.20", unit="l", avail_qty=48),
            "SYN-VALIO-WHIP": _obs("SYN-VALIO-WHIP", unit_price="3.90", comp_price="3.90", unit="l", avail_qty=300),
        },
        named_product=named_prod,
    )
    assert [rc.productId for rc in ranked_cream] == ["SYN-ARLA-PRO", "SYN-VALIO-WHIP"]
    cream_offer_items = build_shortlist_offer_items(norm_item, ranked_candidates=ranked_cream)
    assert [oi.productId for oi in cream_offer_items] == ["SYN-ARLA-PRO", "SYN-VALIO-WHIP"]


def test_split_recheck_confirms_when_unchanged_and_supersedes_on_line_change():
    """Existing re-check rules apply to both lines of a split: unchanged -> CONFIRMED_SIMULATED;
    any change on either line -> SUPERSEDED."""
    prod_a = _named_product("SYN-A", "A", content=("5", "kg"))
    prod_b = _named_product("SYN-B", "B", content=("5", "kg"))
    products = {"SYN-A": prod_a, "SYN-B": prod_b}

    provider = MutableSyntheticProvider()
    provider.prices["SYN-A"] = price_row("SYN-A", "10.00")
    provider.stocks["SYN-A"] = stock_row("SYN-A", 14, sales_unit="PSS")
    provider.prices["SYN-B"] = price_row("SYN-B", "12.00")
    provider.stocks["SYN-B"] = stock_row("SYN-B", 30, sales_unit="PSS")

    item = RequestItem(
        itemId="I1",
        what="pasta",
        quantity=Decimal("120"),
        unit="kg",
        requirements=[GLUTEN_FREE],
    )
    offer_req = GetOfferRequest(
        customerId=M1.customerId,
        requestedDate=MONDAY,
        items=[
            OfferItem(itemId="I1", productId="SYN-A", quantity=Decimal("120"), unit="kg"),
            OfferItem(itemId="I1", productId="SYN-B", quantity=Decimal("120"), unit="kg"),
        ],
    )
    req = StructuredRequest(requestText="120 kg gluten-free pasta.", requestedDate=MONDAY, items=[item])

    # 1. Unchanged -> CONFIRMED_SIMULATED
    gw1 = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer1 = gw1.get_offer(offer_req, offer_id="OF-SPLIT-OK").offer
    prop1 = gw1.create_proposal(M1.customerId, offer1.offerId, offer1.offerVersion, req, now=NOW)
    view1 = gw1.get_approver_view(prop1.proposalId)
    confirmed = gw1.decide_approval(prop1.proposalId, prop1.offerVersion, view1.approvalToken, "APPROVE", now=NOW)
    assert confirmed.state == "CONFIRMED_SIMULATED"

    # 2. Price change on line 2 (B) before re-check -> SUPERSEDED
    gw2 = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer2 = gw2.get_offer(offer_req, offer_id="OF-SPLIT-SUP").offer
    prop2 = gw2.create_proposal(M1.customerId, offer2.offerId, offer2.offerVersion, req, now=NOW)
    view2 = gw2.get_approver_view(prop2.proposalId)

    provider.prices["SYN-B"] = price_row("SYN-B", "12.50")
    superseded = gw2.decide_approval(prop2.proposalId, prop2.offerVersion, view2.approvalToken, "APPROVE", now=NOW)
    assert superseded.state == "SUPERSEDED"
    assert superseded.supersededBy is not None
    v2 = gw2.get_proposal(superseded.supersededBy)
    assert v2.offerVersion == 2
    assert v2.decision == "ASK"
    assert v2.state == "PENDING_APPROVAL"
    assert ReasonCode.SPLIT in v2.reasons
    assert ReasonCode.AGENT_SUBSTITUTE in v2.reasons


def test_scenarios_a_b_c_and_eval_unchanged():
    """Scenario A, B, C and all active eval rows remain unchanged."""
    report = run_eval_suite(
        products=build_reference_products(),
        provider=ReferenceFixtureProvider(),
    )
    assert report.failed_rows == 0
    by_scenario = {r.scenario_id: r for r in report.results if r.scenario_id}
    assert by_scenario["A"].actual_decision == "AUTO"
    assert by_scenario["B"].actual_decision == "ASK"
    assert by_scenario["B"].actual_reasons == ("OVER_AUTO_LIMIT",)
    assert by_scenario["C"].actual_decision == "BLOCK"
    assert by_scenario["B2"].actual_decision == "ASK"
