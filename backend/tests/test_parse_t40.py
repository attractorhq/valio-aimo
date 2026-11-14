"""T40: Finnish inflections, clean item names, pähkinä = peanut + tree nut, same-day deadlines, and
clarifying questions in the buyer's language. Every example the blind judge reported is a case here."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.agent.parse import _is_finnish, parse_request
from app.contracts import ReasonCode, Requirement

HKI = ZoneInfo("Europe/Helsinki")
MORNING = datetime(2026, 11, 14, 9, 0, tzinfo=HKI)  # Sat 14 Nov, 09:00
AFTERNOON = datetime(2026, 11, 14, 15, 0, tzinfo=HKI)
TODAY = date(2026, 11, 14)
TOMORROW = date(2026, 11, 15)
AN = Requirement(kind="AVOID_ALLERGEN", code="AN")
AP = Requirement(kind="AVOID_ALLERGEN", code="AP")


def items_of(text: str, now: datetime = MORNING) -> list[tuple[str, Decimal, str]]:
    result = parse_request(text, now)
    assert result.clarificationQuestion is None, result.clarificationQuestion
    return [(i.what, i.quantity, i.unit) for i in result.request.items]


# --------------------------------------------------------------------------- Finnish inflections


@pytest.mark.parametrize(("text", "what"), [
    ("Tarvitsen 40 kg voita huomiseksi", "butter"),  # the judge's live check
    ("40 kg voi huomiseksi", "butter"),
    ("40 kg Valio voita huomiseksi", "Valio butter"),
    ("5 litraa kermaa huomiseksi", "cream"),
    ("5 litraa kermavaahtoa huomiseksi", "whipping cream"),
    ("5 litraa kuohukermaa huomiseksi", "whipping cream"),
    ("2 kg kermaviiliä huomiseksi", "sour cream"),  # not cream
    ("10 kg juustoa huomiseksi", "cheese"),
    ("10 kg juusto huomiseksi", "cheese"),
    ("10 litraa maitoa huomiseksi", "milk"),
    ("5 kg lohta huomiseksi", "salmon"),
    ("5 kg lohi huomiseksi", "salmon"),
    ("5 kg lohifileetä huomiseksi", "salmon fillet"),
    ("30 kg pastaa huomiseksi", "pasta"),
    ("30 kg pasta huomiseksi", "pasta"),
    ("Voitteko toimittaa 10 kg juustoa huomiseksi?", "cheese"),  # "voi-" as a verb is not butter
])
def test_finnish_inflections_resolve_to_the_product(text, what):
    ((parsed, _quantity, _unit),) = items_of(text)
    assert parsed == what


def test_two_inflected_items():
    assert items_of("10 kg juustoa ja 5 kg lohta huomiseksi") == [
        ("cheese", Decimal(10), "kg"), ("salmon", Decimal(5), "kg"),
    ]


# --------------------------------------------------------------------------- stray tokens


@pytest.mark.parametrize(("text", "what"), [
    ("We need 10 kg of Emmental cheese immediately", "Emmental cheese"),
    ("20 kg butter automatically by Monday", "butter"),
    ("15 kg fresh salmon fillet lunch tomorrow", "fresh salmon fillet"),
    ("30 kg pastaa koulukeittiöön yhdellä oppilaalla vakava pähkinäallergia huomiseksi", "pasta"),
    ("Tarvitsemme 5 litraa kermavaahtoa tänään klo 14 mennessä", "whipping cream"),
    ("We need 10 l of milk today by 14:00", "milk"),
    ("10 kg butter asap", "butter"),
    ("Tarvitsemme heti 10 kg juustoa huomiseksi", "cheese"),
    ("10 kg juustoa lounaaksi huomiseksi", "cheese"),
    ("30 kg pasta to the school kitchen tomorrow", "pasta"),
    ("10 kg pasta for 200 guests tomorrow", "pasta"),
    ("30 kg pastaa koululle huomiseksi", "pasta"),
])
def test_time_urgency_meal_and_destination_words_leave_the_item_name(text, what):
    ((parsed, _quantity, _unit),) = items_of(text)
    assert parsed == what


def test_untranslated_finnish_product_keeps_its_name_but_loses_the_destination():
    ((what, _q, _u),) = items_of("30 kg kvinoaa koulukeittiöön huomiseksi")
    assert what == "kvinoaa"


# --------------------------------------------------------------------------- pähkinä


@pytest.mark.parametrize("text", [
    "30 kg pastaa koulukeittiöön yhdellä oppilaalla vakava pähkinäallergia huomiseksi",
    "20 kg pastaa, ei pähkinää, huomiseksi",
    "20 kg pähkinätöntä pastaa huomiseksi",
    "20 kg pastaa ilman pähkinöitä huomiseksi",
])
def test_finnish_nut_means_peanut_and_tree_nut(text):
    result = parse_request(text, MORNING)
    (item,) = result.request.items
    assert item.what == "pasta"
    assert item.requirements == [AN, AP]


def test_peanut_tree_nut_and_general_nut_codes():
    (item,) = parse_request("20 kg pastaa ilman maapähkinää huomiseksi", MORNING).request.items
    assert item.requirements == [AP]
    (item,) = parse_request("20 kg pasta, no tree nuts, tomorrow", MORNING).request.items
    assert item.requirements == [AN]
    (item,) = parse_request("20 kg pasta, no nuts, tomorrow", MORNING).request.items
    assert item.requirements == [AN, AP]


def test_the_llm_cannot_drop_the_peanut_half_of_pahkina():
    def llm(_text, _today):
        return {"requestedDate": "2026-11-15", "assumptions": [], "clarificationQuestion": None,
                "items": [{"what": "pasta", "quantity": 20, "unit": "kg", "productId": None,
                           "requirements": [{"kind": "AVOID_ALLERGEN", "code": "AN"}], "preferences": []}]}

    result = parse_request("20 kg pastaa, ei pähkinää, huomiseksi", MORNING, interpreter=llm)
    assert result.mode == "LLM"
    assert result.request.items[0].requirements == [AN, AP]


# --------------------------------------------------------------------------- same-day deadlines


@pytest.mark.parametrize("text", [
    "Tarvitsemme 5 litraa kermavaahtoa tänään klo 14 mennessä",
    "We need 10 l of milk today by 14:00",
    "We need 10 l of milk by 2 pm today",
    "10 litraa maitoa tänään kello 14",
])
def test_today_by_a_clock_time_is_a_hard_deadline_today(text):
    request = parse_request(text, MORNING).request
    assert request.requestedDate == TODAY
    assert "Needed today (Sat 14 Nov) by 14:00: a later delivery does not meet this request" in request.assumptions
    assert not any(a.startswith("No delivery date given") for a in request.assumptions)


def test_minutes_are_kept():
    request = parse_request("10 l milk today klo 14.30", MORNING).request
    assert "Needed today (Sat 14 Nov) by 14:30: a later delivery does not meet this request" in request.assumptions


def test_a_clock_time_without_a_date_is_today_while_it_is_still_ahead():
    request = parse_request("We need 10 l of milk by 14:00", MORNING).request
    assert request.requestedDate == TODAY
    assert "No delivery date given: “by 14:00” read as Sat 14 Nov (today)" in request.assumptions
    later = parse_request("We need 10 l of milk by 14:00", AFTERNOON).request
    assert later.requestedDate == TOMORROW
    assert "Needed on Sun 15 Nov by 14:00: a later delivery does not meet this request" in later.assumptions


def test_a_clock_time_is_never_a_quantity_or_a_date():
    request = parse_request("5 kg juustoa 20.11. klo 9", MORNING).request
    assert request.requestedDate == date(2026, 11, 20)
    assert [(i.what, i.quantity, i.unit) for i in request.items] == [("cheese", Decimal(5), "kg")]
    assert "Needed on Fri 20 Nov by 09:00: a later delivery does not meet this request" in request.assumptions


def test_the_llm_cannot_move_or_drop_a_same_day_deadline():
    def llm(_text, _today):
        return {"requestedDate": "2026-11-16", "assumptions": ["Whipped cream"], "clarificationQuestion": None,
                "items": [{"what": "whipping cream", "quantity": 5, "unit": "l", "productId": None,
                           "requirements": [], "preferences": []}]}

    result = parse_request("Tarvitsemme 5 litraa kermavaahtoa tänään klo 14 mennessä", MORNING, interpreter=llm)
    assert result.mode == "LLM"
    assert result.request.requestedDate == TODAY
    assert "Needed today (Sat 14 Nov) by 14:00: a later delivery does not meet this request" in (
        result.request.assumptions
    )


# --------------------------------------------------------------------------- questions in the buyer's language


@pytest.mark.parametrize(("text", "question"), [
    ("We need something for dessert for 200 guests on Friday.",
     "Which product do you need for dessert for 200 guests, and how much (litres, kilograms, pieces or cases)?"),
    ("Tarvitsemme jotain jälkiruoaksi 200 vieraalle perjantaiksi",
     "Mitä tuotetta ja kuinka paljon tarvitsette jälkiruoaksi 200 vieraalle "
     "(litroina, kiloina, kappaleina tai laatikkoina)?"),
    ("We need something for dessert for 200 guests.",
     "Which product do you need for dessert for 200 guests, how much, and by when?"),
    ("Tarvitsemme jotain jälkiruoaksi",
     "Mitä tuotetta, kuinka paljon ja mihin mennessä tarvitsette jälkiruoaksi?"),
    ("Tarvitsemme jotain", "Mitä tarvitsette, kuinka paljon ja mihin mennessä?"),
    ("We need something tomorrow", "What do you need, and how much (litres, kilograms, pieces or cases)?"),
    ("Tarvitsemme voita huomiseksi", "Kuinka paljon voita tarvitsette (litroina, kiloina, kappaleina tai laatikkoina)?"),
    ("We need butter tomorrow", "How much butter do you need, in litres, kilograms, pieces or cases?"),
    ("Tarvitsemme 40 kg huomiseksi", "Mitä tuotetta tarvitsette 40 kg?"),
    ("We need 10 kg of something tomorrow", "Which product do you need 10 kg of?"),
])
def test_clarifying_question_is_grammatical_and_in_the_buyers_language(text, question):
    result = parse_request(text, MORNING)
    assert result.reasons == [ReasonCode.CLARIFICATION_NEEDED]
    assert result.clarificationQuestion == question
    assert result.clarificationQuestion.count("?") == 1


@pytest.mark.parametrize(("text", "start"), [
    ("300 annosta gluteenitonta pastaa huomiseksi", "Annos ei ole tilausyksikkö"),
    ("10 kg mysliä ilman sokeria maanantaiksi", "Mitä ”ilman sokeria” tarkoittaa"),
    ("100 litraa maidotonta maitoa maanantaiksi", "Maito on maitotuote"),
])
def test_finnish_safety_questions_are_finnish(text, start):
    result = parse_request(text, MORNING)
    assert ReasonCode.CLARIFICATION_NEEDED in result.reasons
    assert result.clarificationQuestion.startswith(start)
    assert result.clarificationQuestion.count("?") == 1


@pytest.mark.parametrize(("text", "finnish"), [
    ("Tarvitsen 40 kg voita huomiseksi", True),
    ("Tarvitsemme jotain jälkiruoaksi 200 vieraalle perjantaiksi", True),
    ("10 kg juustoa ja 5 kg lohta huomiseksi", True),
    ("We need 10 kg of Emmental cheese immediately", False),
    ("20 kg butter automatically by Monday", False),
    ("10 kg mozzarella", False),
    ("Hello there", False),
])
def test_language_detection(text, finnish):
    assert _is_finnish(text) is finnish


# --------------------------------------------------------------------------- T53: item-name leaks

LATE_MORNING = datetime(2026, 11, 14, 11, 0, tzinfo=HKI)


def test_judge_salmon_request_keeps_the_morning_as_the_deadline():
    result = parse_request(
        "We need 15 kg of fresh salmon fillet, organic and lactose-free, delivered tomorrow morning", LATE_MORNING
    )
    request = result.request
    (item,) = request.items
    assert item.what == "fresh salmon fillet"
    assert request.requestedDate == TOMORROW
    assert "Needed on Sun 15 Nov in the morning: a later delivery does not meet this request" in request.assumptions


def test_judge_butter_request_drops_the_booking_context():
    ((what, quantity, unit),) = items_of("50 kg salted butter, unexpected banquet booking", LATE_MORNING)
    assert (what, quantity, unit) == ("salted butter", Decimal(50), "kg")


def test_judge_school_request_is_a_meat_sauce_with_similar_accepted():
    result = parse_request(
        "40 kg gluteenitonta ja maidotonta jauhelihakastiketta tai vastaavaa koulun lounaalle, 300 oppilasta",
        LATE_MORNING,
    )
    (item,) = result.request.items
    assert item.what == "meat sauce"
    assert [(r.kind, r.code) for r in item.requirements] == [("CLAIM", "FREE_FROM_GLUTEN"), ("AVOID_ALLERGEN", "AM")]
    assert "Similar products accepted for meat sauce" in result.request.assumptions


@pytest.mark.parametrize(("text", "phrase"), [
    ("10 l milk tomorrow morning", "in the morning"),
    ("10 l milk by noon tomorrow", "by noon"),
    ("10 l milk before lunch tomorrow", "before lunch"),
    ("10 l milk tomorrow afternoon", "in the afternoon"),
    ("10 l milk tomorrow evening", "in the evening"),
    ("10 litraa maitoa huomenna aamulla", "in the morning"),
    ("10 litraa maitoa huomenna aamupäivällä", "by noon"),
    ("10 litraa maitoa huomenna iltapäivällä", "in the afternoon"),
    ("10 litraa maitoa huomenna illalla", "in the evening"),
    ("10 litraa maitoa huomenna ennen lounasta", "before lunch"),
])
def test_time_of_day_is_a_deadline_not_an_item_word(text, phrase):
    request = parse_request(text, LATE_MORNING).request
    (item,) = request.items
    assert item.what == "milk"
    assert request.requestedDate == TOMORROW
    assert f"Needed on Sun 15 Nov {phrase}: a later delivery does not meet this request" in request.assumptions


def test_a_clock_time_wins_over_a_time_of_day():
    request = parse_request("10 l milk tomorrow morning by 9:00", LATE_MORNING).request
    assert "Needed on Sun 15 Nov by 09:00: a later delivery does not meet this request" in request.assumptions


def test_a_greeting_is_not_a_deadline():
    request = parse_request("Good morning, we need 10 l of milk tomorrow", LATE_MORNING).request
    assert not any(a.startswith("Needed") for a in request.assumptions)
    assert [i.what for i in request.items] == ["milk"]


def test_a_time_of_day_without_a_date_is_today_while_it_is_still_ahead():
    request = parse_request("We need 10 l of milk this evening", LATE_MORNING).request
    assert request.requestedDate == TODAY
    assert "Needed today (Sat 14 Nov) in the evening: a later delivery does not meet this request" in (
        request.assumptions
    )


@pytest.mark.parametrize(("text", "what"), [
    ("10 kg cheese for the banquet tomorrow", "cheese"),
    ("10 kg cheese, last-minute party booking, tomorrow", "cheese"),
    ("10 kg cheese for a wedding reception tomorrow", "cheese"),
    ("10 kg juustoa juhliin huomiseksi", "cheese"),
    ("10 kg juustoa koulun lounaalle huomiseksi", "cheese"),
    ("10 kg Valio voita ravintolan keittiöön huomiseksi", "Valio butter"),
    ("40 kg pastaa koululle, 300 oppilasta, huomiseksi", "pasta"),
])
def test_occasion_and_context_end_the_item_name(text, what):
    ((parsed, _q, _u),) = items_of(text, LATE_MORNING)
    assert parsed == what


@pytest.mark.parametrize(("text", "what"), [
    ("10 kg cheese or similar tomorrow", "cheese"),
    ("10 kg cheese or equivalent tomorrow", "cheese"),
    ("10 kg cheese or an alternative tomorrow", "cheese"),
    ("10 kg juustoa tai vastaavaa huomiseksi", "cheese"),
    ("10 kg juustoa tai muuta vastaavaa huomiseksi", "cheese"),
])
def test_or_similar_is_an_assumption_not_part_of_the_item(text, what):
    request = parse_request(text, LATE_MORNING).request
    (item,) = request.items
    assert item.what == what
    assert f"Similar products accepted for {what}" in request.assumptions


@pytest.mark.parametrize(("text", "what"), [
    ("40 kg jauhelihakastiketta huomiseksi", "meat sauce"),
    ("5 kg juustokastiketta huomiseksi", "cheese sauce"),
    ("20 litraa kalakeittoa huomiseksi", "fish soup"),
    ("20 litraa lohikeittoa huomiseksi", "salmon soup"),
    ("10 kg lihapullia huomiseksi", "meatballs"),
    ("20 kg makaronilaatikkoa huomiseksi", "macaroni casserole"),
    ("5 litraa kermavaahtoa huomiseksi", "whipping cream"),
    ("10 kg jauhelihaa huomiseksi", "minced meat"),  # mince stays mince
    ("5 kg tuorejuustoa huomiseksi", "cream cheese"),
])
def test_finnish_compounds_take_their_meaning_from_the_last_part(text, what):
    ((parsed, _q, _u),) = items_of(text, LATE_MORNING)
    assert parsed == what
