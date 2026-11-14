"""T87: product form as a hard requirement in search and planning, and 'X or Y' / 'X tai Y' union search."""

from datetime import date
from decimal import Decimal

import pytest

from app.agent.plan import (
    CandidateObservation,
    build_shortlist_offer_items,
    rank_candidates,
    requested_type_match,
)
from app.catalog.normalise import normalise_product
from app.catalog.search import search_products
from app.catalog.vocab import extract_requested_form, product_form_difference
from app.contracts import Preference, ReasonCode, RequestItem, Requirement
from app.policy import M1
from tests.synthetic.catalog_records import synthetic_product_record

MONDAY = date(2026, 11, 16)
GLUTEN_FREE = Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")
MILK_FREE = Requirement(kind="AVOID_ALLERGEN", code="AM")


def _synth_product(
    product_id: str,
    category: str,
    en: str,
    fi: str,
    *,
    grams: int = 1000,
    allergens: list[tuple[str, str]] | None = None,
    claims: list[str] | None = None,
):
    return normalise_product(
        synthetic_product_record(
            product_id=product_id,
            category=category,
            names={"en": en, "fi": fi},
            allergens=allergens if allergens is not None else [("AM", "CONTAINS")],
            claims=claims if claims is not None else ["FREE_FROM_LACTOSE"],
            unit_conversions=[
                {"synkkaPackagingType": "BASE_UNIT_OR_EACH", "netWeight": {"unit": "GRM", "value": grams}}
            ],
        )
    )


def _obs(product_id: str, unit_price: str, comp_price: str) -> CandidateObservation:
    return CandidateObservation(
        product_id=product_id,
        unit_price_excl_vat=Decimal(unit_price),
        comparison_price=Decimal(comp_price),
        comparison_unit="kg",
        vat_rate=Decimal("14"),
        stock_tier="PLENTY",
        available_quantity=Decimal("1000"),
        delivery_date=MONDAY,
    )


CHEESE_POOL = [
    _synth_product(
        "SYN-EMM-SLICE",
        "10602",
        "Synthetic Emmental cheese slices 800 g",
        "Synteettinen emmentalviipale 800 g",
        grams=800,
    ),
    _synth_product(
        "SYN-EMM-GRATED",
        "10603",
        "Synthetic Emmental grated cheese 2500 g",
        "Synteettinen emmental raaste 2500 g",
        grams=2500,
    ),
    _synth_product(
        "SYN-EMM-BLOCK",
        "10600",
        "Synthetic Emmental cheese 625 g",
        "Synteettinen emmental 625 g",
        grams=625,
    ),
    _synth_product(
        "SYN-FETA-CUBE",
        "10636",
        "Synthetic feta cheese cubes 2000 g",
        "Synteettinen fetajuustokuutio 2000 g",
        grams=2000,
    ),
]


@pytest.mark.parametrize(
    ("query", "form_id", "label"),
    [
        ("grated Emmental cheese", "grated", "Grated"),
        ("emmental-juustoa raasteena", "grated", "Grated"),
        ("juustoraastetta", "grated", "Grated"),
        ("sliced Emmental cheese", "sliced", "Sliced"),
        ("emmental-juustoa viipaleina", "sliced", "Sliced"),
        ("block Emmental cheese", "block", "Block"),
        ("emmental-juustoa palana", "block", "Block"),
        ("juustoa kimpaleena", "block", "Block"),
        ("shredded chicken", "shredded", "Shredded"),
        ("broilerisuikaleita", "shredded", "Shredded"),
        ("cubed feta cheese", "cubed", "Cubed"),
        ("fetajuustoa kuutioina", "cubed", "Cubed"),
        ("whole salmon", "whole", "Whole"),
        ("kokonainen lohi", "whole", "Whole"),
        ("milk powder", "powder", "Powder"),
        ("maitojauhetta", "powder", "Powder"),
    ],
)
def test_extract_requested_form_fi_and_en(query, form_id, label):
    form = extract_requested_form(query)
    assert form is not None
    assert (form.form_id, form.label) == (form_id, label)


@pytest.mark.parametrize(
    ("product_idx", "requested_query", "expected_diff"),
    [
        (0, "grated Emmental cheese", "sliced, not grated"),
        (1, "grated Emmental cheese", None),
        (2, "grated Emmental cheese", "block, not grated"),
        (0, "sliced Emmental cheese", None),
        (1, "sliced Emmental cheese", "grated, not sliced"),
        (2, "block Emmental cheese", None),
        (0, "block Emmental cheese", "sliced, not block"),
        (3, "cubed feta cheese", None),
    ],
)
def test_product_form_difference_from_names_and_category(product_idx, requested_query, expected_diff):
    prod = CHEESE_POOL[product_idx]
    form = extract_requested_form(requested_query)
    diff = product_form_difference((prod.name, *prod.names.values()), prod.category, form)
    assert diff == expected_diff


def test_grated_emmental_never_recommends_cheaper_slices():
    item = RequestItem(
        itemId="I1",
        what="grated Emmental cheese",
        quantity=Decimal("25"),
        unit="kg",
        preferences=[Preference(kind="OTHER", value="Grated")],
    )
    res = search_products(item, products=CHEESE_POOL)
    assert res.candidates[0].productId == "SYN-EMM-GRATED"

    # Even when slices are cheaper per kg than grated, grated is RECOMMENDED (exactType=True)
    # and slices are only an AGENT_SUBSTITUTE (exactType=False).
    observations = {
        "SYN-EMM-SLICE": _obs("SYN-EMM-SLICE", "6.40", "8.00"),
        "SYN-EMM-GRATED": _obs("SYN-EMM-GRATED", "22.50", "9.00"),
        "SYN-EMM-BLOCK": _obs("SYN-EMM-BLOCK", "5.00", "8.00"),
    }
    ranked = rank_candidates(
        res.candidates,
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations=observations,
    )
    assert ranked[0].productId == "SYN-EMM-GRATED"
    assert ranked[0].exactType is True
    assert ranked[0].substitutionSource is None

    by_id = {rc.productId: rc for rc in ranked}
    assert by_id["SYN-EMM-SLICE"].exactType is False
    assert by_id["SYN-EMM-SLICE"].substitutionSource == "AGENT"
    assert ReasonCode.AGENT_SUBSTITUTE in by_id["SYN-EMM-SLICE"].reasons
    assert "sliced, not grated" in by_id["SYN-EMM-SLICE"].tradeoff

    offer_items = build_shortlist_offer_items(item, ranked_candidates=ranked)
    assert [oi.productId for oi in offer_items] == ["SYN-EMM-GRATED"]


def test_only_wrong_form_available_is_flagged_as_agent_substitute():
    item = RequestItem(
        itemId="I1",
        what="grated Emmental cheese",
        quantity=Decimal("25"),
        unit="kg",
        preferences=[Preference(kind="OTHER", value="Grated")],
    )
    only_slice = [CHEESE_POOL[0]]
    ranked = rank_candidates(
        only_slice,
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations={"SYN-EMM-SLICE": _obs("SYN-EMM-SLICE", "6.40", "8.00")},
    )
    assert len(ranked) == 1
    assert ranked[0].exactType is False
    assert ranked[0].substitutionSource == "AGENT"
    assert ReasonCode.AGENT_SUBSTITUTE in ranked[0].reasons
    assert "sliced, not grated" in ranked[0].tradeoff


def test_meat_sauce_or_minced_meat_searches_both_and_ranks_union():
    sauce = _synth_product(
        "SYN-SAUCE",
        "17300",
        "Synthetic Bolognese meat sauce 3000 g",
        "Synteettinen jauhelihakastike 3000 g",
        grams=3000,
        allergens=[("AM", "FREE_FROM"), ("AW", "FREE_FROM")],
        claims=["FREE_FROM_GLUTEN"],
    )
    mince = _synth_product(
        "SYN-MINCE",
        "18760",
        "Synthetic beef minced meat 2000 g",
        "Synteettinen naudan jauheliha 2000 g",
        grams=2000,
        allergens=[("AM", "FREE_FROM"), ("AW", "FREE_FROM")],
        claims=["FREE_FROM_GLUTEN"],
    )
    item = RequestItem(
        itemId="I1",
        what="meat sauce or minced meat",
        quantity=Decimal("40"),
        unit="kg",
        requirements=[GLUTEN_FREE, MILK_FREE],
    )
    res = search_products(item, products=[sauce, mince, *CHEESE_POOL])
    assert {c.productId for c in res.candidates} == {"SYN-SAUCE", "SYN-MINCE"}

    assert requested_type_match(sauce, item).exact is True
    assert requested_type_match(mince, item).exact is True

    ranked = rank_candidates(
        res.candidates,
        item=item,
        mandate=M1,
        requested_date=MONDAY,
        observations={
            "SYN-SAUCE": _obs("SYN-SAUCE", "24.00", "8.00"),
            "SYN-MINCE": _obs("SYN-MINCE", "14.00", "7.00"),
        },
    )
    assert [rc.productId for rc in ranked] == ["SYN-MINCE", "SYN-SAUCE"]
    assert all(rc.exactType and rc.substitutionSource is None for rc in ranked)
