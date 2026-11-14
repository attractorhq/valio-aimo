"""T44: partial stock is filled from compliant same-type alternatives; a hard-limit BLOCK shows what is feasible.

Synthetic hand-made records only (patch P7).
"""

from decimal import Decimal

from test_proposals_gateway import (
    MONDAY,
    NOW,
    MutableSyntheticProvider,
    price_row,
    stock_row,
    syn_product,
)

from app.agent.plan import (
    CandidateObservation,
    build_shortlist_offer_items,
    feasible_within_hard_limit,
    rank_candidates,
)
from app.contracts import GetOfferRequest, ReasonCode, RequestItem, Requirement, StructuredRequest
from app.gateway.proposals import ProposalGateway
from app.policy import DEMO_MANDATES, M1, M2, MandateBook

GLUTEN_FREE = Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")
PASTA_METHOD = "unitConversions.BASE_UNIT_OR_EACH.netWeight x units.PSS.sizeInBaseUnits"


def _penne(code: str):
    return syn_product(code, sales_unit="PSS", content=("5", "kg"), method=PASTA_METHOD, claims=("FREE_FROM_GLUTEN",))


PENNE_A, PENNE_B, FUSILLI = _penne("SYN-PENNE-A"), _penne("SYN-PENNE-B"), _penne("SYN-FUSILLI")
PRODUCTS = {p.productId: p for p in (PENNE_A, PENNE_B, FUSILLI)}


def _provider(stock: dict[str, int]) -> MutableSyntheticProvider:
    provider = MutableSyntheticProvider()
    for code, amount in stock.items():
        provider.prices[code] = price_row(code, "10.00")
        provider.stocks[code] = stock_row(code, amount, sales_unit="PSS")
    return provider


def _obs(code: str, amount: int) -> CandidateObservation:
    return CandidateObservation(
        product_id=code, unit_price_excl_vat=Decimal("10.00"), comparison_price=Decimal("2.00"),
        comparison_unit="kg", vat_rate=Decimal("14"), stock_tier="EXACT", available_quantity=Decimal(amount),
        delivery_date=MONDAY, closing_time=None, reasons=(), facts=(),
    )


def _decide(item: RequestItem, stock: dict[str, int], products):
    ranked = rank_candidates(
        [PRODUCTS[c] for c in products], item=item, mandate=M2, requested_date=MONDAY,
        observations={c: _obs(c, stock[c]) for c in products},
    )
    offer_items = build_shortlist_offer_items(item, ranked_candidates=ranked)
    gateway = ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES), provider=_provider(stock))
    offer = gateway.get_offer(GetOfferRequest(customerId=M2.customerId, requestedDate=MONDAY, items=offer_items)).offer
    request = StructuredRequest(requestText="300 kg gluten-free penne by Monday.", requestedDate=MONDAY, items=[item])
    return gateway.create_proposal(M2.customerId, offer.offerId, offer.offerVersion, request, now=NOW)


def test_two_compliant_products_each_covering_half_make_one_full_proposal():
    item = RequestItem(itemId="I1", what="penne", quantity=Decimal(300), unit="kg", requirements=[GLUTEN_FREE])
    proposal = _decide(item, {"SYN-PENNE-A": 30, "SYN-PENNE-B": 30}, ["SYN-PENNE-A", "SYN-PENNE-B"])
    assert [(ln.productId, ln.quantitySalesUnits) for ln in proposal.lines] == [
        ("SYN-PENNE-A", Decimal(30)), ("SYN-PENNE-B", Decimal(30)),
    ]
    assert sum(ln.deliveredQuantity.value for ln in proposal.lines) == Decimal(300)
    assert ReasonCode.PARTIAL_AVAILABILITY not in proposal.reasons
    assert all(ReasonCode.PARTIAL_AVAILABILITY not in ln.reasons for ln in proposal.lines)
    assert ReasonCode.SPLIT in proposal.reasons  # INFO: covered by two products that meet every requirement


def test_partial_stays_when_the_whole_shortlist_cannot_cover_the_need():
    item = RequestItem(itemId="I1", what="penne", quantity=Decimal(300), unit="kg", requirements=[GLUTEN_FREE])
    proposal = _decide(item, {"SYN-PENNE-A": 30, "SYN-PENNE-B": 10}, ["SYN-PENNE-A", "SYN-PENNE-B"])
    assert sum(ln.deliveredQuantity.value for ln in proposal.lines) == Decimal(200)
    assert proposal.decision == "ASK" and ReasonCode.PARTIAL_AVAILABILITY in proposal.reasons


def test_a_different_type_never_fills_the_remainder_silently():
    # 'fusilli' is not 'penne': it is not added to the shortlist while a penne exists (T39), so the
    # remainder stays partial instead of being silently swapped.
    item = RequestItem(itemId="I1", what="penne", quantity=Decimal(300), unit="kg", requirements=[GLUTEN_FREE])
    proposal = _decide(item, {"SYN-PENNE-A": 30, "SYN-FUSILLI": 100}, ["SYN-PENNE-A", "SYN-FUSILLI"])
    assert [ln.productId for ln in proposal.lines] == ["SYN-PENNE-A"]
    assert ReasonCode.PARTIAL_AVAILABILITY in proposal.reasons


# ---- feasible order within the hard limit ------------------------------------------------------------


def _blocked(quantity: int, stock: int = 100000, mandate=M1):
    item = RequestItem(itemId="I1", what="penne", quantity=Decimal(quantity), unit="kg", requirements=[GLUTEN_FREE])
    gateway = ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES),
                              provider=_provider({"SYN-PENNE-A": stock}))
    offer = gateway.get_offer(GetOfferRequest(
        customerId=mandate.customerId, requestedDate=MONDAY,
        items=[{"itemId": "I1", "productId": "SYN-PENNE-A", "quantity": Decimal(quantity), "unit": "kg"}],
    )).offer
    request = StructuredRequest(requestText="penne", requestedDate=MONDAY, items=[item])
    return gateway.create_proposal(mandate.customerId, offer.offerId, offer.offerVersion, request, now=NOW)


def test_hard_limit_block_suggests_the_largest_feasible_order():
    proposal = _blocked(10000)  # 2,000 bags × €10.00 = €20,000.00 > M1 €5,000 hard limit
    assert proposal.decision == "BLOCK" and ReasonCode.OVER_HARD_LIMIT in proposal.reasons
    feasible = feasible_within_hard_limit(proposal, M1)
    assert feasible is not None
    assert feasible.totalExclVat == Decimal("5000.00") and feasible.totalExclVat <= M1.hardLimitEur
    (line,) = feasible.lines
    assert (line.quantitySalesUnits, line.deliveredQuantity.value) == (Decimal(500), Decimal(2500))
    assert feasible.text == (
        "Feasible within your €5,000.00 hard limit: 2,500 kg for €5,000.00 excl. VAT. "
        "Information only; nothing has been proposed or ordered."
    )


def test_feasible_order_respects_lot_sizes():
    proposal = _blocked(10000)
    feasible = feasible_within_hard_limit(proposal, M1, {"SYN-PENNE-A": Decimal(6)})
    assert feasible is not None
    assert feasible.lines[0].quantitySalesUnits == Decimal(498)  # 6-bag lots: 83 × 6
    assert feasible.totalExclVat == Decimal("4980.00")


def test_no_suggestion_unless_the_hard_limit_blocks():
    assert feasible_within_hard_limit(_blocked(100), M1) is None  # AUTO, inside the limits
    assert feasible_within_hard_limit(_blocked(1000), M1) is None  # €2,000: ASK over the auto limit only


def test_countable_sales_units_are_suggested_whole_even_with_a_fractional_lot():
    proposal = _blocked(10000)
    feasible = feasible_within_hard_limit(proposal, M1, {"SYN-PENNE-A": Decimal("0.01")})
    assert feasible is not None and feasible.lines[0].quantitySalesUnits == Decimal(500)
