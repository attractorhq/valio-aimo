"""T108: the compact agent view explains every reason, carries a one-line summary, and NO_CANDIDATE results
carry the closest matches and the earliest-date retry (same data as the UI), over REST and MCP."""

import json
from decimal import Decimal

from test_agent_run import B_TEXT, MONDAY
from test_agent_run import synthetic_api as synthetic_api

from app.contracts import ContentPerSalesUnit
from app.policy import M1, M2


def compact(client, customer, text, lookup_date=None):
    body = {"customerId": customer.customerId, "requestText": text, "lookupDate": lookup_date}
    response = client.post("/v0.1/agent/run?view=compact", json=body)
    assert response.status_code == 200, response.text
    return response


def test_scenario_b_compact_explains_over_auto_limit_and_stays_small(synthetic_api):
    client, _ = synthetic_api
    response = compact(client, M2, B_TEXT)
    body = response.json()
    print(json.dumps(body, indent=1, ensure_ascii=False))
    assert len(response.content) < 5000
    assert [r["code"] for r in body["reasonTexts"]] == body["reasons"] == ["OVER_AUTO_LIMIT"]
    (reason,) = body["reasonTexts"]
    assert reason["effect"] == "ASK"
    assert reason["text"] == "€506.16 excl. VAT is above your €500.00 auto-approve limit."
    assert body["summary"].startswith("ASK: €506.16 excl. VAT")
    assert reason["text"] in body["summary"]
    assert "\n" not in body["summary"]
    assert body["noOrder"] is None
    # T112: the summary names the chosen product and its pack, not just "1 line".
    first = body["lines"][0]
    assert f"90 kg · {' '.join(first['name'].split())} (18 × 5 kg) and 1 more" in body["summary"]
    assert "2 lines" not in body["summary"]


def test_no_candidate_compact_carries_earliest_date_retry(synthetic_api):
    client, _ = synthetic_api
    text = "I need 10 litres of lactose-free milk today."
    body = compact(client, M1, text).json()
    print(json.dumps(body, indent=1, ensure_ascii=False))
    assert body["proposalId"] is None and body["decision"] is None
    assert "NO_CANDIDATE" in body["reasons"]
    assert all(r["text"] and "…" not in r["text"] for r in body["reasonTexts"])
    no_order = body["noOrder"]
    assert no_order["earliestDeliverableDate"] == MONDAY.isoformat()
    retry = no_order["retry"]
    assert retry["body"] == {"customerId": M1.customerId, "requestText": text, "lookupDate": MONDAY.isoformat()}
    assert "Mon 16 Nov" in retry["hint"]
    again = client.post(retry["path"], json=retry["body"]).json()
    assert again["decision"] == "ASK"
    assert "DATE_LATER" in again["reasons"]


def test_no_candidate_compact_lists_type_checked_closest_matches(synthetic_api):
    client, state = synthetic_api
    source = state.catalog[0]
    safe = source.model_copy(update={
        "name": "Synthetic flounder",
        "names": {"en": "Synthetic flounder"},
        "claims": ["FREE_FROM_GLUTEN"],
        "contentPerSalesUnit": ContentPerSalesUnit(value=Decimal(1), unit="kg", level="DECLARED"),
    })
    unsafe = safe.model_copy(update={"productId": "SYN-UNPROVEN", "claims": []})
    state.catalog = [safe, unsafe]
    body = compact(client, M1, "I need 5 kg of imaginary gluten-free flounder today.").json()
    assert body["proposalId"] is None
    (item,) = body["noOrder"]["items"]
    assert item["closestMatches"] == [
        {"productId": safe.productId, "name": safe.name, "lacks": "imaginary not stated"},
    ]
    assert body["noOrder"]["earliestDeliverableDate"] is None and body["noOrder"]["retry"] is None
    (reason,) = [r for r in body["reasonTexts"] if r["code"] == "NO_CANDIDATE"]
    assert (
        f"Closest matches (not ordered): {safe.name} ({safe.productId}: imaginary not stated)" in reason["text"]
    )
    assert body["summary"].startswith("No order:") and "\n" not in body["summary"]


def test_clarification_compact_reason_text_is_the_question(synthetic_api):
    client, _ = synthetic_api
    body = compact(client, M1, "We need lactose-free milk by Monday.").json()
    (reason,) = body["reasonTexts"]
    assert reason == {"code": "CLARIFICATION_NEEDED", "effect": "NO_ORDER", "text": body["clarificationQuestion"]}
    assert body["clarificationQuestion"] in body["summary"]
    assert body["noOrder"] is None


def test_compact_view_is_advertised_in_openapi(synthetic_api):
    client, _ = synthetic_api
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/v0.1/agent/run"]["post"]
    assert "view=compact" in operation["description"]
    view = next(param for param in operation["parameters"] if param["name"] == "view")
    assert "recommended for agents" in view["description"]
    ok = operation["responses"]["200"]
    assert "view=compact" in ok["description"]
    examples = ok["content"]["application/json"]["examples"]
    assert {"compact_ask", "compact_no_candidate"} <= set(examples)
    assert examples["compact_no_candidate"]["value"]["noOrder"]["retry"]["body"]["lookupDate"]
    props = schema["components"]["schemas"]["CompactAgentRunResult"]["properties"]
    assert {"summary", "reasonTexts", "noOrder"} <= set(props)
    proposal_ops = [op for path, ops in schema["paths"].items() if path.startswith("/v0.1/proposals")
                    for op in ops.values()]
    assert any("view=compact" in (op.get("description") or "") for op in proposal_ops)
