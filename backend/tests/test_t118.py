"""T118 (judge run 20261115-064402): wording that agrees with the facts. Synthetic records only (patch P7)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from test_agent_run import synthetic_api  # noqa: F401  (pytest fixture)

from app.agent.parse import parse_request
from app.agent.run import AgentRunResult
from app.policy import M2

NOW = datetime(2026, 11, 15, 7, 0, tzinfo=ZoneInfo("Europe/Helsinki"))


def _question(text: str, **kw) -> str | None:
    return parse_request(text, NOW, **kw).clarificationQuestion


def test_the_quantity_question_asks_in_the_units_the_product_is_sold_in():
    assert _question("some cheese for the buffet by Monday") == (
        "How much cheese do you need, in kilograms, pieces or cases?")
    assert _question("lactose-free milk by Monday") == "How much milk do you need, in litres, pieces or cases?"
    assert "litres" not in _question("cream cheese by Monday")
    assert _question("juustoa maanantaiksi") == "Kuinka paljon juustoa tarvitsette (kiloina, kappaleina tai laatikkoina)?"


def test_a_contradiction_is_named_in_finnish_and_on_the_llm_path():
    assert _question("10 kg vegaanista voita lehmänmaidosta maanantaiksi").startswith(
        "Maidosta tehty tuote on maitotuote")

    def llm(_text, _today):  # the LLM keeps "vegan butter" and drops "made from cow milk" from the item
        return {"requestedDate": "2026-11-16", "items": [{
            "what": "vegan butter", "quantity": 10, "unit": "kg", "productId": None,
            "requirements": [{"kind": "CLAIM", "code": "VEGAN"}], "preferences": []}],
            "assumptions": [], "clarificationQuestion": None}

    result = parse_request("10 kg vegan butter made from cow milk by Monday", NOW, interpreter=llm)
    assert result.mode == "LLM"
    assert result.clarificationQuestion.startswith("Vegan and made from cow's milk cannot both hold.")
    assert _question("10 kg butter made from cow milk by Monday") is None  # no requirement, no contradiction


def test_cannot_fulfil_lists_the_product_that_holds_the_small_quantity_first(synthetic_api):  # noqa: F811
    client, _ = synthetic_api
    resp = client.post("/v0.1/agent/run", json={
        "customerId": M2.customerId, "requestText": "We need 5000 kg of gluten-free macaroni by Monday."})
    result = AgentRunResult.model_validate(resp.json())
    (item,) = result.steps[-1].noOrder.items
    assert item.why.startswith("Cannot fulfil: only ")
    holders = [m for m in item.closestMatches if (m.lacks or "").startswith("only ")]
    assert holders and item.closestMatches[0] == holders[0]  # the products that hold stock lead the list
    assert all(m.lacks.endswith("available for Mon 16 Nov") for m in holders)
