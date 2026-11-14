"""T52: stated attributes (salted, organic, fat %, fresh, smoked) are hard: a product that does not prove one is
only an agent substitute with the difference named, and never the recommended exact match."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.agent.parse import parse_request
from app.agent.plan import rank_candidates
from app.catalog.categories import product_type_match
from app.catalog.normalise import normalise_product
from app.contracts import Preference, ReasonCode, RequestItem
from app.policy import M1
from tests.synthetic.catalog_records import synthetic_product_record
from tests.test_agent_plan import MONDAY, _make_milk, _obs

SAT = datetime(2026, 11, 14, 11, 0, tzinfo=ZoneInfo("Europe/Helsinki"))


def _product(product_id: str, name: str, *, category: str = "105", grams: int = 500, labels=None):
    return normalise_product(synthetic_product_record(
        product_id=product_id, category=category, names={"en": name}, allergens=[("AM", "CONTAINS")],
        claims=[], labels=labels or [],
        unit_conversions=[{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "netWeight": {"unit": "GRM", "value": grams}}],
    ))


def _rank(products, item, prices):
    return rank_candidates(
        products, item=item, mandate=M1, requested_date=MONDAY,
        observations={pid: _obs(pid, price) for pid, price in prices.items()},
    )


def _item(what: str, unit: str = "kg", quantity: str = "50", preferences=()):
    return RequestItem(itemId="I1", what=what, quantity=Decimal(quantity), unit=unit, preferences=list(preferences))


# --------------------------------------------------------------------------- salted vs unsalted


@pytest.mark.parametrize(("query", "name", "differences"), [
    ("salted butter", "Synthetic butter 500 g unsalted", ("unsalted, not salted",)),
    ("salted butter", "Synthetic butter 500 g normal salted", ()),
    ("salted butter", "Synthetic voi 500 g normaalisuolainen", ()),
    ("salted butter", "Synthetic butter 500 g", ("not salted",)),
    ("unsalted butter", "Synthetic butter 500 g unsalted", ()),
    ("unsalted butter", "Synthetic voi 500 g suolaton", ()),
    ("unsalted butter", "Synthetic butter 500 g salted", ("salted, not unsalted",)),
    ("butter", "Synthetic butter 500 g unsalted", ()),  # not stated: no constraint
])
def test_salted_and_unsalted_are_a_type(query, name, differences):
    assert product_type_match(query, (name,), concept_id="butter").differences == differences


def test_salted_butter_recommends_the_salted_product_over_a_cheaper_unsalted_one():
    unsalted = _product("SYN-UNSALTED", "Synthetic butter 500 g unsalted")
    salted = _product("SYN-SALTED", "Synthetic butter 500 g salted")
    ranked = _rank([unsalted, salted], _item("salted butter"), {"SYN-UNSALTED": "3.10", "SYN-SALTED": "3.60"})
    assert [r.productId for r in ranked] == ["SYN-SALTED", "SYN-UNSALTED"]
    top, sub = ranked
    assert (top.tier, top.exactType, top.substitutionSource) == ("RECOMMENDED", True, None)
    assert sub.exactType is False and sub.substitutionSource == "AGENT"
    assert ReasonCode.AGENT_SUBSTITUTE in sub.reasons
    assert "unsalted, not salted" in sub.tradeoff


def test_only_unsalted_on_offer_is_an_agent_substitute_that_says_so():
    unsalted = _product("SYN-UNSALTED", "Synthetic butter 500 g unsalted")
    (only,) = _rank([unsalted], _item("salted butter"), {"SYN-UNSALTED": "3.10"})
    assert only.exactType is False and only.substitutionSource == "AGENT"
    assert ReasonCode.AGENT_SUBSTITUTE in only.reasons
    assert "unsalted, not salted" in only.tradeoff


# --------------------------------------------------------------------------- organic


def test_organic_is_proven_by_a_label_and_a_plain_product_is_a_named_substitute():
    plain = _make_milk("SYN-PLAIN", name="Synthetic milk drink 1 l lactose free")
    organic = _make_milk("SYN-ORG", name="Synthetic milk drink 1 l lactose free", labels=["EU_ORGANIC_FARMING"])
    item = _item("milk", unit="l", quantity="100", preferences=[Preference(kind="ORGANIC", value="organic")])
    ranked = _rank([plain, organic], item, {"SYN-PLAIN": "1.00", "SYN-ORG": "1.40"})
    assert [r.productId for r in ranked] == ["SYN-ORG", "SYN-PLAIN"]
    assert ranked[0].exactType is True
    assert ranked[1].substitutionSource == "AGENT"
    assert "not organic (no organic label)" in ranked[1].tradeoff


def test_organic_in_the_name_counts_as_proof():
    organic = _make_milk("SYN-LUOMU", name="Synthetic luomu milk drink 1 l lactose free")
    item = _item("milk", unit="l", quantity="100", preferences=[Preference(kind="ORGANIC", value="organic")])
    (only,) = _rank([organic], item, {"SYN-LUOMU": "1.40"})
    assert only.exactType is True and only.substitutionSource is None


# --------------------------------------------------------------------------- fat %


def test_unstated_fat_percent_is_a_named_substitute_after_proven_matches():
    proven = _make_milk("SYN-35", name="Synthetic milk drink 3.5 % 1 l lactose free")
    unknown = _make_milk("SYN-UNK", name="Synthetic milk drink 1 l lactose free")
    item = _item("milk", unit="l", quantity="100", preferences=[Preference(kind="FAT_PERCENT", value="3.5")])
    ranked = _rank([unknown, proven], item, {"SYN-35": "1.30", "SYN-UNK": "1.00"})
    assert [r.productId for r in ranked] == ["SYN-35", "SYN-UNK"]
    assert ranked[1].substitutionSource == "AGENT"
    assert "fat % not stated, 3.5 % asked" in ranked[1].tradeoff


# --------------------------------------------------------------------------- fresh vs frozen (T39), kept


def test_fresh_salmon_is_never_recommended_frozen():
    frozen = _product("SYN-FROZEN", "Synthetic frozen salmon fillet 1 kg", category="30", grams=1000)
    fresh = _product("SYN-FRESH", "Synthetic fresh salmon fillet 1 kg", category="30", grams=1000)
    ranked = _rank([frozen, fresh], _item("fresh salmon fillet", quantity="15"),
                   {"SYN-FROZEN": "9.00", "SYN-FRESH": "14.00"})
    assert ranked[0].productId == "SYN-FRESH" and ranked[0].exactType is True
    assert ranked[1].substitutionSource == "AGENT"
    assert "frozen, not fresh" in ranked[1].tradeoff


# --------------------------------------------------------------------------- the parser keeps them


@pytest.mark.parametrize(("text", "what"), [
    ("50 kg of salted butter delivered tomorrow", "salted butter"),
    ("50 kg suolattua voita huomiseksi", "salted butter"),
    ("50 kg suolatonta voita huomiseksi", "unsalted butter"),
    ("15 kg tuoretta lohifileetä huomiseksi", "fresh salmon fillet"),
    ("10 kg kylmäsavustettua lohta huomiseksi", "cold-smoked salmon"),
    ("10 kg pakastettua lohta huomiseksi", "frozen salmon"),
])
def test_stated_attributes_stay_in_the_item(text, what):
    (item,) = parse_request(text, SAT).request.items
    assert item.what == what


def test_organic_and_fat_percent_are_stated_as_required():
    request = parse_request("100 l organic milk 3.5 % tomorrow", SAT).request
    assert "Organic required: proven only by an organic label, otherwise offered as a substitute" in (
        request.assumptions
    )
    assert "3.5 % fat required: otherwise offered as a substitute" in request.assumptions
