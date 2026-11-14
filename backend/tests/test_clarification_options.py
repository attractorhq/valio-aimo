"""T97: a vague request gets 2–3 complete requests the buyer can run with one tap; every option keeps the buyer's
requirements and parses without another question."""

from datetime import datetime
from zoneinfo import ZoneInfo

from test_agent_run import synthetic_api  # noqa: F401  (pytest fixture)

from app.agent.parse import parse_request
from app.agent.run import AgentRunResult
from app.agent.suggest import clarification_options
from app.contracts import ReasonCode
from app.policy import M1

NOW = datetime(2026, 11, 14, 22, 0, tzinfo=ZoneInfo("Europe/Helsinki"))


def _texts(text: str) -> list[str]:
    return [o.requestText for o in clarification_options(text, NOW)]


def test_an_occasion_without_a_product_suggests_what_kitchens_buy_for_it():
    assert _texts("something for the weekend brunch") == [
        "10 kg sliced cheese for the weekend brunch",
        "10 kg yoghurt for the weekend brunch",
        "10 kg butter for the weekend brunch",
    ]


def test_a_named_product_without_an_amount_suggests_amounts_and_keeps_the_occasion():
    assert _texts("some cheese for the weekend buffet, the usual amount") == [
        "5 kg cheese for the weekend buffet", "10 kg cheese for the weekend buffet", "20 kg cheese for the weekend buffet",
    ]


def test_a_stated_amount_and_form_come_first():
    assert _texts("some cheese for the weekend brunch, maybe 20 kilos, grated")[0] == (
        "20 kg grated cheese for the weekend brunch"
    )


def test_requirements_and_the_buyers_language_are_kept():
    assert _texts("lactose-free milk by Monday")[0] == "20 l lactose-free milk by Monday"
    assert _texts("laktoositonta maitoa maanantaiksi")[0] == "20 l laktoositonta maitoa maanantaiksi"
    assert _texts("jotain viikonlopun brunssille")[0] == "10 kg juustoviipaleita viikonlopun brunssille"


def test_every_option_parses_cleanly_with_the_same_requirements():
    for text in ("lactose-free milk by Monday", "gluten-free and milk-free cheese for Friday",
                 "something for the weekend brunch", "kermaa huomiseksi"):
        options = clarification_options(text, NOW)
        assert 2 <= len(options) <= 3, text
        for option in options:
            parsed = parse_request(option.requestText, NOW)
            assert parsed.clarificationQuestion is None and parsed.request is not None, option
    gluten = clarification_options("gluten-free and milk-free cheese for Friday", NOW)
    for option in gluten:
        codes = {r.code for r in parse_request(option.requestText, NOW).request.items[0].requirements}
        assert {"FREE_FROM_GLUTEN", "AM"} <= codes


def test_the_agent_run_returns_the_options_with_the_question(synthetic_api):  # noqa: F811
    client, _ = synthetic_api
    resp = client.post("/v0.1/agent/run", json={"customerId": M1.customerId,
                                                 "requestText": "something for the weekend brunch"})
    assert resp.status_code == 200, resp.text
    result = AgentRunResult.model_validate(resp.json())
    assert result.reasons == [ReasonCode.CLARIFICATION_NEEDED] and result.proposal is None
    assert [o.label for o in result.clarificationOptions] == ["10 kg sliced cheese", "10 kg yoghurt", "10 kg butter"]
    assert result.steps[0].clarificationOptions == result.clarificationOptions


def test_lead_in_words_are_dropped_so_the_amount_reads_naturally():
    assert _texts("We need lactose-free milk by Monday.")[0] == "20 l lactose-free milk by Monday."
    assert _texts("Tarvitaan laktoositonta maitoa maanantaiksi.")[0] == "20 l laktoositonta maitoa maanantaiksi."
    assert _texts("Please send cream tomorrow")[0] == "10 l cream tomorrow"


def test_the_streamed_run_carries_the_options_too(synthetic_api):  # noqa: F811
    import json

    client, _ = synthetic_api
    resp = client.post("/v0.1/agent/run", headers={"Accept": "application/x-ndjson"},
                       json={"customerId": M1.customerId, "requestText": "something for the weekend brunch"})
    assert resp.status_code == 200, resp.text
    events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    final = next(e for e in reversed(events) if "clarificationOptions" in e or "result" in e)
    payload = final.get("result", final)
    assert [o["label"] for o in payload["clarificationOptions"]] == ["10 kg sliced cheese", "10 kg yoghurt", "10 kg butter"]
