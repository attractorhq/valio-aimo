"""T74: product type and name hygiene. Compounds resolve to the most specific type, brand names stay names,
purpose and occasion clauses and certification words never enter the item name, and a milk-free question
offers an alternative of the same product type. Every example the blind judge reported is a case here."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent.parse import parse_request
from app.catalog.vocab import resolve_query_concepts
from app.contracts import Preference, ReasonCode, Requirement

HKI = ZoneInfo("Europe/Helsinki")
MORNING = datetime(2026, 11, 14, 9, 0, tzinfo=HKI)  # Sat 14 Nov, 09:00
LACTOSE_FREE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")
GLUTEN_FREE = Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")
MSC = Preference(kind="LABEL", value="MARINE_STEWARDSHIP_COUNCIL_LABEL")


def only_item(text: str):
    result = parse_request(text, MORNING)
    assert result.clarificationQuestion is None, result.clarificationQuestion
    (item,) = result.request.items
    return item, result.request


def concept_of(what: str) -> str | None:
    concepts = resolve_query_concepts(what)
    return concepts[0].concept_id if concepts else None


# --------------------------------------------------------------------------- compounds: the most specific type

# (Finnish request word, English item name, the catalogue's product type for that name)
COMPOUNDS = [
    ("kermajuustoa", "tilsit cheese", "cheese"),
    ("tuorejuustoa", "cream cheese", "cream_cheese"),
    ("sulatejuustoa", "processed cheese", "processed_cheese"),
    ("sulatejuustoviipaleita", "processed cheese", "processed_cheese"),
    ("ruokakermaa", "cooking cream", "cream"),
    ("vispikermaa", "whipping cream", "cream"),
    ("kuohukermaa", "whipping cream", "cream"),
    ("kahvikermaa", "coffee cream", "cream"),
    ("kevytkermaa", "low-fat cream", "cream"),
    ("kermaviiliä", "sour cream", "sour_cream"),
    ("smetanaa", "sour cream", "sour_cream"),
    ("jogurttia", "yoghurt", "yoghurt"),
    ("mustikkajogurttia", "blueberry yoghurt", "yoghurt"),
    ("rahkaa", "quark", "quark"),
    ("maitorahkaa", "quark", "quark"),
    ("voita", "butter", "butter"),
    ("levitettä", "fat spread", "butter"),
    ("kasvirasvalevitettä", "vegetable fat spread", "butter"),
    ("maitojauhetta", "milk powder", "milk_powder"),
    ("maitojuomaa", "milk drink", "milk"),
    ("kevytmaitojuomaa", "light milk drink", "milk"),
    ("kevytmaitoa", "light milk", "milk"),
    ("piimää", "sour milk", "milk"),
    ("kahvimaitoa", "coffee milk", "milk"),
    ("kaurajuomaa", "oat drink", "oat_drink"),
    ("riisijauhoa", "rice flour", "flour"),
    ("ruisleipää", "rye bread", "bread"),
    ("maitosuklaata", "milk chocolate", None),
    ("kermajäätelöä", "ice cream", None),
]


@pytest.mark.parametrize(("finnish", "english", "concept"), COMPOUNDS)
def test_finnish_compound_resolves_to_the_most_specific_type(finnish, english, concept):
    item, _ = only_item(f"Tarvitsemme 10 kg {finnish} huomiseksi")
    assert item.what == english
    assert concept_of(item.what) == concept


@pytest.mark.parametrize(
    ("query", "concept"),
    [
        ("cream cheese", "cream_cheese"),
        ("natural cream cheese", "cream_cheese"),
        ("processed cheese", "processed_cheese"),
        ("milk powder", "milk_powder"),
        ("sour cream", "sour_cream"),
        ("coffee cream", "cream"),
        ("fat spread", "butter"),
        ("margarine", "butter"),
        ("cheese spread", "cheese"),
        ("tilsit cheese", "cheese"),
        ("ice cream", None),
        ("peanut butter", None),
        ("milk chocolate", None),
        ("kermajuusto", "cheese"),
        ("tuorejuusto", "cream_cheese"),
        ("levite", "butter"),
        ("irtotee", "tea"),
        ("green tea", "tea"),
        ("milk", "milk"),
        ("cream", "cream"),
    ],
)
def test_the_longest_match_wins_over_its_head_word(query, concept):
    assert concept_of(query) == concept


def test_judge_kermajuusto_is_a_cheese_never_cream():
    """Finnish "kermajuusto" is a semi-hard cheese (Valio's own English name for it is tilsit); cream cheese
    is "tuorejuusto". It must never become cream, and the reading is said in the assumptions."""
    item, request = only_item("Tarvitsen huomiseksi 40 kg laktoositonta ja gluteenitonta kermajuustoa")
    assert item.what == "tilsit cheese"
    assert concept_of(item.what) == "cheese"
    assert item.requirements == [LACTOSE_FREE, GLUTEN_FREE]
    assert any("Kermajuusto” read as tilsit" in a for a in request.assumptions)


def test_english_cream_cheese_is_cream_cheese():
    item, _ = only_item("We need 40 kg lactose-free cream cheese by Monday")
    assert item.what == "cream cheese"
    assert item.requirements == [LACTOSE_FREE]
    assert concept_of(item.what) == "cream_cheese"


# --------------------------------------------------------------------------- brand and product names


def test_judge_brand_name_is_never_a_negation():
    item, _ = only_item("Need 400 litres of Oatly No Sugars oat drink 1.5% for Monday")
    assert item.what == "Oatly No Sugars oat drink"
    assert item.requirements == []
    assert Preference(kind="FAT_PERCENT", value="1.5") in item.preferences
    assert concept_of(item.what) == "oat_drink"


@pytest.mark.parametrize(
    ("text", "what"),
    [
        ("Need 20 l Coca-Cola Zero for tomorrow", "Coca-Cola Zero"),
        ("We need 20 l Valio Eila milk drink tomorrow", "Valio Eila milk drink"),
        ("Please send 30 l Planto No Added Sugar oat drink tomorrow", "Planto No Added Sugar oat drink"),
    ],
)
def test_brand_phrases_stay_in_the_name(text, what):
    item, _ = only_item(text)
    assert item.what == what


def test_a_real_negation_outside_a_brand_name_still_asks():
    result = parse_request("We need 10 l oat drink with no sugar tomorrow", MORNING)
    assert ReasonCode.CLARIFICATION_NEEDED in result.reasons
    assert "no sugar" in result.clarificationQuestion


# --------------------------------------------------------------------------- purpose and occasion clauses


@pytest.mark.parametrize(
    ("text", "what"),
    [
        ("Tarvitsen tänään 200 kg tuoretta lohifileetä, iso juhla illalla", "fresh salmon fillet"),
        ("200 kg fresh salmon fillet, big party tonight", "fresh salmon fillet"),
        ("Tarvitsemme 40 kg juustoa hotellin aamiaiselle", "cheese"),
        ("40 kg juustoa hotellin aamiaiselle", "cheese"),
        ("50 kg lohta juhlia varten huomiseksi", "salmon"),
        ("Tarvitsemme 30 kg kinkkua yrityksen pikkujouluihin", "ham"),
        ("Tarvitsemme 10 kg Juhla Mokka kahvia", "Juhla Mokka coffee"),
    ],
)
def test_purpose_and_occasion_clauses_never_enter_the_name(text, what):
    item, _ = only_item(text)
    assert item.what == what


def test_diet_words_in_a_purpose_clause_still_count():
    item, _ = only_item("40 kg gluteenitonta pastaa koulun keliaakikoille")
    assert item.what == "pasta"
    assert GLUTEN_FREE in item.requirements


def test_judge_occasion_clause_keeps_the_same_day_deadline():
    item, request = only_item("Tarvitsen tänään 200 kg tuoretta lohifileetä, iso juhla illalla")
    assert item.what == "fresh salmon fillet"
    assert request.requestedDate.isoformat() == "2026-11-14"
    assert any("in the evening" in a for a in request.assumptions)


# --------------------------------------------------------------------------- certification words


def test_judge_certification_is_kept_out_of_the_name_and_said():
    item, request = only_item("20 kg salmon fillet, MSC certified, by Wednesday 18.11.")
    assert item.what == "salmon fillet"
    assert MSC in item.preferences
    assert "MSC label preferred: MSC-labelled products are ranked first (not a hard requirement)" in (
        request.assumptions
    )


@pytest.mark.parametrize(
    ("text", "what", "label"),
    [
        ("20 kg salmon fillet, ASC certified, tomorrow", "salmon fillet", "AQUACULTURE_STEWARDSHIP_COUNCIL"),
        ("Tarvitsemme 20 kg MSC-merkittyä lohifileetä huomiseksi", "salmon fillet",
         "MARINE_STEWARDSHIP_COUNCIL_LABEL"),
        ("10 kg Fairtrade coffee tomorrow", "coffee", "FAIR_TRADE_MARK"),
        ("Tarvitsemme 10 kg kahvia, Reilun kaupan, huomiseksi", "coffee", "FAIR_TRADE_MARK"),
    ],
)
def test_certifications_become_label_preferences(text, what, label):
    item, _ = only_item(text)
    assert item.what == what
    assert Preference(kind="LABEL", value=label) in item.preferences


def test_organic_certified_is_the_organic_preference_only():
    item, _ = only_item("10 kg organic certified butter tomorrow")
    assert item.what == "butter"
    assert Preference(kind="ORGANIC", value="organic") in item.preferences


# --------------------------------------------------------------------------- same-type clarifying question


def test_judge_milk_free_cheddar_from_cow_milk_offers_a_cheese_alternative():
    result = parse_request("10 kg milk-free cheddar made from cow milk", MORNING)
    assert ReasonCode.CLARIFICATION_NEEDED in result.reasons
    question = result.clarificationQuestion
    assert "a milk-free cheese alternative" in question
    assert "oat drink" not in question
    assert question.count("?") == 1


def test_milk_itself_still_offers_oat_drink():
    result = parse_request("10 l milk-free milk tomorrow", MORNING)
    assert result.clarificationQuestion == (
        "Milk is a dairy product, so it cannot also be milk-free: do you need lactose-free milk, "
        "or a milk-free alternative such as oat drink?"
    )


def test_a_dairy_product_without_a_known_type_names_no_example():
    result = parse_request("10 kg milk-free milk chocolate tomorrow", MORNING)
    assert result.clarificationQuestion.endswith("or a milk-free alternative?")
    assert "oat drink" not in result.clarificationQuestion


def test_vegan_yoghurt_made_from_milk_offers_a_vegan_yoghurt_alternative():
    result = parse_request("10 kg vegan yoghurt made from cow's milk tomorrow", MORNING)
    assert "a vegan yoghurt alternative" in result.clarificationQuestion


def test_milk_free_cheese_alone_is_not_a_contradiction():
    item, _ = only_item("10 kg milk-free cheese tomorrow")
    assert item.what == "cheese"


# --------------------------------------------------------------------------- the LLM path keeps the same hygiene


def test_llm_name_is_cleaned_and_the_certification_label_is_kept():
    def llm(_text, _today):
        return {
            "requestedDate": "2026-11-18",
            "items": [{"what": "salmon fillet MSC certified", "quantity": 20, "unit": "kg", "productId": None,
                       "requirements": [], "preferences": []}],
            "assumptions": [],
            "clarificationQuestion": None,
        }

    result = parse_request("20 kg salmon fillet, MSC certified, by Wednesday 18.11.", MORNING, interpreter=llm)
    assert result.mode == "LLM"
    (item,) = result.request.items
    assert item.what == "salmon fillet"
    assert MSC in item.preferences
    assert any(a.startswith("MSC label preferred") for a in result.request.assumptions)


def test_llm_path_keeps_the_kermajuusto_reading_and_a_brand_name_is_never_a_question():
    def llm(_text, _today):
        return {
            "requestedDate": "2026-11-16",
            "items": [{"what": "Oatly No Sugars oat drink", "quantity": 400, "unit": "l", "productId": None,
                       "requirements": [], "preferences": [{"kind": "FAT_PERCENT", "value": "1.5"}]}],
            "assumptions": [],
            "clarificationQuestion": None,
        }

    result = parse_request("Need 400 litres of Oatly No Sugars oat drink 1.5% for Monday", MORNING, interpreter=llm)
    assert result.clarificationQuestion is None
    assert result.request.items[0].what == "Oatly No Sugars oat drink"

    def llm_fi(_text, _today):
        return {
            "requestedDate": "2026-11-15",
            "items": [{"what": "tilsit cheese", "quantity": 40, "unit": "kg", "productId": None,
                       "requirements": [], "preferences": []}],
            "assumptions": [],
            "clarificationQuestion": None,
        }

    result = parse_request("Tarvitsen huomiseksi 40 kg kermajuustoa", MORNING, interpreter=llm_fi)
    assert any("Kermajuusto” read as tilsit" in a for a in result.request.assumptions)
