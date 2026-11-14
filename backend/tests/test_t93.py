"""T93: 422 on unknown requirement codes with alias hints; Valio-side per-customer audit across sessions."""

import pytest
from fastapi.testclient import TestClient as RawTestClient
from synthetic.demo_client import DemoTestClient as TestClient
from test_agent_run import A_TEXT, B_TEXT, NOW, SyntheticAgentProvider, _build_synthetic_catalog

from app.api import state as state_module
from app.api.state import GatewayState, SandboxRegistry, fresh_sandbox
from app.contracts.requirements import VALID_ALLERGEN_CODES, VALID_CLAIM_CODES
from app.gateway.proposals import ProposalGateway
from app.main import app
from app.policy import M1, M2
from app.trust.labels import (
    CLAIM_DISPLAY_NAMES,
    EU14_ALLERGEN_CODES,
    LABEL_DISPLAY_NAMES,
    NON_EU14_ALLERGEN_CODES,
)

STORAGE_TEMP_LABELS = {"warmTemperature", "coolTemperature", "freezeTemperature"}
HOTEL_AUTH = {"Authorization": f"Bearer demo-agent-{M1.customerId}"}
SCHOOL_AUTH = {"Authorization": f"Bearer demo-agent-{M2.customerId}"}
ADMIN_AUTH = {"X-Demo-Admin-Key": "demo-admin"}


@pytest.fixture
def sandboxes(monkeypatch):
    products = _build_synthetic_catalog()
    resources = GatewayState(
        ProposalGateway(products=products, provider=SyntheticAgentProvider()),
        list(products.values()),
        "RECORDED",
        clock=lambda: NOW,
    )
    registry = SandboxRegistry(lambda sandbox_id: fresh_sandbox(resources, sandbox_id))
    monkeypatch.setattr(state_module, "_state", resources)
    monkeypatch.setattr(state_module, "_registry", registry)
    app.dependency_overrides.clear()
    yield registry, resources
    app.dependency_overrides.clear()


def test_contract_requirement_code_sets_stay_in_sync_with_trust_labels():
    expected_claims = set(CLAIM_DISPLAY_NAMES) | (set(LABEL_DISPLAY_NAMES) - STORAGE_TEMP_LABELS)
    expected_allergens = set(EU14_ALLERGEN_CODES) | set(NON_EU14_ALLERGEN_CODES)
    assert set(VALID_CLAIM_CODES) == expected_claims
    assert set(VALID_ALLERGEN_CODES) == expected_allergens


@pytest.mark.parametrize(
    ("requirement", "expected_hint", "expected_valid_token"),
    [
        (
            {"kind": "CLAIM", "code": "LACTOSE_FREE"},
            "did you mean FREE_FROM_LACTOSE?",
            "FREE_FROM_GLUTEN",
        ),
        (
            {"kind": "CLAIM", "code": "GLUTEN_FREE"},
            "did you mean FREE_FROM_GLUTEN?",
            "FREE_FROM_LACTOSE",
        ),
        (
            {"kind": "CLAIM", "code": "MSC"},
            "did you mean MARINE_STEWARDSHIP_COUNCIL_LABEL?",
            "EU_ORGANIC_FARMING",
        ),
        (
            {"kind": "CLAIM", "code": "LUOMU"},
            "did you mean EU_ORGANIC_FARMING?",
            "VEGAN",
        ),
        (
            {"kind": "CLAIM", "code": "MILK_FREE"},
            "did you mean kind='AVOID_ALLERGEN' with code='AM' (milk)?",
            "FREE_FROM_LACTOSE",
        ),
        (
            {"kind": "AVOID_ALLERGEN", "code": "MILK"},
            "did you mean AM?",
            "AW",
        ),
        (
            {"kind": "AVOID_ALLERGEN", "code": "LACTOSE"},
            "did you mean ML (or kind='CLAIM' with code='FREE_FROM_LACTOSE')?",
            "AM",
        ),
        (
            {"kind": "AVOID_ALLERGEN", "code": "FREE_FROM_LACTOSE"},
            "did you mean kind='CLAIM' with code='FREE_FROM_LACTOSE'?",
            "AM",
        ),
    ],
)
def test_search_unknown_requirement_code_returns_422_with_hint_and_valid_codes(
    sandboxes, requirement, expected_hint, expected_valid_token
):
    with TestClient(app) as client:
        res = client.post(
            "/v0.1/products/search",
            json={"query": "cream", "requirements": [requirement]},
        )
    assert res.status_code == 422, res.text
    err = res.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert expected_hint in err["message"]
    assert expected_valid_token in err["message"]
    assert any(expected_hint in d["message"] and expected_valid_token in d["message"] for d in err["details"])


def test_search_unknown_forbidden_allergen_and_mcp_unknown_requirement_return_validation_error(sandboxes):
    with TestClient(app) as client:
        res = client.post(
            "/v0.1/products/search",
            json={"query": "milk", "forbiddenAllergens": ["MILK"]},
        )
        assert res.status_code == 422
        assert "did you mean AM?" in res.json()["error"]["message"]

        mcp_res = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "searchProducts",
                    "arguments": {
                        "query": "cream",
                        "requirements": [{"kind": "CLAIM", "code": "LACTOSE_FREE"}],
                    },
                },
            },
        ).json()["result"]
        assert mcp_res["isError"] is True
        assert mcp_res["structuredContent"]["error"]["code"] == "VALIDATION_ERROR"
        assert "did you mean FREE_FROM_LACTOSE?" in mcp_res["structuredContent"]["error"]["message"]


def test_scope_all_aggregates_browser_sessions_per_customer_and_verifies_each_session_separately(sandboxes):
    registry, _ = sandboxes
    browser_headers = {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"}
    with (
        TestClient(app, headers=browser_headers) as browser_one,
        TestClient(app, headers=browser_headers) as browser_two,
        RawTestClient(app) as external,
    ):
        # Establish two separate browser cookie sessions and create orders in each.
        browser_one.get("/health")
        cookie_one = browser_one.cookies["voa_sandbox"]
        prop_one = browser_one.post(
            "/v0.1/agent/run", json={"customerId": M1.customerId, "requestText": A_TEXT}
        ).json()["proposal"]

        browser_two.get("/health")
        cookie_two = browser_two.cookies["voa_sandbox"]
        assert cookie_one != cookie_two
        prop_two = browser_two.post(
            "/v0.1/agent/run", json={"customerId": M1.customerId, "requestText": A_TEXT}
        ).json()["proposal"]
        school_prop = browser_two.post(
            "/v0.1/agent/run", json={"customerId": M2.customerId, "requestText": B_TEXT}
        ).json()["proposal"]

        # Default scope=session outside the browser sees only the empty `shared` session.
        empty_trail = external.get(
            "/v0.1/audit", params={"customerId": M1.customerId}, headers=HOTEL_AUTH
        )
        assert empty_trail.status_code == 200
        assert empty_trail.json()["entries"] == []
        empty_ver = external.get(
            "/v0.1/audit/verify", params={"customerId": M1.customerId}, headers=HOTEL_AUTH
        )
        assert empty_ver.status_code == 200
        assert empty_ver.json()["chainLength"] == 0

        # Same auth boundaries on scope=all: missing key -> 401, wrong customer key -> 403.
        assert (
            external.get(
                "/v0.1/audit", params={"customerId": M1.customerId, "scope": "all"}
            ).status_code
            == 401
        )
        assert (
            external.get(
                "/v0.1/audit",
                params={"customerId": M1.customerId, "scope": "all"},
                headers=SCHOOL_AUTH,
            ).status_code
            == 403
        )

        # scope=all returns entries from both browser sessions for M1, and never leaks M2 or cookie secrets.
        all_trail_res = external.get(
            "/v0.1/audit",
            params={"customerId": M1.customerId, "scope": "all"},
            headers=HOTEL_AUTH,
        )
        assert all_trail_res.status_code == 200, all_trail_res.text
        entries = all_trail_res.json()["entries"]
        assert {e["customerId"] for e in entries} == {M1.customerId}
        assert {prop_one["proposalId"], prop_two["proposalId"]} <= {e["proposalId"] for e in entries}
        assert school_prop["proposalId"] not in {e["proposalId"] for e in entries}

        # Verify chain with scope=all: each session's chain is verified separately, cookie secrets never exposed.
        ver_res = external.get(
            "/v0.1/audit/verify",
            params={"customerId": M1.customerId, "scope": "all"},
            headers=HOTEL_AUTH,
        )
        assert ver_res.status_code == 200, ver_res.text
        ver = ver_res.json()
        assert ver["verified"] is True
        assert ver["chainLength"] == len(entries)
        assert len(ver["sessions"]) == 2
        assert all(s["verified"] is True and s["chainLength"] > 0 for s in ver["sessions"])
        assert cookie_one not in ver_res.text and cookie_two not in ver_res.text

        # CSV export and proposal-scoped read across sessions also work with scope=all.
        csv_res = external.get(
            "/v0.1/audit/export",
            params={"customerId": M1.customerId, "scope": "all", "format": "csv"},
            headers=ADMIN_AUTH,
        )
        assert csv_res.status_code == 200
        assert prop_one["proposalId"] in csv_res.text and prop_two["proposalId"] in csv_res.text

        prop_audit = external.get(
            "/v0.1/audit",
            params={"proposalId": prop_one["proposalId"], "scope": "all"},
            headers=HOTEL_AUTH,
        )
        assert prop_audit.status_code == 200
        assert prop_audit.json()["entries"]

        # Tampering with browser_one's log fails only that session's chain while browser_two remains valid.
        state_one = registry.get(cookie_one)
        state_one.gateway.log._entries[0] = state_one.gateway.log._entries[0].model_copy(
            update={"summary": "tampered summary"}
        )
        tampered_ver = external.get(
            "/v0.1/audit/verify",
            params={"customerId": M1.customerId, "scope": "all"},
            headers=ADMIN_AUTH,
        ).json()
        assert tampered_ver["verified"] is False
        assert sorted(s["verified"] for s in tampered_ver["sessions"]) == [False, True]
