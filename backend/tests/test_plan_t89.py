"""T89: a generic requested type (pasta, cheese, cream, milk, juice, flour …) accepts any of its subtypes as an
exact match; a specific type (penne) still flags another subtype (macaroni) as an agent substitute.

All committed tests use synthetic hand-made records (patch P7).
"""

from decimal import Decimal

import pytest

from app.agent.plan import CandidateObservation, rank_candidates, requested_type_match
from app.catalog.categories import same_product_family
from app.catalog.normalise import normalise_product
from app.catalog.search import search_products
from app.contracts import Preference, RequestItem
from app.policy import M1
from tests.synthetic.catalog_records import synthetic_product_record
from tests.test_proposals_gateway import MONDAY


def _product(pid: str, category: str, en: str, fi: str, *, kg: str = "1"):
    return normalise_product(
        synthetic_product_record(
            product_id=pid,
            category=category,
            names={"en": en, "fi": fi},
            unit_conversions=[{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "weight": {"unit": "KGM", "value": kg}}],
        )
    )


def _item(what: str, *, unit: str = "kg", preferences: list[Preference] | None = None) -> RequestItem:
    return RequestItem(itemId="I1", what=what, quantity=Decimal("10"), unit=unit, preferences=preferences or [])


MACARONI = _product("SYN-MAC", "21380", "Synthetic macaroni glutenfree pasta 5 kg", "Synteettinen makaroni 5 kg", kg="5")
EDAM_SLICES = _product("SYN-EDAM", "10602", "Synthetic Edam 17 % e1 kg slices", "Synteettinen Edam 17 % viipale")
BRAND_SLICES = _product("SYN-OLT", "10602", "Synthetic Oltermanni e900 g slices", "Synteettinen Oltermanni viipale")


@pytest.mark.parametrize(
    ("what", "product"),
    [
        ("pasta", MACARONI),
        ("pasta", _product("SYN-FUS", "21380", "Synthetic fusilli 3 kg", "Synteettinen fusilli 3 kg")),
        ("pasta", _product("SYN-SPA", "21380", "Synthetic spaghetti 5 kg", "Synteettinen spagetti 5 kg")),
        ("cheese", EDAM_SLICES),
        ("cheese", BRAND_SLICES),
        ("cheese", _product("SYN-CC", "10635", "Synthetic natural cream cheese 1,5 kg", "Synteettinen tuorejuusto")),
        ("cream", _product("SYN-WC", "10401", "Synthetic whipping cream 1 l", "Synteettinen vispikerma 1 l")),
        ("milk", _product("SYN-MD", "10400", "Synthetic semi skimmed milk drink 1 l", "Synteettinen kevytmaitojuoma")),
        ("juice", _product("SYN-OJ", "22700", "Synthetic orange juice 1 l", "Synteettinen appelsiinimehu 1 l")),
        ("flour", _product("SYN-WF", "21300", "Synthetic wheat flour 2 kg", "Synteettinen vehnäjauho 2 kg")),
        ("rice", _product("SYN-BR", "21340", "Synthetic basmati rice 5 kg", "Synteettinen basmatiriisi 5 kg")),
    ],
)
def test_a_generic_type_accepts_its_subtypes_as_exact(what, product):
    match = requested_type_match(product, _item(what))
    assert match.exact, match.differences


def test_pasta_to_macaroni_is_exact_and_never_an_agent_substitute():
    ranked = rank_candidates(
        [MACARONI], item=_item("pasta"), mandate=M1, requested_date=MONDAY,
        observations={"SYN-MAC": _obs("SYN-MAC")},
    )
    assert [r.productId for r in ranked] == ["SYN-MAC"]
    assert ranked[0].exactType is True
    assert ranked[0].substitutionSource is None


def test_a_specific_type_still_flags_another_subtype():
    match = requested_type_match(MACARONI, _item("penne"))
    assert not match.exact
    assert match.differences == ("macaroni, not penne",)
    ranked = rank_candidates(
        [MACARONI], item=_item("penne"), mandate=M1, requested_date=MONDAY,
        observations={"SYN-MAC": _obs("SYN-MAC")},
    )
    assert ranked[0].substitutionSource == "AGENT"


def test_the_family_guard_admits_cheese_slices_by_category():
    for slices in (EDAM_SLICES, BRAND_SLICES):
        names = (slices.name, *slices.names.values())
        assert same_product_family("cheese", names, concept_id="cheese", category=slices.category)


def test_a_cheese_variety_is_still_specific():
    # "edam" asked: a brand slice that does not say Edam is not the exact variety.
    assert not requested_type_match(BRAND_SLICES, _item("edam")).exact
    assert requested_type_match(EDAM_SLICES, _item("edam")).exact


def test_whipping_cream_35_percent_is_exact_when_the_product_states_35():
    cream_35 = _product("SYN-WC35", "10401", "Synthetic whipping cream 35 % 1 l UHT", "Synteettinen vispikerma 35 % 1 l")
    item = _item("whipping cream", unit="l", preferences=[Preference(kind="FAT_PERCENT", value="35")])
    assert requested_type_match(cream_35, item).exact
    # T52 is unchanged: a stated fat % that the product data does not prove is named, never assumed.
    unstated = _product("SYN-WCX", "10401", "Synthetic whipping cream 1 l UHT", "Synteettinen vispikerma 1 l")
    assert requested_type_match(unstated, item).differences == ("fat % not stated, 35 % asked",)


def test_generic_cheese_search_finds_variety_and_brand_slices():
    result = search_products("cheese", products=[EDAM_SLICES, BRAND_SLICES])
    assert {c.productId for c in result.candidates} == {"SYN-EDAM", "SYN-OLT"}


def _obs(code: str) -> CandidateObservation:
    return CandidateObservation(
        product_id=code,
        unit_price_excl_vat=Decimal("10.00"),
        comparison_price=Decimal("2.00"),
        comparison_unit="kg",
        vat_rate=Decimal("14"),
        stock_tier="PLENTY",
        available_quantity=Decimal("999999"),
        delivery_date=MONDAY,
        closing_time=None,
        reasons=(),
        facts=(),
    )
