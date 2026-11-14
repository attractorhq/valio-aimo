"""T89: generic product types accept their subtypes without substitute warnings."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.agent.plan import CandidateObservation, rank_candidates
from app.catalog.categories import matches_product_concept
from app.catalog.normalise import normalise_product
from app.contracts import Quantity, ReasonCode, RequestItem
from app.policy import M1
from app.policy.lines import ContentBasis, LineInput, StockKind, evaluate_line
from tests.synthetic.catalog_records import synthetic_product_record

MONDAY = date(2026, 11, 16)
NOW = datetime.fromisoformat("2026-11-14T10:00:00+02:00")


def _product(name, category):
    return normalise_product(synthetic_product_record(
        product_id="SYN-TYPE",
        names={"en": name},
        category=category,
        sales_unit="KG",
        base_unit="KG",
        allergens=[("AM", "CONTAINS")],
    ))


@pytest.mark.parametrize(("query", "name", "category"), [
    ("pasta", "Synthetic macaroni", "21380"),
    ("pasta", "Synthetic penne", "21380"),
    ("pasta", "Synthetic fettuccine", "21380"),
    ("cheese", "Synthetic Gouda", "10615"),
    ("cream", "Synthetic Vispi 35 %", "10401"),
    ("whipping cream 35 %", "Synthetic whipping cream 35 %", "10401"),
    ("whipping cream 35 %", "Synthetic Vispi 35 %", "10401"),
    ("milk", "Synthetic semi skimmed milk", "10400"),
    ("juice", "Synthetic apple juice", "22700"),
    ("flour", "Synthetic spelt flour", "21310"),
])
def test_generic_type_and_matching_qualifiers_are_not_substitutes(query, name, category):
    product = _product(name, category)
    item = RequestItem(itemId="I1", what=query, quantity=Decimal(1), unit="kg")
    ranked = rank_candidates([product], item=item, mandate=M1, requested_date=MONDAY)
    assert len(ranked) == 1
    candidate = ranked[0]
    assert candidate.exactType
    assert candidate.substitutionSource is None
    assert ReasonCode.AGENT_SUBSTITUTE not in candidate.reasons
    assert "Agent substitute" not in candidate.tradeoff

    line = LineInput(
        line_id="L1", item_id="I1", product=product,
        quantity_sales_units=Decimal(1), delivered_quantity=Quantity(value="1", unit="kg"),
        line_total_excl_vat=Decimal(1), price_valid=True, sales_blocked=False,
        stock=StockKind.PLENTY, stock_amount=None, available_date=MONDAY, closing_time=None,
        content_basis=ContentBasis.DECLARED,
        substitution=candidate.substitutionSource, product_type_differs=not candidate.exactType,
    )
    verdict = evaluate_line(line, item=item, mandate=M1, requested_date=MONDAY, now=NOW)
    assert ReasonCode.AGENT_SUBSTITUTE not in verdict.reasons


@pytest.mark.parametrize(("query", "name", "category"), [
    ("penne", "Synthetic macaroni", "21380"),
    ("whipping cream", "Synthetic cooking cream 15 %", "10401"),
    ("cheddar", "Synthetic Gouda", "10615"),
    ("milk", "Synthetic oat milk alternative", "10402"),
])
def test_specific_types_are_not_satisfied_by_their_siblings(query, name, category):
    product = _product(name, category)
    item = RequestItem(itemId="I1", what=query, quantity=Decimal(1), unit="kg")
    ranked = rank_candidates([product], item=item, mandate=M1, requested_date=MONDAY)
    # An unrelated family can be excluded altogether; a proposed alternative must be flagged.
    assert all(not c.exactType and ReasonCode.AGENT_SUBSTITUTE in c.reasons for c in ranked)
    if query in {"penne", "whipping cream", "cheddar"}:
        assert len(ranked) == 1


def test_named_product_swap_is_still_a_substitute_even_for_the_same_type():
    product = _product("Synthetic whipping cream 35 %", "10401")
    item = RequestItem(
        itemId="I1", what="whipping cream 35 %", quantity=Decimal(1), unit="kg",
        productId="SYN-NAMED",
    )
    candidate, = rank_candidates([product], item=item, mandate=M1, requested_date=MONDAY)
    assert candidate.exactType
    assert candidate.substitutionSource == "AGENT"
    assert ReasonCode.AGENT_SUBSTITUTE in candidate.reasons


def test_generic_subtype_keeps_normal_price_ranking():
    macaroni = _product("Synthetic macaroni", "21380")
    penne = _product("Synthetic penne", "21380").model_copy(update={"productId": "SYN-PENNE"})
    item = RequestItem(itemId="I1", what="pasta", quantity=Decimal(1), unit="kg")
    ranked = rank_candidates(
        [penne, macaroni], item=item, mandate=M1, requested_date=MONDAY,
        observations={p.productId: CandidateObservation(
            product_id=p.productId, unit_price_excl_vat=price,
            comparison_price=price, comparison_unit="kg", delivery_date=MONDAY,
        ) for p, price in [(macaroni, Decimal(1)), (penne, Decimal(2))]},
    )
    assert [c.productId for c in ranked] == [macaroni.productId, penne.productId]
    assert all(c.exactType and c.substitutionSource is None for c in ranked)


@pytest.mark.parametrize(("concept", "name", "category"), [
    ("penne", "Synthetic macaroni", "21380"),
    ("juice", "Synthetic mineral water", "22700"),
    ("cream", "Synthetic sour cream", "10401"),
    ("cream", "Synthetic cream cheese", "10401"),
    ("pasta", "Synthetic rice", "21340"),
    ("cheese", "Synthetic Gouda", "unknown"),
])
def test_category_evidence_never_uses_siblings_broad_groups_or_excluded_names(concept, name, category):
    assert not matches_product_concept(concept, name.lower(), category=category)
