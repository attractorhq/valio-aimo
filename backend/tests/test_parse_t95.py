"""T95: forms, tentative amounts, pack descriptions and Finnish compound heads are not invented items."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from test_agent_run import synthetic_api as synthetic_api
from test_proposals_gateway import _decl, syn_product

from app.agent.parse import _fallback, parse_request
from app.agent.plan import requested_type_match
from app.catalog.vocab import extract_requested_base, resolve_query_concepts
from app.contracts import Preference, ReasonCode
from app.policy import M2
from tests.synthetic.policy import product

NOW = datetime(2026, 11, 14, 15, tzinfo=ZoneInfo("Europe/Helsinki"))


def test_tentative_brunch_request_keeps_cheese_as_noun_and_does_not_invent_may_date():
    text = "some cheese for the weekend brunch, maybe 20 kilos, grated"
    draft = _fallback(text, NOW.date(), [])
    assert len(draft.items) == 1
    assert draft.items[0].what == "grated cheese"
    assert draft.items[0].quantity == Decimal(20)
    assert draft.explicit_date is None
    parsed = parse_request(text, NOW)
    assert parsed.reasons == [ReasonCode.CLARIFICATION_NEEDED]
    assert "grated cheese" in parsed.clarificationQuestion


@pytest.mark.parametrize("text,what,amount,pack", [
    ("2000 kg grated cabbage, 1 kg bags, delivered by Monday", "grated cabbage", "2000", "1 kg"),
    ("20 kg flour in 5 kg packs by Monday", "flour", "20", "5 kg"),
    ("20 kg flour, in 5-kg packs, by Monday", "flour", "20", "5 kg"),
    ("20 kg flour by Monday. In 5 kg packs.", "flour", "20", "5 kg"),
    ("20 kg jauhoja 5 kg pussit maanantaiksi", "flour", "20", "5 kg"),
    ("20 kg jauhoja, 5 kg:n pusseissa, maanantaiksi", "flour", "20", "5 kg"),
    ("10 kg sliced cheese, 500 g packs, by Monday", "sliced cheese", "10", "500 g"),
    ("20 kg cheese, 1 kg bags, grated, by Monday", "grated cheese", "20", "1 kg"),
])
def test_pack_sizes_modify_the_current_item_instead_of_creating_container_lines(text, what, amount, pack):
    parsed = parse_request(text, NOW)
    assert parsed.clarificationQuestion is None, parsed.clarificationQuestion
    (item,) = parsed.request.items
    assert (item.what, item.quantity, item.unit) == (what, Decimal(amount), "kg")
    assert Preference(kind="PACK_SIZE", value=pack) in item.preferences
    assert parsed.request.requestedDate == date(2026, 11, 16)


@pytest.mark.parametrize("text,what", [
    ("cheese, 20 kg, grated, by Monday", "grated cheese"),
    ("cheese 20 kg sliced by Monday", "sliced cheese"),
    ("juustoa 20 kiloa raasteena maanantaiksi", "grated cheese"),
    ("juustoa, 20 kg, viipaleina maanantaiksi", "sliced cheese"),
])
def test_a_detached_form_modifies_the_product_noun(text, what):
    parsed = parse_request(text, NOW)
    assert parsed.clarificationQuestion is None
    assert parsed.request.items[0].what == what


@pytest.mark.parametrize("text", [
    "20 kg grated by Monday", "20 kg sliced by Monday", "20 kg raasteena maanantaiksi",
    "5 kg flour bags by Monday, maybe", "some cheese, maybe 20 kg, grated",
    "noin 20 kg juustoa maanantaiksi", "ehkä 20 kg juustoa maanantaiksi",
    "20 kg jotain maanantaiksi", "approximately 20 kg flour by Monday",
    "1 kg bags of flour by Monday", "5 kg pussit maanantaiksi",
])
def test_hedges_form_only_and_pack_only_requests_require_clarification(text):
    parsed = parse_request(text, NOW)
    assert ReasonCode.CLARIFICATION_NEEDED in parsed.reasons
    assert parsed.clarificationQuestion.count("?") == 1


@pytest.mark.parametrize("phrase,expected", [
    ("20.11.", date(2026, 11, 20)), ("20 Nov", date(2026, 11, 20)),
    ("November 20", date(2026, 11, 20)), ("Friday 20th", date(2026, 11, 20)),
    ("20 May", date(2027, 5, 20)), ("May 20", date(2027, 5, 20)),
])
def test_date_patterns_remain_dates_not_product_tokens(phrase, expected):
    parsed = parse_request(f"20 kg cheese by {phrase}", NOW)
    assert parsed.clarificationQuestion is None
    assert parsed.request.requestedDate == expected
    assert parsed.request.items[0].what == "cheese"


@pytest.mark.parametrize("text,what,base", [
    ("Tarvitsemme 30 kg maidotonta ja gluteenitonta kaurajugurttia maanantaiksi.", "oat yogurt", "oat"),
    ("30 kg kaurajogurttia maanantaiksi", "oat yoghurt", "oat"),
    ("30 kg soijajugurttia maanantaiksi", "soy yogurt", "soy"),
    ("30 kg mantelijugurttia maanantaiksi", "almond yogurt", "almond"),
    ("30 kg kookosjugurttia maanantaiksi", "coconut yogurt", "coconut"),
    ("30 kg riisijauhoa maanantaiksi", "rice flour", "rice"),
])
def test_compounds_translate_by_head_and_preserve_hard_base(text, what, base):
    parsed = parse_request(text, NOW)
    assert parsed.clarificationQuestion is None
    (item,) = parsed.request.items
    assert item.what == what
    assert extract_requested_base(item.what).form_id == base
    assert resolve_query_concepts(item.what)[0].concept_id == ("flour" if base == "rice" else "yoghurt")
    if "maidotonta" in text:
        assert {r.code for r in item.requirements} == {"AM", "FREE_FROM_GLUTEN"}
    other = product().model_copy(update={"name": "Synthetic soy yogurt", "category": "10413"})
    if base == "oat":
        assert not requested_type_match(other, item).exact
        exact = other.model_copy(update={"name": "Synthetic oat yogurt"})
        assert requested_type_match(exact, item).exact


@pytest.mark.parametrize("text,model_items", [
    ("20 kg grated by Monday", [{"what": "grated cheese", "quantity": "20", "unit": "kg"}]),
    ("grated please by Monday", [{"what": "grated cheese", "quantity": "20", "unit": "kg"}]),
    ("maybe 20 kg cheese by Monday", [{"what": "cheese", "quantity": "20", "unit": "kg"}]),
    ("20 kg cheese grated by Monday", [{"what": "grated cabbage", "quantity": "20", "unit": "kg"}]),
    ("2000 kg grated cabbage, 1 kg bags, delivered by Monday", [
        {"what": "grated cabbage", "quantity": "2000", "unit": "kg"},
        {"what": "bags", "quantity": "1", "unit": "pcs"},
    ]),
    ("30 kg kaurajugurttia maanantaiksi", [{"what": "yogurt", "quantity": "30", "unit": "kg"}]),
    ("30 kg kaurajugurttia maanantaiksi", [{"what": "soy yogurt", "quantity": "30", "unit": "kg"}]),
])
def test_model_cannot_bypass_form_hedge_pack_or_base_guards(text, model_items):
    def model(_text, _today):
        return {"requestedDate": "2026-11-16", "items": model_items}

    parsed = parse_request(text, NOW, interpreter=model)
    if "maybe" in text or text in ("20 kg grated by Monday", "grated please by Monday"):
        assert parsed.clarificationQuestion is not None
    else:
        assert parsed.mode == "FALLBACK"
        assert parsed.fallbackReason == "invalid_output"
        assert len(parsed.request.items) == 1
        assert parsed.request.items[0].what != "grated cabbage" or "cabbage" in text


def test_weekday_and_day_number_conflict_requires_clarification():
    parsed = parse_request("20 kg cheese by Monday 20th", NOW)
    assert parsed.clarificationQuestion is not None
    assert ReasonCode.CLARIFICATION_NEEDED in parsed.reasons


@pytest.mark.parametrize("name,exact", [
    ("Synthetic oat yogurt", True),
    ("Synthetic soy-free oat yogurt", True),
    ("Synthetic soijaton kaurajogurtti", True),
    ("Synthetic oat and soy yogurt", False),
    ("Synthetic soy yogurt", False),
])
def test_hard_base_checks_distinguish_mixtures_and_free_from_names(name, exact):
    request = parse_request("30 kg kaurajugurttia maanantaiksi", NOW).request
    candidate = product().model_copy(update={"name": name, "category": "10413"})
    assert requested_type_match(candidate, request.items[0]).exact is exact


@pytest.mark.parametrize("text", ["some cheese by Monday", "Tarvitsemme jotain maanantaiksi"])
def test_model_cannot_invent_an_exact_purchase_for_a_vague_unquantified_request(text):
    def model(_text, _today):
        return {"requestedDate": "2026-11-16", "items": [{"what": "cheese", "quantity": "20", "unit": "kg"}]}

    parsed = parse_request(text, NOW, interpreter=model)
    assert parsed.clarificationQuestion is not None
    assert parsed.reasons == [ReasonCode.CLARIFICATION_NEEDED]


@pytest.mark.parametrize("text,expected", [
    ("some cheese for the weekend brunch, maybe 20 kilos, grated", None),
    ("2000 kg grated cabbage, 1 kg bags, delivered by Monday", "grated cabbage"),
    ("Tarvitsemme 30 kg maidotonta ja gluteenitonta kaurajugurttia maanantaiksi.", "oat yogurt"),
])
def test_reported_requests_through_public_agent_endpoint_with_synthetic_catalog(synthetic_api, text, expected):
    client, state = synthetic_api
    for code, name, category in (
        ("SYN-CABBAGE", "Synthetic grated cabbage", "17400"),
        ("SYN-OAT-YOGURT", "Synthetic oat yogurt", "10413"),
        ("SYN-SOY-YOGURT", "Synthetic soy yogurt", "10413"),
    ):
        candidate = syn_product(code, content=("1", "kg"), claims=("FREE_FROM_GLUTEN",), allergens=(_decl("X99"),))
        candidate = candidate.model_copy(update={"name": name, "names": {}, "category": category})
        state.gateway.products[code] = candidate
        state.catalog.append(candidate)
        state.gateway.provider.prices[code] = (1.00, 1.00, "KG")
    response = client.post("/v0.1/agent/run", json={"customerId": M2.customerId, "requestText": text})
    assert response.status_code == 200, response.text
    result = response.json()
    if expected is None:
        assert result["clarificationQuestion"]
        assert result["proposal"] is None
    else:
        assert result["clarificationQuestion"] is None
        assert len(result["request"]["items"]) == 1
        assert result["request"]["items"][0]["what"] == expected
        assert len(result["plan"]["items"]) == 1
        assert result["proposal"]["lines"][0]["productId"] == (
            "SYN-CABBAGE" if expected == "grated cabbage" else "SYN-OAT-YOGURT"
        )
