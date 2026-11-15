"""T120: allergy lists are requirements, items keep their own dates, vague requests get a readable question."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.agent.parse import parse_request
from app.contracts import ReasonCode, RequestItem, StructuredRequest
from app.policy import M1, decide
from tests.synthetic.policy import NOW, line

SUNDAY = datetime(2026, 11, 15, 10, 0, tzinfo=ZoneInfo("Europe/Helsinki"))
TODAY, TOMORROW = date(2026, 11, 15), date(2026, 11, 16)


def codes(item):
    return sorted(r.code for r in item.requirements)


# --------------------------------------------------------------------------- 1. allergy lists

def test_allergy_list_becomes_requirements_and_leaves_the_product_query():
    result = parse_request(
        "School lunch on Wednesday 18 Nov: 40 kg fish fingers for a kitchen with strict egg, milk and peanut "
        "allergies.", SUNDAY)
    (item,) = result.request.items
    assert item.what == "fish fingers"
    assert codes(item) == ["AE", "AM", "AP"]
    assert result.request.requestedDate == date(2026, 11, 18)
    assert "Applied to every item: egg-free, milk-free, peanut-free" in result.request.assumptions


@pytest.mark.parametrize(("text", "what", "expected"), [
    ("40 kg fish fingers by Wednesday, the children are allergic to egg and milk", "fish fingers", ["AE", "AM"]),
    ("20 kg rice, two pupils are allergic to sesame and mustard, by Monday", "rice", ["AS", "BM"]),
    ("40 kg fish fingers and 10 kg peas by Wednesday, strict egg, milk and peanut allergies", "fish fingers",
     ["AE", "AM", "AP"]),
])
def test_allergy_phrasings(text, what, expected):
    result = parse_request(text, SUNDAY)
    assert result.request.items[0].what == what
    for item in result.request.items:
        assert codes(item) == expected


def test_a_single_allergy_clause_still_reads_as_before():
    result = parse_request("We need 200 kg penne plus 50 litres of oat drink, one pupil has a nut allergy", SUNDAY)
    assert [i.what for i in result.request.items] == ["penne", "oat drink"]
    assert all(codes(i) == ["AN", "AP"] for i in result.request.items)


# --------------------------------------------------------------------------- 2. per-item dates

@pytest.mark.parametrize("text", [
    "Tarvitsen 40 kg voita tänään ja 12 kg raastettua emmentalia huomiseksi",
    "40 kg butter today and 12 kg grated emmental tomorrow",
])
def test_each_item_keeps_its_own_date(text):
    request = parse_request(text, SUNDAY).request
    assert request.requestedDate == TODAY  # the earliest deadline
    butter, emmental = request.items
    assert (butter.requestedDate, emmental.requestedDate) == (None, TOMORROW)
    assert (request.deadline_for("I1"), request.deadline_for("I2")) == (TODAY, TOMORROW)
    assert "Each item has its own delivery date: butter Sun 15 Nov, grated Emmental Mon 16 Nov" in request.assumptions


def test_one_date_for_the_whole_request_is_unchanged():
    request = parse_request("40 kg butter and 12 kg grated emmental by Monday", SUNDAY).request
    assert request.requestedDate == TOMORROW
    assert all(item.requestedDate is None for item in request.items)
    a = parse_request("I need 1,000 litres of lactose-free milk by Monday.", SUNDAY).request
    assert a.requestedDate == TOMORROW and a.items[0].requestedDate is None


def test_item_date_cannot_be_before_the_request_date():
    item = RequestItem(itemId="I1", what="butter", quantity=Decimal(1), unit="kg", requestedDate=TODAY)
    with pytest.raises(ValidationError):
        StructuredRequest(requestText="x", requestedDate=TOMORROW, items=[item])


def test_policy_judges_each_line_against_its_own_items_date():
    request = StructuredRequest(
        requestText="40 kg butter today and 12 kg grated emmental tomorrow",
        requestedDate=TODAY,
        items=[
            RequestItem(itemId="I1", what="butter", quantity=Decimal(10), unit="l"),
            RequestItem(itemId="I2", what="emmental", quantity=Decimal(10), unit="l", requestedDate=TOMORROW),
        ],
    )
    lines = [line("L1", "I1", available_date=TOMORROW), line("L2", "I2", available_date=TOMORROW)]
    d = decide(mandate=M1, request=request, lines=lines, now=NOW)
    assert d.outcome == "ASK" and d.reasons == (ReasonCode.DATE_LATER,)
    later = {f.line_id for f in d.findings if f.code == "DATE_LATER"}
    assert later == {"L1"}  # the emmental is on time for its own date; only the butter is late

    on_time = [line("L1", "I1", available_date=TODAY), line("L2", "I2", available_date=TOMORROW)]
    assert decide(mandate=M1, request=request, lines=on_time, now=NOW).reasons == ()


# --------------------------------------------------------------------------- 3. vague requests

@pytest.mark.parametrize(("text", "question"), [
    ("we need stuff for a breakfast buffet for 200 people next week",
     "Which product do you need for a breakfast buffet for 200 people, how much, and by when?"),
    ("we need milk next week", "How much milk do you need, in litres, pieces or cases?"),
])
def test_vague_requests_get_a_readable_question(text, question):
    result = parse_request(text, SUNDAY)
    assert result.request is None
    assert result.clarificationQuestion == question
    assert "week" not in result.clarificationQuestion.split("for 200 people")[0]
