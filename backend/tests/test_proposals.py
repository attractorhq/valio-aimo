from decimal import Decimal

import pytest
from conftest import BUYER, HOTEL, MANAGER, commit, propose
from sqlalchemy import func, select

from app.db.models import Order


def test_complete_auto_flow_and_idempotency(context, payload):
    client, _, factory, _ = context
    p = propose(client, payload)
    assert p["status"] == "AUTO_APPROVED"
    assert p["offer"]["total"] == "139.00"
    result = commit(client, p["id"])
    assert result.status_code == 200, result.text
    order = result.json()["order"]
    assert order["simulated"] is True
    assert commit(client, p["id"]).json()["order"]["id"] == order["id"]
    assert commit(client, p["id"], key="another-key").status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1
    events = client.get("/api/v1/audit", params={"proposal_id": p["id"]}, headers=BUYER).json()
    assert {
        "REQUEST_RECEIVED",
        "OFFER_VERIFIED",
        "POLICY_EVALUATED",
        "OFFER_REVALIDATED",
        "ORDER_SIMULATED",
    } <= {e["event"] for e in events}


def test_over_limit_requires_real_manager_action(context, payload):
    client = context[0]
    payload["items"][0]["quantity"] = "200"
    p = propose(client, payload)
    assert p["status"] == "NEEDS_APPROVAL"
    assert commit(client, p["id"]).status_code == 409
    route = f"/api/v1/proposals/{p['id']}/approve"
    assert client.post(route, headers=BUYER, json={"note": "Accept"}).status_code == 403
    approved = client.post(route, headers=MANAGER, json={"note": "Event budget explicitly authorized"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by"] == "manager-demo"
    assert commit(client, p["id"]).json()["status"] == "COMMITTED"


def test_identity_scoping(context, payload):
    client = context[0]
    p = propose(client, payload)
    assert client.get(f"/api/v1/proposals/{p['id']}", headers=HOTEL).status_code == 403
    assert client.get("/api/v1/proposals", headers=HOTEL).json() == []
    assert client.get("/api/v1/audit", params={"proposal_id": p["id"]}, headers=HOTEL).status_code == 403
    assert client.post("/api/v1/proposals", json=payload, headers=MANAGER).status_code == 403
    assert client.get("/api/v1/products").status_code == 401


@pytest.mark.parametrize(
    "change,reason", [("price", "PRICE_CHANGED"), ("stock", "UNAVAILABLE"), ("policy", "POLICY_CHANGED")]
)
def test_commit_revalidates(context, payload, change, reason):
    client, provider, _, _ = context
    p = propose(client, payload)
    if change == "price":
        provider.prices["DEMO-MLK-02"] = Decimal("1.50")
    elif change == "stock":
        provider.stock["DEMO-MLK-02"] = Decimal("50")
    else:
        assert (
            client.patch(
                "/api/v1/policies/restaurant-a", headers=MANAGER, json={"max_auto_total": "100"}
            ).status_code
            == 200
        )
    assert commit(client, p["id"]).status_code == 409
    refreshed = client.get(f"/api/v1/proposals/{p['id']}", headers=BUYER).json()
    assert reason in {r["code"] for r in refreshed["reasons"]}
    if change == "price":
        assert refreshed["offer"]["total"] == "150.00"
        assert (
            client.post(
                f"/api/v1/proposals/{p['id']}/approve", headers=MANAGER, json={"note": "Accept new price"}
            ).status_code
            == 200
        )
        assert commit(client, p["id"]).status_code == 200


def test_approval_does_not_bypass_changed_terms(context, payload):
    client, provider, _, _ = context
    payload["budget"] = "100"
    p = propose(client, payload)
    route = f"/api/v1/proposals/{p['id']}/approve"
    assert client.post(route, headers=MANAGER, json={"note": "Budget authorized"}).status_code == 200
    provider.prices["DEMO-MLK-02"] = Decimal("1.49")
    assert commit(client, p["id"]).status_code == 409
    assert client.get(f"/api/v1/proposals/{p['id']}", headers=BUYER).json()["approved_by"] is None


def test_reviewer_must_see_updated_terms(context, payload):
    client, provider, _, _ = context
    payload["budget"] = "100"
    p = propose(client, payload)
    provider.prices["DEMO-MLK-02"] = Decimal("1.55")
    route = f"/api/v1/proposals/{p['id']}/approve"
    assert client.post(route, headers=MANAGER, json={"note": "Approve old terms"}).status_code == 409
    assert client.post(route, headers=MANAGER, json={"note": "Approve refreshed terms"}).status_code == 200


def test_pack_and_unit_validation(context, payload):
    client = context[0]
    payload["items"] = [{"product_code": "DEMO-RIC-01", "quantity": "7", "unit": "KG"}]
    assert client.post("/api/v1/proposals", headers=BUYER, json=payload).status_code == 400
    payload["items"][0].update(quantity="10", unit="L")
    assert client.post("/api/v1/proposals", headers=BUYER, json=payload).status_code == 400
    payload["items"][0].update(quantity="-1", unit="KG")
    assert client.post("/api/v1/proposals", headers=BUYER, json=payload).status_code == 400


def test_idempotency_key_reuse_conflicts(context, payload):
    client = context[0]
    first, second = propose(client, payload), propose(client, payload)
    assert commit(client, first["id"]).status_code == 200
    assert commit(client, second["id"]).status_code == 409


def test_manager_reject_is_terminal(context, payload):
    client = context[0]
    payload["budget"] = "100"
    p = propose(client, payload)
    assert (
        client.post(
            f"/api/v1/proposals/{p['id']}/reject", headers=MANAGER, json={"note": "Outside event budget"}
        ).json()["status"]
        == "REJECTED"
    )
    assert commit(client, p["id"]).status_code == 409
