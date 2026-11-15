"""T55 access boundaries: raw clients, explicit credentials, handmade synthetic records."""

import pytest
from fastapi.testclient import TestClient
from test_api import api, scenario_a, scenario_b  # noqa: F401

from app.main import app
from app.policy import M1, M2

SCHOOL = {"Authorization": f"Bearer demo-agent-{M2.customerId}"}
HOTEL = {"Authorization": f"Bearer demo-agent-{M1.customerId}"}
CHEF = {"X-Demo-Approver-Role": "Head Chef"}
MANAGER = {"X-Demo-Approver-Role": "Purchasing Manager"}
ADMIN = {"X-Demo-Admin-Key": "demo-admin"}


@pytest.fixture
def access(api):  # noqa: F811
    authenticated, provider, state = api
    _, school = scenario_b(authenticated)
    _, hotel = scenario_a(authenticated)
    with TestClient(app) as raw:
        yield raw, school.json(), hotel.json(), provider, state


def rpc(name, arguments=None):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


def test_approver_must_choose_role_and_queue_is_server_scoped(access):
    client, school, hotel, _, _ = access
    assert client.get("/v0.1/approver/proposals").status_code == 401
    for headers, proposal in ((CHEF, school), (MANAGER, hotel)):
        queue = client.get("/v0.1/approver/proposals", headers=headers)
        assert queue.status_code == 200
        assert [p["proposalId"] for p in queue.json()] == [proposal["proposalId"]]
    assert client.get("/v0.1/approver/proposals", headers=CHEF,
                      params={"customerId": M1.customerId}).status_code == 403
    assert len(client.get("/v0.1/approver/proposals", headers=ADMIN).json()) == 2
    path = f"/v0.1/approver/proposals/{school['proposalId']}"
    for headers, status in (({}, 401), (SCHOOL, 401), (ADMIN, 401), (MANAGER, 403)):
        assert client.get(path, headers=headers).status_code == status


@pytest.mark.parametrize("action", ["APPROVE", "REJECT"])
def test_wrong_role_cannot_decide_even_with_valid_phone_token(access, action):
    client, school, _, _, state = access
    path = f"/v0.1/approver/proposals/{school['proposalId']}"
    view = client.get(path, headers=CHEF).json()
    body = {"offerVersion": school["offerVersion"], "approvalToken": view["approvalToken"], "action": action}
    assert client.post(path + "/decision", headers=MANAGER, json=body).status_code == 403
    assert state.gateway.get_proposal(school["proposalId"]).state == "PENDING_APPROVAL"
    # A real phone can use its signed capability without a cookie or separate role header.
    assert client.get(path, headers={"X-Approval-Token": view["approvalToken"]}).status_code == 200
    accepted = client.post(path + "/decision", json=body)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == ("CONFIRMED_SIMULATED" if action == "APPROVE" else "REJECTED")
    assert client.post(path + "/decision", json=body).status_code == 409


@pytest.mark.parametrize("path,method,body", [
    (f"/v0.1/admin/mandates/{M2.customerId}", "PATCH", {"autoApproveLimitEur": "510.00"}),
    (f"/v0.1/admin/mandates/{M2.customerId}/reset", "POST", None),
    ("/v0.1/admin/demo/reset", "POST", None),
])
def test_admin_mutations_require_admin_key(access, path, method, body):
    client, _, _, _, state = access
    for headers in ({}, SCHOOL, CHEF, {"X-Demo-Admin-Key": "wrong"}):
        assert client.request(method, path, json=body, headers=headers).status_code == 401
    assert state.gateway.mandates.get(M2.customerId).version == 1
    assert len(state.proposal_ids) == 2


def test_admin_key_permits_mandate_write_and_audits_it(access):
    client, _, _, _, _ = access
    result = client.patch(f"/v0.1/admin/mandates/{M2.customerId}", headers=ADMIN,
                          json={"autoApproveLimitEur": "510.00"})
    assert result.status_code == 200
    assert result.json()["mandate"]["version"] == 2
    assert result.json()["auditEntry"]["actor"] == "human:Buyer admin"


@pytest.mark.parametrize("path,body", [
    ("/v0.1/products/search?customerId=northbay-school-kitchen", {"query": "oat"}),
    ("/v0.1/offers", {"customerId": M2.customerId, "requestedDate": "2026-11-16",
                       "items": [{"itemId": "I1", "productId": "SYN-OAT", "quantity": "1", "unit": "l"}]}),
    ("/v0.1/proposals", {"customerId": M2.customerId, "offerId": "unused", "offerVersion": 1,
                          "request": {"requestText": "Oat", "requestedDate": "2026-11-16",
                                      "items": [{"itemId": "I1", "what": "oat", "quantity": "1", "unit": "l"}]}}),
    ("/v0.1/agent/run", {"customerId": M2.customerId, "requestText": "1 litre of oat drink"}),
])
def test_agent_rest_writes_require_key_and_reject_other_buyer_before_work(access, path, body):
    client, _, _, provider, state = access
    before_calls, before_ids = len(provider.calls), list(state.proposal_ids)
    for headers in ({}, {"Authorization": "Bearer wrong"}, ADMIN, CHEF):
        refused = client.post(path, json=body, headers=headers)
        assert refused.status_code == 401, refused.text
        assert refused.headers["www-authenticate"] == "Bearer"
    assert client.post(path, json=body, headers=HOTEL).status_code == 403
    assert len(provider.calls) == before_calls
    assert state.proposal_ids == before_ids


@pytest.mark.parametrize("path", ["/v0.1/audit", "/v0.1/audit/verify", "/v0.1/audit/export"])
@pytest.mark.parametrize("scope", ["customerId", "proposalId"])
def test_audit_reads_exports_and_verification_enforce_same_customer_scope(access, path, scope):
    client, school, _, _, _ = access
    params = {scope: M2.customerId if scope == "customerId" else school["proposalId"]}
    assert client.get(path, params=params).status_code == 401
    assert client.get(path, headers=HOTEL, params=params).status_code == 403
    allowed = client.get(path, headers=SCHOOL, params=params)
    assert allowed.status_code == 200, allowed.text
    if path == "/v0.1/audit/verify":
        assert allowed.json()["verified"] is True
    else:
        assert {e["customerId"] for e in allowed.json()["entries"]} == {M2.customerId}
    if path.endswith("export"):
        assert client.get(path, params={**params, "format": "csv"}, headers=HOTEL).status_code == 403
        assert client.get(path, params={**params, "format": "csv"}, headers=SCHOOL).status_code == 200


def test_global_audit_is_admin_only_and_customer_list_is_key_scoped(access):
    client, school, hotel, _, _ = access
    for path in ("/v0.1/audit", "/v0.1/audit/verify"):
        for headers in ({}, CHEF, {"X-Demo-Admin-Key": "wrong"}):
            assert client.get(path, headers=headers).status_code == 401
        assert client.get(path, headers=ADMIN).status_code == 200
    # T110: an agent key without a scope reads no global trail, but verifies its own buyer's chain.
    assert client.get("/v0.1/audit", headers=SCHOOL).status_code == 401
    assert client.get("/v0.1/audit/verify", headers=SCHOOL).status_code == 200
    assert {e["customerId"] for e in client.get("/v0.1/audit", headers=ADMIN).json()["entries"]} == {
        M1.customerId, M2.customerId}
    for headers, expected in ((SCHOOL, school), (HOTEL, hotel)):
        assert [p["proposalId"] for p in client.get("/v0.1/proposals", headers=headers).json()] == [expected["proposalId"]]


def test_mcp_enforces_http_keys_and_binds_customer_independently_of_arguments(access):
    client, school, hotel, _, _ = access
    for method in ("GET", "POST", "DELETE"):
        for headers in ({}, {"Authorization": "Bearer wrong"}, ADMIN):
            assert client.request(method, "/mcp", headers=headers,
                                  json=rpc("listMandates") if method == "POST" else None).status_code == 401
    result = client.post("/mcp", headers=SCHOOL, json=rpc("listMandates")).json()["result"]["structuredContent"]
    assert [m["customerId"] for m in result["mandates"]] == [M2.customerId]
    own = client.post("/mcp", headers=SCHOOL, json=rpc("getMandate")).json()["result"]["structuredContent"]
    assert own["customerId"] == M2.customerId
    cases = [("getMandate", {"customerId": M1.customerId}),
             ("getOffer", {"customerId": M1.customerId}),
             ("createProposal", {"customerId": M1.customerId}),
             ("searchProducts", {"customerId": M1.customerId}),
             ("getProposal", {"proposalId": hotel["proposalId"]}),
             ("getAuditTrail", {"customerId": M1.customerId}),
             ("getAuditTrail", {"proposalId": hotel["proposalId"]})]
    for name, arguments in cases:
        result = client.post("/mcp", headers=SCHOOL, json=rpc(name, arguments)).json()["result"]
        assert result["isError"] is True
        assert result["structuredContent"]["error"]["code"] == "FORBIDDEN"
    # T111: repeated cross-buyer calls suspend that agent session; another session of the same key still reads.
    own_log = client.post("/mcp", headers={**SCHOOL, "User-Agent": "Another session"},
                          json=rpc("getAuditTrail", {"proposalId": school["proposalId"]})).json()["result"]
    assert own_log["isError"] is False
    assert {e["customerId"] for e in own_log["structuredContent"]["entries"]} == {M2.customerId}


@pytest.mark.parametrize("tool", ["getProposal", "getAuditTrail"])
def test_mcp_validates_identifiers_before_ownership_lookup(access, tool):
    client, _, _, _, _ = access
    response = client.post("/mcp", headers=SCHOOL, json=rpc(tool, {"proposalId": ["not-a-string"]}))
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["error"]["code"] == "VALIDATION_ERROR"


def test_mcp_omitted_customer_on_offer_and_proposal_uses_key_buyer(access):
    client, _, _, _, _ = access
    offer = client.post("/mcp", headers=SCHOOL, json=rpc("getOffer", {
        "requestedDate": "2026-11-16",
        "items": [{"itemId": "I1", "productId": "SYN-OAT", "quantity": "1", "unit": "l"}],
    })).json()["result"]["structuredContent"]
    assert offer["customerId"] == M2.customerId
    proposal = client.post("/mcp", headers=SCHOOL, json=rpc("createProposal", {
        "offerId": offer["offerId"], "offerVersion": offer["offerVersion"],
        "request": {"requestText": "1 litre of oat drink", "requestedDate": "2026-11-16",
                    "items": [{"itemId": "I1", "what": "oat drink", "quantity": "1", "unit": "l"}]},
    })).json()["result"]["structuredContent"]
    assert proposal["customerId"] == M2.customerId
    assert not {"approvalToken", "approvalUrl", "qrPayload"} & proposal.keys()


def test_proposal_reads_are_scoped_to_the_owning_buyer(access):
    """T100: like the audit (T55), a proposal read needs the owner's agent key on the shared session."""
    client, school, hotel, _, _ = access
    path = f"/v0.1/proposals/{school['proposalId']}"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=SCHOOL).status_code == 200
    other = client.get(path, headers=HOTEL)
    assert other.status_code == 404  # no hint that the proposal exists
    unknown = client.get("/v0.1/proposals/P-DOES-NOT-EXIST", headers=HOTEL)
    assert unknown.status_code == 404 and other.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert client.get(path, headers=ADMIN).status_code == 200
    assert client.get(path, headers=CHEF).status_code == 200  # the approver who signs it off
    assert client.get(path, headers=MANAGER).status_code == 403
    assert client.get(f"/v0.1/proposals/{hotel['proposalId']}", headers=HOTEL).status_code == 200


def test_a_used_phone_link_can_still_read_its_own_proposal(access):
    client, school, _, _, _ = access
    path = f"/v0.1/approver/proposals/{school['proposalId']}"
    view = client.get(path, headers=CHEF).json()
    body = {"offerVersion": school["offerVersion"], "approvalToken": view["approvalToken"], "action": "APPROVE"}
    assert client.post(path + "/decision", json=body).status_code == 200
    read = client.get(f"/v0.1/proposals/{school['proposalId']}", headers={"X-Approval-Token": view["approvalToken"]})
    assert read.status_code == 200 and read.json()["state"] == "CONFIRMED_SIMULATED"


def _entries(client, customer_id):
    return client.get("/v0.1/audit", headers=ADMIN, params={"customerId": customer_id}).json()["entries"]


def test_t110_agent_key_verifies_only_its_own_buyers_chain(access):
    client, school, hotel, _, _ = access
    full = client.get("/v0.1/audit/verify", headers=ADMIN).json()
    assert full["verified"] is True and full["customerId"] is None and full["proposalId"] is None
    for headers, customer in ((SCHOOL, M2.customerId), (HOTEL, M1.customerId)):
        own = client.get("/v0.1/audit/verify", headers=headers)
        assert own.status_code == 200, own.text
        body = own.json()
        assert body["verified"] is True
        assert body["customerId"] == customer
        assert body["chainLength"] == len(_entries(client, customer)) > 0
        assert body["chainLength"] < full["chainLength"]
        assert body["headHash"] == body["links"][-1]["entryHash"] != full["headHash"]
        assert {link["entryId"] for link in body["links"]} == {e["entryId"] for e in _entries(client, customer)}
        # The same as asking for the buyer explicitly, and as the admin's scoped verify.
        explicit = client.get("/v0.1/audit/verify", headers=headers, params={"customerId": customer}).json()
        admin_scoped = client.get("/v0.1/audit/verify", headers=ADMIN, params={"customerId": customer}).json()
        assert explicit["headHash"] == admin_scoped["headHash"] == body["headHash"]
    by_proposal = client.get("/v0.1/audit/verify", headers=SCHOOL, params={"proposalId": school["proposalId"]})
    assert by_proposal.status_code == 200
    assert by_proposal.json()["proposalId"] == school["proposalId"]
    assert by_proposal.json()["customerId"] == M2.customerId


def test_t110_verify_errors_name_the_key_to_send(access):
    client, school, hotel, _, _ = access
    missing = client.get("/v0.1/audit/verify")
    assert missing.status_code == 401
    message = missing.json()["error"]["message"]
    assert "Authorization: Bearer demo-agent-<customerId>" in message
    assert "X-Demo-Admin-Key: demo-admin" in message
    wrong_admin = client.get("/v0.1/audit/verify", headers={"X-Demo-Admin-Key": "wrong"})
    assert wrong_admin.status_code == 401
    assert "X-Demo-Admin-Key: demo-admin" in wrong_admin.json()["error"]["message"]
    for params in ({"customerId": M1.customerId}, {"proposalId": hotel["proposalId"]}):
        other = client.get("/v0.1/audit/verify", headers=SCHOOL, params=params)
        assert other.status_code == 403
        message = other.json()["error"]["message"]
        assert "Authorization: Bearer demo-agent-<customerId>" in message
        assert "X-Demo-Admin-Key: demo-admin" in message
    global_trail = client.get("/v0.1/audit", headers=SCHOOL)
    assert "customerId or proposalId" in global_trail.json()["error"]["message"]


def test_t110_openapi_documents_both_verify_keys():
    with TestClient(app) as client:
        operation = client.get("/openapi.json").json()["paths"]["/v0.1/audit/verify"]["get"]
    text = operation["description"]
    assert "Authorization: Bearer demo-agent-<customerId>" in text
    assert "X-Demo-Admin-Key: demo-admin" in text
