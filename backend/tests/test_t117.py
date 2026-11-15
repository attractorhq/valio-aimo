"""T117 (judge run 20261115-064402): no item is dropped, stated attributes are requirements, no phantom "named
product", no unrequested lactose assumption. Synthetic hand-made records only (patch P7)."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from test_t115_substitute_text_and_form_rank import MONDAY, _obs, _product

from app.agent.parse import parse_request
from app.agent.plan import _find_named_product_in_results, rank_candidates
from app.catalog.categories import proposal_type_match
from app.catalog.search import SearchProductsResult
from app.contracts import ExcludedProduct, Preference, ReasonCode, RequestItem
from app.policy import M1

NOW = datetime(2026, 11, 15, 7, 0, tzinfo=ZoneInfo("Europe/Helsinki"))


def _items(text: str) -> list[tuple[str, str, str]]:
    return [(i.what, str(i.quantity), i.unit) for i in parse_request(text, NOW).request.items]


def test_a_counted_item_after_and_is_its_own_item():
    assert _items("12 kg sliced gouda cheese and 300 eggs for tomorrow") == [
        ("sliced Gouda cheese", "12", "kg"), ("eggs", "300", "pcs"),
    ]
    assert _items("12 kg juustoa ja 300 kananmunaa huomiseksi") == [("cheese", "12", "kg"), ("eggs", "300", "pcs")]
    assert _items("300 eggs for tomorrow") == [("eggs", "300", "pcs")]


def test_barista_is_a_stated_attribute():
    plain = proposal_type_match("oat barista drink", ("Synthetic oat drink 1 l UHT",), category="10314",
                                requirement_codes=())
    barista = proposal_type_match("oat barista drink", ("Synthetic Barista oat drink 1 l",), category="10314",
                                  requirement_codes=())
    assert (plain.exact, plain.differences) == (False, ("not Barista",))
    assert barista.exact
    # Not asked: a barista drink is still an oat drink.
    assert proposal_type_match("oat drink", ("Synthetic Barista oat drink 1 l",), category="10314",
                               requirement_codes=()).exact


GRATED = _product("SYN-GRATED", "10603", "Synthetic Edam grated cheese 2500 g", "Synteettinen edam raaste 2500 g",
                  grams=2500)
BLOCK = _product("SYN-BLOCK", "10600", "Synthetic Edam cheese block 1000 g", "Synteettinen edam 1000 g")
BREAD = _product("SYN-BREAD", "10690", "Synthetic lactose-free bread cheese 500 g",
                 "Synteettinen laktoositon leipäjuusto 500 g", grams=500)
CREAM = _product("SYN-CREAM", "10620", "Synthetic Creamy Original 500 g", "Synteettinen tuorejuusto 500 g",
                 grams=500)


def test_a_cheese_made_for_another_use_is_never_offered_for_grated():
    item = RequestItem(itemId="I1", what="grated cheese", quantity=Decimal(12), unit="kg",
                       preferences=[Preference(kind="OTHER", value="Grated")])
    ranked = rank_candidates([BREAD, CREAM, BLOCK], item=item, mandate=M1, requested_date=MONDAY,
                             observations={p.productId: _obs(p.productId, "6.00") for p in (BREAD, CREAM, BLOCK)})
    # A block can be grated in the kitchen: a named substitute. Bread cheese and cream cheese are never offered.
    assert [c.productId for c in ranked] == ["SYN-BLOCK"]
    assert ranked[0].substitutionSource == "AGENT" and ReasonCode.AGENT_SUBSTITUTE in ranked[0].reasons
    with_grated = rank_candidates([BREAD, GRATED], item=item, mandate=M1, requested_date=MONDAY,
                                  observations={p.productId: _obs(p.productId, "9.00") for p in (BREAD, GRATED)})
    assert [c.productId for c in with_grated] == ["SYN-GRATED"]


def test_a_form_word_never_makes_a_phantom_named_product():
    sliced = _product("SYN-EDAM-SL", "10600", "Synthetic Edam sliced 1000 g", "Synteettinen edam viipaloitu 1 kg")
    item = RequestItem(itemId="I1", what="sliced Edam", quantity=Decimal(30), unit="kg")
    result = SearchProductsResult(
        query="sliced Edam",
        candidates=[],
        excluded=[ExcludedProduct(productId=sliced.productId, name=sliced.name, itemId="I1",
                                  reasons=[ReasonCode.CUTOFF_PASSED])],
    )
    assert _find_named_product_in_results(item, result, {sliced.productId: sliced}) is None
