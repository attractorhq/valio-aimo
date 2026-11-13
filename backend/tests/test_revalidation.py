from conftest import BUYER, MANAGER, commit, propose
from sqlalchemy import select

from app.db.models import Product


def test_new_unknown_allergens_need_new_human_review(context, payload):
    client, _, factory, _ = context
    payload["excluded_allergens"] = ["nuts"]
    payload["budget"] = "100"
    p = propose(client, payload)
    route = f"/api/v1/proposals/{p['id']}/approve"
    assert client.post(route, headers=MANAGER, json={"note": "Budget exception approved"}).status_code == 200
    with factory() as db:
        product = db.get(Product, "DEMO-MLK-02")
        product.data = {**product.data, "allergens": None}
        db.commit()
    assert commit(client, p["id"]).status_code == 409
    refreshed = client.get(f"/api/v1/proposals/{p['id']}", headers=BUYER).json()
    assert refreshed["status"] == "NEEDS_APPROVAL"
    assert "ALLERGEN_DATA_UNKNOWN" in {r["code"] for r in refreshed["reasons"]}
    assert refreshed["approved_by"] is None


def test_persisted_timestamp_has_timezone(context, payload):
    client = context[0]
    p = propose(client, payload)
    reread = client.get(f"/api/v1/proposals/{p['id']}", headers=BUYER).json()
    assert reread["created_at"].endswith("Z")
    events = client.get("/api/v1/audit", headers=BUYER).json()
    assert all(e["created_at"].endswith("Z") for e in events)


def test_inquiries_never_create_orders(context, payload):
    from app.db.models import Order, Proposal

    client, _, factory, _ = context
    inquiry = {"items": payload["items"], "delivery_date": payload["delivery_date"]}
    assert client.post("/api/v1/check-offer", headers=BUYER, json=inquiry).status_code == 200
    with factory() as db:
        assert list(db.scalars(select(Order))) == []
        assert list(db.scalars(select(Proposal))) == []
