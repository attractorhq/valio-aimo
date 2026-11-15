"""Real request dependencies with handmade catalogue/provider: no network, data or secrets."""

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi import HTTPException
from synthetic.demo_client import DemoTestClient as TestClient
from test_agent_run import A_TEXT, B_TEXT, NOW, SyntheticAgentProvider, _build_synthetic_catalog

from app.api import state as state_module
from app.api.state import GatewayState, SandboxRegistry, fresh_sandbox
from app.gateway.proposals import ProposalGateway
from app.main import app
from app.policy import M1, M2


@pytest.fixture
def sandboxes(monkeypatch):
    products = _build_synthetic_catalog()
    resources = GatewayState(ProposalGateway(products=products, provider=SyntheticAgentProvider()),
                             list(products.values()), "RECORDED", clock=lambda: NOW)
    registry = SandboxRegistry(lambda sandbox_id: fresh_sandbox(resources, sandbox_id))
    monkeypatch.setattr(state_module, "_state", resources)
    monkeypatch.setattr(state_module, "_registry", registry)
    app.dependency_overrides.clear()
    yield registry, resources
    app.dependency_overrides.clear()


def run_a(client):
    response = client.post("/v0.1/agent/run", json={"customerId": M1.customerId, "requestText": A_TEXT})
    assert response.status_code == 200, response.text
    return response.json()["proposal"]


def lower_limit(client):
    response = client.patch(f"/v0.1/admin/mandates/{M1.customerId}", json={"autoApproveLimitEur": "100.00"})
    assert response.status_code == 200, response.text


def test_two_sandboxes_have_private_mandates_proposals_approvals_logs_and_stats(sandboxes):
    registry, resources = sandboxes
    with TestClient(app, headers={"X-Demo-Sandbox": "visitor-one"}) as one, \
            TestClient(app, headers={"X-Demo-Sandbox": "visitor-two"}) as two:
        lower_limit(one)
        pending = run_a(one)
        auto = run_a(two)
        assert pending["decision"] == "ASK"
        assert auto["decision"] == "AUTO" and auto["totals"]["exclVat"] == "1010.00"
        assert {p["proposalId"] for p in two.get("/v0.1/approver/proposals").json()} == {auto["proposalId"]}
        assert two.get(f"/v0.1/proposals/{pending['proposalId']}").status_code == 404
        assert two.get("/v0.1/audit", params={"proposalId": pending["proposalId"]}).status_code == 404
        assert all(e["proposalId"] != pending["proposalId"] for e in two.get(
            "/v0.1/audit/export", params={"customerId": M1.customerId},
        ).json()["entries"])
        for client in (one, two):
            assert client.get("/v0.1/audit/verify").json()["verified"] is True
        assert one.get("/v0.1/insights").json()["decisionStats"]["byDecision"]["ASK"] == 1
        assert two.get("/v0.1/insights").json()["decisionStats"]["byDecision"]["ASK"] == 0
        assert one.get("/health").json()["sandboxes"] == 2
    first, second = registry.get("visitor-one"), registry.get("visitor-two")
    assert first.gateway is not second.gateway
    assert first.gateway.mandates is not second.gateway.mandates
    assert first.gateway.approvals is not second.gateway.approvals
    assert first.gateway.log is not second.gateway.log
    assert first.gateway.products is second.gateway.products is resources.gateway.products
    assert first.catalog is second.catalog is resources.catalog
    assert first.gateway.provider is second.gateway.provider is resources.gateway.provider


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_browser_cookie_is_random_httponly_lax_secure_on_https_and_reused(sandboxes, scheme):
    with TestClient(app, base_url=f"{scheme}://testserver", headers={"Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"}) as one, \
            TestClient(app, base_url=f"{scheme}://testserver", headers={"Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"}) as two:
        response = one.get("/health")
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert ("Secure" in cookie) == (scheme == "https")
        assert response.headers["cache-control"] == "no-store"
        first_id = one.cookies["voa_sandbox"]
        assert len(first_id) >= 32
        one.get("/health")
        assert one.cookies["voa_sandbox"] == first_id
        two.get("/health")
        assert two.cookies["voa_sandbox"] != first_id
        lower_limit(one)
        assert run_a(two)["decision"] == "AUTO"


def test_cookie_does_not_override_explicit_api_sandbox_and_invalid_headers_fail(sandboxes):
    with TestClient(app, headers={"Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"}) as browser:
        browser.get("/health")
        lower_limit(browser)
        response = browser.get(f"/v0.1/mandates/{M1.customerId}", headers={"X-Demo-Sandbox": "explicit"})
        assert response.json()["autoApproveLimitEur"] == "2500.00"
        for bad in ("bad/id", "x" * 101, ""):
            assert browser.get("/health", headers={"X-Demo-Sandbox": bad}).status_code == 400


def test_cookieless_clients_get_private_sessions_and_stay_in_them_by_echoing_the_header(sandboxes):
    """T116: two cookieless clients with the same key never share state; echoing X-Demo-Sandbox keeps a session."""
    registry, _ = sandboxes
    with TestClient(app) as one, TestClient(app) as two:
        assert "set-cookie" not in one.get("/health").headers
        changed = one.patch(f"/v0.1/admin/mandates/{M1.customerId}", json={"autoApproveLimitEur": "100.00"})
        session = changed.headers["x-demo-sandbox"]
        assert session.startswith("api-") and len(session) == 36
        # The other client (no header) gets its own fresh session: the edit is not visible there.
        other = two.get(f"/v0.1/mandates/{M1.customerId}")
        assert other.json()["autoApproveLimitEur"] == format(M1.autoApproveLimitEur, "f")
        assert other.headers["x-demo-sandbox"] not in (session, "")
        # Echoing the id returns to the session, over REST and MCP.
        mine = one.get(f"/v0.1/mandates/{M1.customerId}", headers={"X-Demo-Sandbox": session})
        assert mine.json()["autoApproveLimitEur"] == "100.00"
        assert mine.headers["x-demo-sandbox"] == session
        response = two.post("/mcp", headers={"X-Demo-Sandbox": session}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "getMandate", "arguments": {"customerId": M1.customerId}}})
        assert response.status_code == 200
        assert response.json()["result"]["structuredContent"]["autoApproveLimitEur"] == "100.00"
        assert registry.get(session).gateway.mandates.get(M1.customerId).autoApproveLimitEur == Decimal("100")


def test_reads_in_a_new_session_do_not_fill_the_registry(sandboxes):
    registry, _ = sandboxes
    with TestClient(app) as client:
        before = registry.count()
        for _ in range(5):
            assert client.get("/v0.1/mandates").headers["x-demo-sandbox"].startswith("api-")
        assert registry.count() == before


def _a_offer(client, headers=None):
    run = client.post("/v0.1/agent/run", json={"customerId": M1.customerId, "requestText": A_TEXT})
    offer = client.post("/v0.1/offers", json=run.json()["plan"]["offerRequest"], headers=headers or {})
    assert offer.status_code == 200, offer.text
    body = {"customerId": M1.customerId, "offerId": offer.json()["offerId"],
            "offerVersion": offer.json()["offerVersion"], "request": run.json()["request"]}
    return offer, body


def test_offer_then_proposal_works_when_the_session_is_echoed(sandboxes):
    with TestClient(app) as client:
        offer, body = _a_offer(client)
        session = offer.headers["x-demo-sandbox"]
        created = client.post("/v0.1/proposals", json=body, headers={"X-Demo-Sandbox": session})
        assert created.status_code == 201, created.text
        assert created.headers["x-demo-sandbox"] == session


def test_header_less_offer_then_proposal_continues_in_the_offers_session(sandboxes):
    """Generic HTTP helpers never echo custom headers: naming one's own offer/proposal finds its session."""
    registry, _ = sandboxes
    with TestClient(app) as client:
        offer, body = _a_offer(client)
        session = offer.headers["x-demo-sandbox"]
        created = client.post("/v0.1/proposals", json=body)  # no header
        assert created.status_code == 201, created.text
        assert created.headers["x-demo-sandbox"] == session
        proposal_id = created.json()["proposalId"]
        assert registry.get(session).gateway.get_proposal(proposal_id).customerId == M1.customerId

        read = client.get(f"/v0.1/proposals/{proposal_id}")
        assert read.status_code == 200 and read.headers["x-demo-sandbox"] == session
        audit = client.get("/v0.1/audit", params={"proposalId": proposal_id})
        assert audit.status_code == 200 and audit.headers["x-demo-sandbox"] == session
        verify = client.get("/v0.1/audit/verify", params={"proposalId": proposal_id})
        assert verify.status_code == 200 and verify.headers["x-demo-sandbox"] == session
        mcp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "getProposal", "arguments": {"proposalId": proposal_id}}})
        assert mcp.json()["result"]["structuredContent"]["proposalId"] == proposal_id
        assert mcp.headers["x-demo-sandbox"] == session


def test_another_buyers_key_never_resolves_a_header_less_offer_or_proposal(sandboxes):
    with TestClient(app) as client:
        offer, body = _a_offer(client)
        created = client.post("/v0.1/proposals", json=body)
        proposal_id = created.json()["proposalId"]
        other = {"Authorization": f"Bearer demo-agent-{M2.customerId}"}
        stolen = client.post("/v0.1/proposals", json={**body, "customerId": M2.customerId}, headers=other)
        assert stolen.status_code in (403, 404)
        assert stolen.headers["x-demo-sandbox"] != offer.headers["x-demo-sandbox"]
        read = client.get(f"/v0.1/proposals/{proposal_id}", headers=other)
        assert read.status_code in (403, 404)
        assert read.headers["x-demo-sandbox"] != offer.headers["x-demo-sandbox"]


def test_daily_totals_and_suspension_never_cross_cookieless_clients(sandboxes):
    with TestClient(app) as noisy, TestClient(app) as quiet:
        first = noisy.post("/v0.1/agent/run", json={"customerId": M1.customerId, "requestText": A_TEXT})
        session = first.headers["x-demo-sandbox"]
        for _ in range(2):
            again = noisy.post("/v0.1/agent/run", headers={"X-Demo-Sandbox": session},
                               json={"customerId": M1.customerId, "requestText": A_TEXT})
            assert again.status_code == 200
        # Three refused approval attempts suspend the noisy session only.
        for _ in range(3):
            noisy.post("/v0.1/proposals", headers={"X-Demo-Sandbox": session},
                       json={"customerId": M1.customerId, "approvalToken": "x"})
        assert noisy.get("/v0.1/proposals", headers={"X-Demo-Sandbox": session}).status_code == 403
        # The quiet client's first order is decided on its own: no other client's committed total, no suspension.
        own = quiet.post("/v0.1/agent/run", json={"customerId": M1.customerId, "requestText": A_TEXT})
        assert own.status_code == 200, own.text
        assert (own.json()["proposal"]["decision"], own.json()["proposal"]["reasons"]) == ("AUTO", [])


def test_cookieless_mcp_sessions_are_private_and_can_continue_over_rest(sandboxes):
    """Each MCP session is its own sandbox; sending its Mcp-Session-Id as X-Demo-Sandbox joins it over REST."""
    with TestClient(app) as client:
        initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        first = client.post("/mcp", json=initialize)
        second = client.post("/mcp", json=initialize)
        assert "set-cookie" not in first.headers
        first_id, second_id = first.headers["mcp-session-id"], second.headers["mcp-session-id"]
        assert first.headers["x-demo-sandbox"] == first_id
        changed = client.patch(f"/v0.1/admin/mandates/{M1.customerId}", json={"autoApproveLimitEur": "100.00"},
                               headers={"X-Demo-Sandbox": first_id})
        assert changed.status_code == 200
        query = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "getMandate", "arguments": {"customerId": M1.customerId},
        }}
        limits = {
            session_id: client.post("/mcp", json=query, headers={"Mcp-Session-Id": session_id})
            .json()["result"]["structuredContent"]["autoApproveLimitEur"]
            for session_id in (first_id, second_id)
        }
        assert limits == {first_id: "100.00", second_id: format(M1.autoApproveLimitEur, "f")}


def test_signed_approval_routes_without_cookie_and_tampered_sandbox_is_rejected(sandboxes):
    with TestClient(app, headers={"X-Demo-Sandbox": "buyer-session"}) as issuer, TestClient(app) as phone:
        lower_limit(issuer)
        pending = run_a(issuer)
        path = f"/v0.1/approver/proposals/{pending['proposalId']}"
        view = issuer.get(path).json()
        token = view["approvalToken"]
        assert ".sbx1." in token
        result = phone.get(path, headers={"X-Approval-Token": token})
        assert result.status_code == 200, result.text
        assert result.json()["proposal"] == view["proposal"]
        assert phone.get("/v0.1/approver/proposals/another-proposal", headers={"X-Approval-Token": token}).status_code == 403
        assert phone.get(f"/v0.1/proposals/{pending['proposalId']}").status_code == 404
        tampered = token.split(".")
        tampered[2] = "b3RoZXItc2FuZGJveA"  # another sandbox, with the unchanged signature
        bad = ".".join(tampered)
        assert phone.get(path, headers={"X-Approval-Token": bad}).status_code == 403
        body = {"offerVersion": pending["offerVersion"], "approvalToken": bad, "action": "APPROVE"}
        assert phone.post(path + "/decision", json=body).status_code == 403
        body["approvalToken"] = token
        accepted = phone.post(path + "/decision", json=body)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["state"] == "CONFIRMED_SIMULATED"
        assert issuer.get(f"/v0.1/proposals/{pending['proposalId']}").json()["state"] == "CONFIRMED_SIMULATED"
        assert phone.post(path + "/decision", json=body).status_code == 409
        assert token not in issuer.get("/v0.1/audit", params={"customerId": M1.customerId}).text


def test_reset_clears_confirmed_pending_mandates_approvals_and_hash_chain_only_for_caller(sandboxes):
    with TestClient(app, headers={"X-Demo-Sandbox": "one"}) as one, \
            TestClient(app, headers={"X-Demo-Sandbox": "two"}) as two:
        first = run_a(one)
        assert first["decision"] == "AUTO"
        lower_limit(two)
        other_pending = run_a(two)
        pending = one.post("/v0.1/agent/run", json={"customerId": "northbay-school-kitchen", "requestText": B_TEXT})
        pending = pending.json()["proposal"]
        path = f"/v0.1/approver/proposals/{pending['proposalId']}"
        token = one.get(path).json()["approvalToken"]
        lower_limit(one)
        reset = one.post("/v0.1/admin/demo/reset")
        assert reset.status_code == 200
        assert first["proposalId"] in reset.json()["clearedProposals"]
        assert one.get("/v0.1/approver/proposals").json() == []
        assert one.get(f"/v0.1/proposals/{first['proposalId']}").status_code == 404
        assert one.get("/v0.1/audit/verify").json()["chainLength"] == 0
        assert one.get("/v0.1/insights").json()["decisionStats"]["totalProposals"] == 0
        assert one.get(path, headers={"X-Approval-Token": token}).status_code == 403
        assert two.get(f"/v0.1/proposals/{other_pending['proposalId']}").json()["state"] == "PENDING_APPROVAL"
        assert run_a(one)["decision"] == "AUTO"  # confirmed orders no longer count after reset (T62).


def test_global_reset_requires_signing_key_derived_architect_secret(sandboxes, monkeypatch):
    registry, _ = sandboxes
    monkeypatch.setenv("APPROVAL_SIGNING_KEY", "synthetic-signing-key")
    with TestClient(app, headers={"X-Demo-Sandbox": "one"}) as one, \
            TestClient(app, headers={"X-Demo-Sandbox": "two"}) as two:
        lower_limit(one)
        lower_limit(two)
        assert one.post("/v0.1/admin/demo/reset", headers={"X-Admin-Reset": "all"}).status_code == 403
        secret = hmac.new(b"synthetic-signing-key", b"voa-admin-reset-all", hashlib.sha256).hexdigest()
        response = one.post("/v0.1/admin/demo/reset", headers={"X-Admin-Reset": "all", "X-Admin-Reset-Key": secret})
        assert response.status_code == 200
        assert response.json() == {"scope": "all", "resetSandboxes": 2}
        assert registry.count() == 0
        for client in (one, two):
            assert client.get(f"/v0.1/mandates/{M1.customerId}").json()["version"] == 1


def test_sandbox_lru_idle_expiry_bounds_and_same_id_concurrency(sandboxes):
    _, resources = sandboxes
    ticks = [0.0]
    registry = SandboxRegistry(lambda sandbox_id: fresh_sandbox(resources, sandbox_id), capacity=2,
                               monotonic=lambda: ticks[0])
    first = registry.get("first")
    registry.get("second")
    ticks[0] = 1
    registry.get("first")
    registry.get("third")
    with pytest.raises(HTTPException):
        registry.get("second", create=False)
    assert registry.count() == 2
    ticks[0] += 6 * 60 * 60
    assert registry.count() == 0
    assert registry.get("first") is not first
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: registry.get("concurrent"), range(30)))
    assert all(state is results[0] for state in results)
    bounded = SandboxRegistry(lambda sandbox_id: fresh_sandbox(resources, sandbox_id))
    for i in range(501):
        bounded.get(str(i))
    assert bounded.count() == 500
    with pytest.raises(HTTPException):
        bounded.get("0", create=False)


def test_node_fetch_without_sec_fetch_site_stays_shared_so_offer_then_proposal_works(sandboxes):
    """Node/undici fetch sends Sec-Fetch-Mode but no Sec-Fetch-Site and keeps no cookies: it must use `shared`."""
    with TestClient(app, headers={"Sec-Fetch-Mode": "cors"}) as one, TestClient(app, headers={"Sec-Fetch-Mode": "cors"}) as two:
        assert one.get("/health").status_code == 200
        assert "voa_sandbox" not in one.cookies
        lower_limit(one)
        assert two.get(f"/v0.1/mandates/{M1.customerId}").json()["autoApproveLimitEur"] == \
            one.get(f"/v0.1/mandates/{M1.customerId}").json()["autoApproveLimitEur"]
