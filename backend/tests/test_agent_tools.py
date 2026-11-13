from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from conftest import BUYER
from openai import APIConnectionError

from app.agent.runner import run_agent
from app.agent.tools import TOOL_MODELS, execute_tool
from app.auth import DEMO_IDENTITIES


def test_allowlist_and_input_validation(context):
    _, provider, factory, _ = context
    assert set(TOOL_MODELS) == {
        "search_products",
        "get_product_details",
        "check_offer",
        "create_order_proposal",
        "get_proposal_status",
    }
    with factory() as db:
        actor = DEMO_IDENTITIES["demo-restaurant-a"]
        assert execute_tool("commit_order", "{}", db, provider, actor)["error"] == "TOOL_NOT_ALLOWED"
        assert (
            execute_tool("search_products", '{"limit":999}', db, provider, actor)["error"]
            == "INVALID_TOOL_INPUT"
        )
        assert (
            execute_tool("search_products", '{"url":"https://example.com"}', db, provider, actor)["error"]
            == "INVALID_TOOL_INPUT"
        )


def test_missing_key_guided_fallback(context, monkeypatch):
    client, _, _, settings = context
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    r = client.post("/api/v1/agent/chat", headers=BUYER, json={"message": "Order milk"})
    assert r.status_code == 200
    assert r.json()["mode"] == "guided"


def test_provider_down_guided_fallback(context):
    _, provider, factory, settings = context
    settings.deepseek_api_key = "not-a-real-key"
    with factory() as db, patch("app.agent.runner.OpenAI") as mock:
        mock.return_value.__enter__.return_value.chat.completions.create.side_effect = APIConnectionError(
            request=httpx.Request("POST", "https://api.deepseek.com")
        )
        result = run_agent(settings, db, provider, DEMO_IDENTITIES["demo-restaurant-a"], "Milk")
        assert result.mode == "guided"
        assert "not-a-real-key" not in result.message


def test_tool_loop_is_bounded(context):
    _, provider, factory, settings = context
    settings.deepseek_api_key = "not-a-real-key"
    call = MagicMock()
    call.id = "call_1"
    call.function.name = "search_products"
    call.function.arguments = '{"query":"milk"}'
    call.model_dump.return_value = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "search_products", "arguments": '{"query":"milk"}'},
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))]
    )
    with factory() as db, patch("app.agent.runner.OpenAI") as mock:
        create = mock.return_value.__enter__.return_value.chat.completions.create
        create.return_value = response
        result = run_agent(settings, db, provider, DEMO_IDENTITIES["demo-restaurant-a"], "Milk")
        assert create.call_count == 6
        assert result.mode == "guided"
