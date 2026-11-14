"""T99: 'nut-free' means tree nuts (AN) and peanuts (AP), and trailing constraint clauses apply to every item."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.agent.parse import parse_request
from app.contracts import Preference, Requirement

HKI = ZoneInfo("Europe/Helsinki")
NOW = datetime(2026, 11, 14, 9, 0, tzinfo=HKI)  # Sat 14 Nov -> Monday is 16 Nov

GLUTEN_FREE = Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")
LACTOSE_FREE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")
AE = Requirement(kind="AVOID_ALLERGEN", code="AE")
AM = Requirement(kind="AVOID_ALLERGEN", code="AM")
AN = Requirement(kind="AVOID_ALLERGEN", code="AN")
AP = Requirement(kind="AVOID_ALLERGEN", code="AP")

JUDGE_REQUEST = "We need 200 kg gluten-free and egg-free penne for Monday plus 50 litres of oat drink, must be nut-free"


def test_judge_request_applies_tree_nut_and_peanut_free_to_both_items():
    result = parse_request(JUDGE_REQUEST, NOW)
    assert result.clarificationQuestion is None
    assert result.reasons == []
    assert result.request is not None
    assert result.request.requestedDate == date(2026, 11, 16)

    penne, oat_drink = result.request.items
    assert (penne.what, penne.quantity, penne.unit) == ("penne", Decimal(200), "kg")
    assert penne.requirements == [GLUTEN_FREE, AE, AN, AP]

    assert (oat_drink.what, oat_drink.quantity, oat_drink.unit) == ("oat drink", Decimal(50), "l")
    assert oat_drink.requirements == [AN, AP]

    assert "Applied to every item: tree-nut-free, peanut-free" in result.request.assumptions


def test_judge_request_survives_incomplete_llm_interpretation():
    def incomplete_llm(_text, _today):
        return {
            "requestedDate": "2026-11-16",
            "items": [
                {
                    "what": "penne",
                    "quantity": 200,
                    "unit": "kg",
                    "productId": None,
                    "requirements": [
                        {"kind": "CLAIM", "code": "FREE_FROM_GLUTEN"},
                        {"kind": "AVOID_ALLERGEN", "code": "AE"},
                    ],
                    "preferences": [],
                },
                {
                    "what": "oat drink",
                    "quantity": 50,
                    "unit": "l",
                    "productId": None,
                    "requirements": [{"kind": "AVOID_ALLERGEN", "code": "AN"}],
                    "preferences": [],
                },
            ],
            "assumptions": ["UHT or fresh"],
            "clarificationQuestion": None,
        }

    result = parse_request(JUDGE_REQUEST, NOW, interpreter=incomplete_llm)
    assert result.mode == "LLM"
    assert result.clarificationQuestion is None
    penne, oat_drink = result.request.items
    assert {r.code for r in penne.requirements} == {"FREE_FROM_GLUTEN", "AE", "AN", "AP"}
    assert {r.code for r in oat_drink.requirements} == {"AN", "AP"}
    assert "Applied to every item: tree-nut-free, peanut-free" in result.request.assumptions


@pytest.mark.parametrize(
    ("text", "expected_codes"),
    [
        # General nut phrases (EN + FI) -> AN + AP
        ("50 kg nut-free pasta by Monday", [AN, AP]),
        ("50 kg nut free pasta by Monday", [AN, AP]),
        ("50 kg pasta, no nuts, by Monday", [AN, AP]),
        ("50 kg pasta without nuts by Monday", [AN, AP]),
        ("50 kg pasta free from nuts by Monday", [AN, AP]),
        ("50 kg pasta, nut allergy, by Monday", [AN, AP]),
        ("50 kg pasta, nut-allergic, by Monday", [AN, AP]),
        ("50 kg pasta, allergic to nuts, by Monday", [AN, AP]),
        ("50 kg pähkinätöntä pastaa maanantaiksi", [AN, AP]),
        ("50 kg pastaa ilman pähkinöitä maanantaiksi", [AN, AP]),
        ("50 kg pastaa, ei pähkinää, maanantaiksi", [AN, AP]),
        ("50 kg pastaa, pähkinäallergia, maanantaiksi", [AN, AP]),
        # Tree-nut-only phrases (EN + FI) -> AN only
        ("50 kg tree-nut-free pasta by Monday", [AN]),
        ("50 kg tree nut-free pasta by Monday", [AN]),
        ("50 kg tree nut free pasta by Monday", [AN]),
        ("50 kg treenut-free pasta by Monday", [AN]),
        ("50 kg pasta, no tree nuts, by Monday", [AN]),
        ("50 kg pasta without tree-nuts by Monday", [AN]),
        ("50 kg pasta free from tree nuts by Monday", [AN]),
        ("50 kg pasta, tree-nut allergy, by Monday", [AN]),
        ("50 kg pasta, tree nut allergy, by Monday", [AN]),
        ("50 kg pasta, allergic to tree nuts, by Monday", [AN]),
        ("50 kg puupähkinätöntä pastaa maanantaiksi", [AN]),
        ("50 kg pastaa ilman puupähkinöitä maanantaiksi", [AN]),
        ("50 kg pastaa, puupähkinäallergia, maanantaiksi", [AN]),
        # Peanut-only phrases (EN + FI) -> AP only
        ("50 kg peanut-free pasta by Monday", [AP]),
        ("50 kg peanut free pasta by Monday", [AP]),
        ("50 kg pasta, no peanuts, by Monday", [AP]),
        ("50 kg pasta without peanuts by Monday", [AP]),
        ("50 kg pasta free from peanuts by Monday", [AP]),
        ("50 kg pasta, peanut allergy, by Monday", [AP]),
        ("50 kg pasta, peanut-allergic, by Monday", [AP]),
        ("50 kg pasta, allergic to peanuts, by Monday", [AP]),
        ("50 kg maapähkinätöntä pastaa maanantaiksi", [AP]),
        ("50 kg pastaa ilman maapähkinöitä maanantaiksi", [AP]),
        ("50 kg pastaa, ei maapähkinää, maanantaiksi", [AP]),
        ("50 kg pastaa, maapähkinäallergia, maanantaiksi", [AP]),
    ],
)
def test_nut_tree_nut_and_peanut_vocabulary(text, expected_codes):
    result = parse_request(text, NOW)
    assert result.clarificationQuestion is None, result.clarificationQuestion
    (item,) = result.request.items
    assert item.what == "pasta"
    assert item.requirements == expected_codes


@pytest.mark.parametrize(
    ("text", "expected_codes", "expected_chip"),
    [
        (
            "We need 200 kg gluten-free penne plus 50 litres of oat drink, must be nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, no nuts",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, without nuts",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, nut allergy",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, one pupil has a nut allergy",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, all nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, all items must be nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, both must be nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, everything must be nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink and everything must be nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink — must be nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink; must be nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "Everything must be nut-free: 200 kg penne plus 50 litres of oat drink",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, must be tree-nut-free",
            {"AN"},
            "Applied to every item: tree-nut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, must be peanut-free",
            {"AP"},
            "Applied to every item: peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, must be gluten-free and nut-free",
            {"FREE_FROM_GLUTEN", "AN", "AP"},
            "Applied to every item: gluten-free, tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne plus 50 litres of oat drink, no milk, eggs or nuts, by Monday",
            {"AM", "AE", "AN", "AP"},
            "Applied to every item: milk-free, egg-free, tree-nut-free, peanut-free",
        ),
        (
            "60 kg pastaa ja 30 litraa kaurajuomaa maanantaiksi, täysin pähkinätön",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "60 kg pastaa ja 30 litraa kaurajuomaa maanantaiksi, kaiken pitää olla pähkinätöntä",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg gluten-free and egg-free penne for Tue 17 Nov plus 50 litres of oat drink, must be nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "We need 200 kg penne and 50 litres of oat drink, and everything must be nut-free",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
        (
            "60 kg pastaa ja 30 litraa kaurajuomaa maanantaiksi, ei saa sisältää pähkinöitä",
            {"AN", "AP"},
            "Applied to every item: tree-nut-free, peanut-free",
        ),
    ],
)
def test_trailing_and_request_wide_clauses_apply_to_every_item(text, expected_codes, expected_chip):
    result = parse_request(text, NOW)
    assert result.clarificationQuestion is None, result.clarificationQuestion
    assert result.reasons == []
    assert len(result.request.items) == 2
    first, second = result.request.items
    assert first.what in ("penne", "pasta")
    assert second.what == "oat drink"
    assert expected_codes <= {r.code for r in first.requirements}
    assert {r.code for r in second.requirements} == expected_codes
    assert expected_chip in result.request.assumptions


def test_trailing_constraint_after_item_form_or_pack_preserves_item_local_preference():
    grated = parse_request(
        "60 l cooking cream and 25 kg Emmental cheese, grated, must be lactose-free", NOW
    )
    assert grated.clarificationQuestion is None
    cream, cheese = grated.request.items
    assert (cream.what, cheese.what) == ("cooking cream", "grated Emmental cheese")
    assert Preference(kind="OTHER", value="Grated") not in cream.preferences
    assert Preference(kind="OTHER", value="Grated") in cheese.preferences
    assert cream.requirements == [LACTOSE_FREE]
    assert cheese.requirements == [LACTOSE_FREE]
    assert "Applied to every item: lactose-free" in grated.request.assumptions

    packed = parse_request("200 kg penne plus 20 kg flour, 5 kg bags, must be nut-free", NOW)
    assert packed.clarificationQuestion is None
    penne, flour = packed.request.items
    assert (penne.what, flour.what) == ("penne", "flour")
    assert Preference(kind="PACK_SIZE", value="5 kg") not in penne.preferences
    assert Preference(kind="PACK_SIZE", value="5 kg") in flour.preferences
    assert penne.requirements == [AN, AP]
    assert flour.requirements == [AN, AP]
    assert "Applied to every item: tree-nut-free, peanut-free" in packed.request.assumptions


def test_item_local_nut_modifiers_stay_on_their_own_item():
    result = parse_request(
        "20 kg tree-nut-free pasta, 10 kg peanut-free muesli and 15 kg nut-free flour by Monday", NOW
    )
    assert result.clarificationQuestion is None
    pasta, muesli, flour = result.request.items
    assert (pasta.what, pasta.requirements) == ("pasta", [AN])
    assert (muesli.what, muesli.requirements) == ("muesli", [AP])
    assert (flour.what, flour.requirements) == ("flour", [AN, AP])
    assert not any(a.startswith("Applied to every item:") for a in result.request.assumptions)

    two_sentences = parse_request(
        "We need 200 kg penne, gluten-free. We also need 50 litres of milk.", NOW
    )
    assert two_sentences.clarificationQuestion is None
    penne, milk = two_sentences.request.items
    assert (penne.requirements, milk.requirements) == ([GLUTEN_FREE], [])
    assert not any(a.startswith("Applied to every item:") for a in two_sentences.request.assumptions)


def test_llm_cannot_drop_peanut_half_of_english_nut_free_when_item_counts_differ():
    def llm(_text, _today):
        return {
            "requestedDate": "2026-11-16",
            "items": [
                {
                    "what": "pasta",
                    "quantity": 10,
                    "unit": "kg",
                    "productId": None,
                    "requirements": [{"kind": "AVOID_ALLERGEN", "code": "AN"}],
                    "preferences": [],
                },
                {
                    "what": "sauce",
                    "quantity": 5,
                    "unit": "kg",
                    "productId": None,
                    "requirements": [],
                    "preferences": [],
                },
            ],
            "assumptions": [],
            "clarificationQuestion": None,
        }

    result = parse_request("10 kg nut-free pasta by Monday", NOW, interpreter=llm)
    assert result.mode == "LLM"
    assert result.request.items[0].requirements == [AN, AP]
    assert result.request.items[1].requirements == []
