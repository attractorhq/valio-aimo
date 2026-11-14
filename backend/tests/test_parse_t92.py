"""Finnish product inflections and request-wide dietary context."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent.parse import parse_request
from app.catalog.normalise import normalise_product
from app.catalog.search import search_products
from app.contracts import Requirement
from tests.synthetic.catalog_records import synthetic_product_record

NOW = datetime(2026, 11, 14, 9, tzinfo=ZoneInfo("Europe/Helsinki"))


@pytest.mark.parametrize(("word", "product"), [
    ("majoneesia", "mayonnaise"),
    ("sinappia", "mustard"),
    ("tomaatteja", "tomato"),
    ("porkkanaa", "carrot"),
    ("kurkkuja", "cucumber"),
    ("sipuleita", "onion"),
    ("lohta", "salmon"),
    ("teetä", "tea"),
    ("voita", "butter"),
    ("riisiä", "rice"),
    ("jauhoja", "flour"),
    ("perunoita", "potatoes"),
    ("juustoja", "cheese"),
    ("leipää", "bread"),
    ("kanoja", "chicken"),
    ("piimää", "sour milk"),
])
def test_inflected_product_names(word, product):
    result = parse_request(f"Tarvitsemme 15 kg {word} huomiseksi.", NOW)
    assert result.clarificationQuestion is None
    assert [item.what for item in result.request.items] == [product]


def test_school_diet_applies_even_to_the_next_item_in_the_same_sentence():
    result = parse_request(
        "Koulukeittiöön 60 kg pastaa ja 30 litraa maidotonta kaurajuomaa maanantaiksi, lapsella on keliakia.",
        NOW,
    )
    assert result.clarificationQuestion is None
    pasta, drink = result.request.items
    assert (pasta.what, drink.what) == ("pasta", "oat drink")
    assert {requirement.code for requirement in pasta.requirements} == {"FREE_FROM_GLUTEN"}
    assert {requirement.code for requirement in drink.requirements} == {"FREE_FROM_GLUTEN", "AM"}
    assert "Applied to every item: gluten-free" in result.request.assumptions


def test_hospital_request_finds_mayonnaise_with_egg_exclusion():
    result = parse_request("Sairaalan keittiö: 15 kg kananmunatonta majoneesia huomiseksi", NOW)
    assert result.clarificationQuestion is None
    (item,) = result.request.items
    assert item.what == "mayonnaise"
    assert item.requirements == [Requirement(kind="AVOID_ALLERGEN", code="AE")]
    products = [
        normalise_product(synthetic_product_record(
            product_id=code, category="215", names={"en": "Synthetic mayonnaise", "fi": "Synteettinen majoneesi"},
            allergens=[("AE", evidence)],
        ))
        for code, evidence in (("SYN-EGG-FREE", "FREE_FROM"), ("SYN-EGG", "CONTAINS"))
    ]
    search = search_products(item.what, products=products, requirements=item.requirements)
    assert [candidate.productId for candidate in search.candidates] == ["SYN-EGG-FREE"]
