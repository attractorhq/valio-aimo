"""T101: Swedish requests (laktosfri, till fredag) and misspelled free-from words are never dropped silently."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.agent.parse import parse_request
from app.catalog.normalise import normalise_product
from app.catalog.search import search_products
from app.contracts import Preference, ReasonCode, Requirement
from app.policy import M1
from tests.synthetic.catalog_records import synthetic_product_record

HKI = ZoneInfo("Europe/Helsinki")
NOW = datetime(2026, 11, 14, 9, 0, tzinfo=HKI)  # Saturday 14 Nov 2026

LACTOSE_FREE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")
GLUTEN_FREE = Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")
MILK_FREE = Requirement(kind="AVOID_ALLERGEN", code="AM")
EGG_FREE = Requirement(kind="AVOID_ALLERGEN", code="AE")
AN = Requirement(kind="AVOID_ALLERGEN", code="AN")
AP = Requirement(kind="AVOID_ALLERGEN", code="AP")
SOY_FREE = Requirement(kind="AVOID_ALLERGEN", code="AY")
FISH_FREE = Requirement(kind="AVOID_ALLERGEN", code="AF")


# --------------------------------------------------------------------------- 1. The two judge requests


def test_judge_swedish_sliced_lactose_free_cheese_by_friday():
    result = parse_request("Vi behöver 60 kg skivad laktosfri ost till fredag.", NOW)
    assert result.clarificationQuestion is None, result.clarificationQuestion
    assert result.request is not None
    assert result.request.requestedDate == date(2026, 11, 20)  # Friday 20 Nov 2026
    (item,) = result.request.items
    assert item.what == "sliced cheese"
    assert item.quantity == Decimal("60")
    assert item.unit == "kg"
    assert item.requirements == [LACTOSE_FREE]
    assert Preference(kind="OTHER", value="Sliced") in item.preferences
    assert "Delivered on or before Fri 20 Nov" in result.request.assumptions
    assert "Sliced" in result.request.assumptions

    # Verify non-lactose-free cheese is excluded so nothing can be AUTO-confirmed without the requirement.
    regular_cheese = normalise_product(
        synthetic_product_record(
            product_id="SYN-CHEESE-REG",
            category="10602",
            names={"en": "Synthetic sliced cheese 1 kg", "sv": "Syntetisk skivad ost 1 kg"},
            allergens=[("AM", "CONTAINS"), ("ML", "CONTAINS")],
            claims=[],
        )
    )
    lf_cheese = normalise_product(
        synthetic_product_record(
            product_id="SYN-CHEESE-LF",
            category="10602",
            names={"en": "Synthetic lactose-free sliced cheese 1 kg", "sv": "Syntetisk skivad laktosfri ost 1 kg"},
            allergens=[("AM", "CONTAINS")],
            claims=["FREE_FROM_LACTOSE"],
        )
    )
    search_res = search_products(item, mandate=M1, products=[regular_cheese, lf_cheese])
    assert [c.productId for c in search_res.candidates] == ["SYN-CHEESE-LF"]
    assert [e.productId for e in search_res.excluded] == ["SYN-CHEESE-REG"]
    assert ReasonCode.CLAIM_MISSING in search_res.excluded[0].reasons


def test_judge_misspelled_laktootonta_butter_fuzzy_matches_with_chip():
    result = parse_request("Tarvitsemme 50 kg laktootonta voita huomiseksi.", NOW)
    assert result.clarificationQuestion is None, result.clarificationQuestion
    assert result.request is not None
    (item,) = result.request.items
    assert item.what == "butter"
    assert item.quantity == Decimal("50")
    assert item.unit == "kg"
    assert item.requirements == [LACTOSE_FREE]
    assert "Read 'laktootonta' as lactose-free" in result.request.assumptions

    # Regular butter without FREE_FROM_LACTOSE is excluded, never silently proposed.
    regular_butter = normalise_product(
        synthetic_product_record(
            product_id="SYN-BUT-REG",
            category="10500",
            names={"en": "Synthetic unsalted butter 500 g", "fi": "Synteettinen suolaton voi 500 g"},
            allergens=[("AM", "CONTAINS"), ("ML", "CONTAINS")],
            claims=[],
        )
    )
    lf_butter = normalise_product(
        synthetic_product_record(
            product_id="SYN-BUT-LF",
            category="10500",
            names={"en": "Synthetic lactose-free butter 500 g", "fi": "Synteettinen laktoositon voi 500 g"},
            allergens=[("AM", "CONTAINS")],
            claims=["FREE_FROM_LACTOSE"],
        )
    )
    search_res = search_products(item, mandate=M1, products=[regular_butter, lf_butter])
    assert [c.productId for c in search_res.candidates] == ["SYN-BUT-LF"]
    assert [e.productId for e in search_res.excluded] == ["SYN-BUT-REG"]
    assert ReasonCode.CLAIM_MISSING in search_res.excluded[0].reasons


# --------------------------------------------------------------------------- 2. Table test: 12 Swedish requests


@pytest.mark.parametrize(
    ("text", "expected_what", "expected_qty", "expected_unit", "expected_reqs", "expected_date"),
    [
        (
            "Vi behöver 60 kg skivad laktosfri ost till fredag.",
            "sliced cheese",
            Decimal("60"),
            "kg",
            [LACTOSE_FREE],
            date(2026, 11, 20),
        ),
        (
            "Vi behöver 100 liter laktosfri mjölk till måndag.",
            "milk",
            Decimal("100"),
            "l",
            [LACTOSE_FREE],
            date(2026, 11, 16),
        ),
        (
            "50 kg laktosfritt smör till tisdag",
            "butter",
            Decimal("50"),
            "kg",
            [LACTOSE_FREE],
            date(2026, 11, 17),
        ),
        (
            "40 liter laktosfri grädde till onsdag",
            "cream",
            Decimal("40"),
            "l",
            [LACTOSE_FREE],
            date(2026, 11, 18),
        ),
        (
            "30 liter laktosfri vispgrädde till torsdag",
            "whipping cream",
            Decimal("30"),
            "l",
            [LACTOSE_FREE],
            date(2026, 11, 19),
        ),
        (
            "80 liter glutenfri havredryck till måndag",
            "oat drink",
            Decimal("80"),
            "l",
            [GLUTEN_FREE],
            date(2026, 11, 16),
        ),
        (
            "25 kg mjölkfritt smör till lördag",
            "butter",
            Decimal("25"),
            "kg",
            [MILK_FREE],
            date(2026, 11, 14),
        ),
        (
            "20 kg nötfri müsli till söndag",
            "muesli",
            Decimal("20"),
            "kg",
            [AN, AP],
            date(2026, 11, 15),
        ),
        (
            "15 kg äggfri pasta imorgon",
            "pasta",
            Decimal("15"),
            "kg",
            [EGG_FREE],
            date(2026, 11, 15),
        ),
        (
            "30 kg riven laktosfri ost idag",
            "grated cheese",
            Decimal("30"),
            "kg",
            [LACTOSE_FREE],
            date(2026, 11, 14),
        ),
        (
            "20 kg färsk laxfilé utan mjölk och nötter till fredag",
            "fresh salmon fillet",
            Decimal("20"),
            "kg",
            [MILK_FREE, AN, AP],
            date(2026, 11, 20),
        ),
        (
            "10 kg glutenfritt bröd till onsdag",
            "bread",
            Decimal("10"),
            "kg",
            [GLUTEN_FREE],
            date(2026, 11, 18),
        ),
    ],
)
def test_swedish_requests_table(text, expected_what, expected_qty, expected_unit, expected_reqs, expected_date):
    result = parse_request(text, NOW)
    assert result.clarificationQuestion is None, result.clarificationQuestion
    assert result.request is not None
    assert result.request.requestedDate == expected_date
    (item,) = result.request.items
    assert item.what == expected_what
    assert item.quantity == expected_qty
    assert item.unit == expected_unit
    assert item.requirements == expected_reqs


# --------------------------------------------------------------------------- 3. Table test: 12 misspellings


@pytest.mark.parametrize(
    ("text", "typo_word", "expected_what", "expected_reqs", "expected_chip"),
    [
        (
            "50 kg laktootonta voita huomiseksi",
            "laktootonta",
            "butter",
            [LACTOSE_FREE],
            "Read 'laktootonta' as lactose-free",
        ),
        (
            "100 l laktositonta maitoa maanantaiksi",
            "laktositonta",
            "milk",
            [LACTOSE_FREE],
            "Read 'laktositonta' as lactose-free",
        ),
        (
            "40 kg gluteentonta makaronia maanantaiksi",
            "gluteentonta",
            "macaroni",
            [GLUTEN_FREE],
            "Read 'gluteentonta' as gluten-free",
        ),
        (
            "40 kg glutenitonta pastaa huomiseksi",
            "glutenitonta",
            "pasta",
            [GLUTEN_FREE],
            "Read 'glutenitonta' as gluten-free",
        ),
        (
            "20 kg maidotona juustoa huomiseksi",
            "maidotona",
            "cheese",
            [MILK_FREE],
            "Read 'maidotona' as milk-free",
        ),
        (
            "15 kg pahkinatonta mysliä maanantaiksi",
            "pahkinatonta",
            "muesli",
            [AN, AP],
            "Read 'pahkinatonta' as nut-free",
        ),
        (
            "10 kg kananmunatona pastaa huomiseksi",
            "kananmunatona",
            "pasta",
            [EGG_FREE],
            "Read 'kananmunatona' as egg-free",
        ),
        (
            "Vi behöver 60 kg skivad laktosfir ost till fredag.",
            "laktosfir",
            "sliced cheese",
            [LACTOSE_FREE],
            "Read 'laktosfir' as lactose-free",
        ),
        (
            "Vi behöver 20 kg mjolkfri ost till måndag.",
            "mjolkfri",
            "cheese",
            [MILK_FREE],
            "Read 'mjolkfri' as milk-free",
        ),
        (
            "Vi behöver 15 kg notfri müsli till tisdag.",
            "notfri",
            "muesli",
            [AN, AP],
            "Read 'notfri' as nut-free",
        ),
        (
            "We need 80 l glutne-free oat drink by Monday",
            "glutne-free",
            "oat drink",
            [GLUTEN_FREE],
            "Read 'glutne-free' as gluten-free",
        ),
        (
            "We need 100 l lcatose-free milk by Monday",
            "lcatose-free",
            "milk",
            [LACTOSE_FREE],
            "Read 'lcatose-free' as lactose-free",
        ),
    ],
)
def test_misspelled_free_from_words_table(text, typo_word, expected_what, expected_reqs, expected_chip):
    result = parse_request(text, NOW)
    assert result.clarificationQuestion is None, result.clarificationQuestion
    assert result.request is not None
    (item,) = result.request.items
    assert item.what == expected_what
    assert typo_word not in item.what.lower()
    assert item.requirements == expected_reqs
    assert expected_chip in result.request.assumptions


# --------------------------------------------------------------------------- 4. Unmappable free-from words ask clarification


@pytest.mark.parametrize(
    ("text", "expected_token"),
    [
        ("50 kg sokeritonta jogurttia huomiseksi", "sokeritonta"),
        ("20 kg palmuöljytöntä levitettä maanantaiksi", "palmuöljytöntä"),
        ("15 kg histamiinitonta juustoa huomiseksi", "histamiinitonta"),
        ("Vi behöver 20 kg sockerfri yoghurt till fredag.", "sockerfri"),
        ("Vi behöver 10 kg tillsatsfri skinka imorgon.", "tillsatsfri"),
        ("We need 20 kg preservative-free ham by Monday.", "preservative-free"),
    ],
)
def test_unmappable_inline_free_from_words_ask_clarification_never_dropped(text, expected_token):
    result = parse_request(text, NOW)
    assert ReasonCode.CLARIFICATION_NEEDED in result.reasons
    assert result.clarificationQuestion is not None
    assert result.clarificationQuestion.count("?") == 1
    assert expected_token in result.clarificationQuestion


# --------------------------------------------------------------------------- 5. LLM path preserves Swedish & fuzzy requirements


def test_llm_path_preserves_swedish_and_fuzzy_requirements():
    def llm(_text, _today):
        return {
            "requestedDate": "2026-11-15",
            "items": [
                {
                    "what": "butter",
                    "quantity": 50,
                    "unit": "kg",
                    "productId": None,
                    "requirements": [],
                    "preferences": [],
                }
            ],
            "assumptions": [],
            "clarificationQuestion": None,
        }

    res = parse_request("Tarvitsemme 50 kg laktootonta voita huomiseksi.", NOW, interpreter=llm)
    assert res.mode == "LLM"
    assert res.request.items[0].requirements == [LACTOSE_FREE]
    assert "Read 'laktootonta' as lactose-free" in res.request.assumptions

    def llm_sv(_text, _today):
        return {
            "requestedDate": "2026-11-15",
            "items": [
                {
                    "what": "skivad laktosfri ost fredag",
                    "quantity": 60,
                    "unit": "kg",
                    "productId": None,
                    "requirements": [],
                    "preferences": [],
                }
            ],
            "assumptions": [],
            "clarificationQuestion": None,
        }

    res_sv = parse_request("Vi behöver 60 kg skivad laktosfri ost till fredag.", NOW, interpreter=llm_sv)
    assert res_sv.mode == "LLM"
    assert res_sv.request.requestedDate == date(2026, 11, 20)
    (sv_item,) = res_sv.request.items
    assert sv_item.what == "sliced cheese"
    assert sv_item.requirements == [LACTOSE_FREE]
    assert Preference(kind="OTHER", value="Sliced") in sv_item.preferences
