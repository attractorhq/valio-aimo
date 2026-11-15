"""T85: when no single compliant product covers a partial line's shortfall, top up from at most two.

All committed tests use synthetic hand-made records (patch P7).
"""

from decimal import Decimal

from test_proposals_gateway import GLUTEN_FREE, MONDAY, NOW, MutableSyntheticProvider, price_row, stock_row
from test_split import _named_product, _obs

from app.agent.plan import build_shortlist_offer_items, rank_candidates
from app.contracts import GetOfferRequest, OfferItem, ReasonCode, RequestItem, StructuredRequest
from app.gateway.proposals import ProposalGateway, format_approval_statement
from app.policy import DEMO_MANDATES, M1, MandateBook

PENNE = RequestItem(itemId="I1", what="penne pasta", quantity=Decimal("300"), unit="kg", requirements=[GLUTEN_FREE])
REQUEST = StructuredRequest(requestText="300 kg gluten-free penne pasta by Monday.", requestedDate=MONDAY,
                            items=[PENNE])


def _gateway(stock: dict[str, tuple[str, int]], products):
    provider = MutableSyntheticProvider()
    for code, (price, bags) in stock.items():
        provider.prices[code] = price_row(code, price)
        provider.stocks[code] = stock_row(code, bags, sales_unit="PSS")
    return ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider), provider


def _propose(gateway, codes, offer_id="OF-T85"):
    offer = gateway.get_offer(GetOfferRequest(
        customerId=M1.customerId, requestedDate=MONDAY,
        items=[OfferItem(itemId="I1", productId=code, quantity=Decimal("300"), unit="kg") for code in codes],
    ), offer_id=offer_id).offer
    return gateway.create_proposal(M1.customerId, offer.offerId, offer.offerVersion, REQUEST, now=NOW)


def test_shortlist_takes_two_partial_top_ups_in_rank_order_when_none_covers_the_shortfall():
    penne = _named_product("SYN-PENNE", "Synthetic Penne 500g glutenfree pasta", content=("0.5", "kg"))
    macaroni = _named_product("SYN-MAC", "Synthetic 5kg macaroni glutenfree pasta", content=("5", "kg"))
    fusilli = _named_product("SYN-FUS", "Synthetic 5kg fusilli glutenfree pasta", content=("5", "kg"))
    spaghetti = _named_product("SYN-SPA", "Synthetic 5kg spaghetti glutenfree pasta", content=("5", "kg"))
    observations = {
        "SYN-PENNE": _obs("SYN-PENNE", unit_price="2.60", comp_price="5.20", avail_qty=399),  # 199.5 kg
        "SYN-MAC": _obs("SYN-MAC", unit_price="22.00", comp_price="4.40", avail_qty=19),  # 95 kg
        "SYN-FUS": _obs("SYN-FUS", unit_price="23.00", comp_price="4.60", avail_qty=19),  # 95 kg
        "SYN-SPA": _obs("SYN-SPA", unit_price="24.00", comp_price="4.80", avail_qty=19),  # 95 kg
    }
    ranked = rank_candidates([penne, macaroni, fusilli, spaghetti], item=PENNE, mandate=M1,
                             requested_date=MONDAY, observations=observations)
    assert ranked[0].productId == "SYN-PENNE" and ranked[0].exactType
    top_ups = [rc.productId for rc in ranked[1:]]

    ids = [oi.productId for oi in build_shortlist_offer_items(PENNE, ranked_candidates=ranked)]
    assert ids == ["SYN-PENNE", *top_ups[:2]]

    # When one candidate covers the whole shortfall, T69 still takes only that one.
    observations["SYN-SPA"] = _obs("SYN-SPA", unit_price="24.00", comp_price="4.80", avail_qty=50)
    ranked = rank_candidates([penne, macaroni, fusilli, spaghetti], item=PENNE, mandate=M1,
                             requested_date=MONDAY, observations=observations)
    assert [oi.productId for oi in build_shortlist_offer_items(PENNE, ranked_candidates=ranked)] == [
        "SYN-PENNE", "SYN-SPA"]


def test_partial_a_with_b_and_c_each_covering_part_gives_a_three_line_split_with_the_rest_named():
    products = {
        "SYN-A": _named_product("SYN-A", "A", content=("0.5", "kg")),
        "SYN-B": _named_product("SYN-B", "B", content=("5", "kg")),
        "SYN-C": _named_product("SYN-C", "C", content=("5", "kg")),
    }
    # A 399 × 0.5 kg = 199.5 kg; B 19 × 5 kg = 95 kg; C 1 × 5 kg = 5 kg → 299.5 of 300 kg.
    gateway, _ = _gateway({"SYN-A": ("2.50", 399), "SYN-B": ("25.00", 19), "SYN-C": ("26.00", 1)}, products)
    proposal = _propose(gateway, ["SYN-A", "SYN-B", "SYN-C"])

    assert proposal.decision == "ASK" and proposal.state == "PENDING_APPROVAL"
    assert [(ln.productId, ln.deliveredQuantity.value) for ln in proposal.lines] == [
        ("SYN-A", Decimal("199.5")), ("SYN-B", Decimal("95")), ("SYN-C", Decimal("5"))]
    for reason in (ReasonCode.SPLIT, ReasonCode.AGENT_SUBSTITUTE, ReasonCode.PARTIAL_AVAILABILITY):
        assert reason in proposal.reasons
    for top_up in proposal.lines[1:]:
        assert top_up.substitutionSource == "AGENT" and top_up.substituteFor == "SYN-A"
        assert ReasonCode.SPLIT in top_up.reasons and ReasonCode.AGENT_SUBSTITUTE in top_up.reasons
    assert any(f.fact == "coverage.partial" for f in proposal.lines[0].facts)

    statement = format_approval_statement(proposal, mandate=M1, today=NOW.date())
    assert statement == (
        "Approving means 199.5 kg of A + 95 kg of B + 5 kg of C (€0.20/kg more), "
        "299.5 kg of 300 kg; 0.5 kg cannot be met by Mon 16 Nov."
    )


def test_a_top_up_failing_a_claim_is_skipped():
    penne = _named_product("SYN-PENNE", "Synthetic Penne 500g glutenfree pasta", content=("0.5", "kg"))
    no_claim = _named_product("SYN-NOCLAIM", "Synthetic 5kg macaroni pasta", content=("5", "kg"), claims=())
    fusilli = _named_product("SYN-FUS", "Synthetic 5kg fusilli glutenfree pasta", content=("5", "kg"))
    products = {p.productId: p for p in (penne, no_claim, fusilli)}
    observations = {
        "SYN-PENNE": _obs("SYN-PENNE", unit_price="2.60", comp_price="5.20", avail_qty=399),
        "SYN-NOCLAIM": _obs("SYN-NOCLAIM", unit_price="20.00", comp_price="4.00", avail_qty=50),
        "SYN-FUS": _obs("SYN-FUS", unit_price="23.00", comp_price="4.60", avail_qty=19),
    }
    ranked = rank_candidates(list(products.values()), item=PENNE, mandate=M1, requested_date=MONDAY,
                             observations=observations)
    ids = [oi.productId for oi in build_shortlist_offer_items(PENNE, ranked_candidates=ranked)]
    assert ids == ["SYN-PENNE", "SYN-FUS"]  # the cheaper macaroni would cover it all, but it is not gluten-free

    gateway, _ = _gateway({"SYN-PENNE": ("2.60", 399), "SYN-NOCLAIM": ("20.00", 50), "SYN-FUS": ("23.00", 19)},
                          products)
    proposal = _propose(gateway, ids)
    assert [ln.productId for ln in proposal.lines] == ["SYN-PENNE", "SYN-FUS"]
    assert {ReasonCode.SPLIT, ReasonCode.AGENT_SUBSTITUTE, ReasonCode.PARTIAL_AVAILABILITY} <= set(proposal.reasons)
    assert "294.5 kg of 300 kg; 5.5 kg cannot be met by Mon 16 Nov" in format_approval_statement(
        proposal, mandate=M1, today=NOW.date())


def test_partial_split_recheck_confirms_when_unchanged():
    products = {
        "SYN-A": _named_product("SYN-A", "A", content=("0.5", "kg")),
        "SYN-B": _named_product("SYN-B", "B", content=("5", "kg")),
        "SYN-C": _named_product("SYN-C", "C", content=("5", "kg")),
    }
    gateway, _ = _gateway({"SYN-A": ("2.50", 399), "SYN-B": ("25.00", 19), "SYN-C": ("26.00", 1)}, products)
    proposal = _propose(gateway, ["SYN-A", "SYN-B", "SYN-C"], offer_id="OF-T85-RECHECK")
    view = gateway.get_approver_view(proposal.proposalId)
    confirmed = gateway.decide_approval(proposal.proposalId, proposal.offerVersion, view.approvalToken, "APPROVE",
                                        now=NOW)
    assert confirmed.state == "CONFIRMED_SIMULATED"
