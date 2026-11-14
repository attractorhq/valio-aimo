"""T88: rejected requirement bindings and unexpected failures stay structured over HTTP.

Only the existing hand-made synthetic products/provider are used; no supplier records or network.
"""

import re

import pytest
from fastapi.testclient import TestClient
from test_api import api as api
from test_api import item, offer_and_propose
from test_proposals_gateway import MONDAY

from app.main import app
from app.policy import M1


@pytest.mark.parametrize("product_id", ["SYN-PASTA", "SYN-REJECTED-SUB"])
def test_missing_lactose_free_claim_returns_no_order_instead_of_500(api, product_id):
    client, provider, state = api
    # A hand-made equivalent of the cheese repro: the offer is otherwise valid, but the
    # product does not declare lactose-free. Use Tuesday as in the reported REST request.
    tuesday = MONDAY.replace(day=17)
    provider.stocks[product_id]["scheduleLines"][0]["availableDate"] = tuesday.isoformat()
    offered = client.post("/v0.1/offers", json={
        "customerId": M1.customerId, "requestedDate": tuesday.isoformat(),
        "items": [{"itemId": "I1", "productId": product_id, "quantity": "1", "unit": "kg" if product_id == "SYN-PASTA" else "l"}],
    })
    assert offered.status_code == 200, offered.text
    offer = offered.json()
    assert len(offer["lines"]) == 1
    calls_before = len(provider.calls)
    body = {
        "customerId": M1.customerId, "offerId": offer["offerId"], "offerVersion": offer["offerVersion"],
        "request": {
            "requestText": "One unit of a lactose-free product by Tuesday.", "requestedDate": tuesday.isoformat(),
            "items": [item("I1", "lactose-free product", "1", "kg" if product_id == "SYN-PASTA" else "l",
                           [{"kind": "CLAIM", "code": "FREE_FROM_LACTOSE"}])],
            "assumptions": [],
        },
    }
    for _ in range(2):
        rejected = client.post("/v0.1/proposals", json=body)
        assert rejected.status_code == 422, rejected.text
        assert rejected.headers["content-type"].startswith("application/json")
        error = rejected.json()["error"]
        assert error["code"] == "NO_ORDER"
        assert "CLAIM_MISSING" in error["details"]["reasons"]
        assert "CLAIM_MISSING" in error["message"]
        assert any(entry["productId"] == product_id and "CLAIM_MISSING" in entry["reasons"]
                   for entry in error["details"]["excluded"])
    assert len(provider.calls) == calls_before  # no confirmation/re-check of an excluded item
    assert not state.gateway._proposals


def test_two_empty_offers_over_http_have_distinct_offer_hashes(api):
    client, provider, _ = api
    hashes = []
    for product_id, unit in (("SYN-PASTA", "kg"), ("SYN-OAT", "l")):
        provider.stocks[product_id]["scheduleLines"] = []
        resp = client.post("/v0.1/offers", json={
            "customerId": M1.customerId,
            "requestedDate": MONDAY.isoformat(),
            "items": [{"itemId": "I1", "productId": product_id, "quantity": "1", "unit": unit}],
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["lines"] == []
        assert len(data["excluded"]) == 1
        hashes.append(data["offerHash"])
    assert hashes[0] != hashes[1]


def test_same_product_without_extra_requirement_remains_confirmable(api):
    client, _, _ = api
    _, created = offer_and_propose(
        client, M1.customerId, [("I1", "SYN-PASTA", "1", "kg")],
        "One kilogram of synthetic pasta by Monday.", [item("I1", "pasta", "1", "kg")],
    )
    assert created.status_code == 201, created.text
    assert created.json()["state"] == "CONFIRMED_SIMULATED"


@pytest.mark.parametrize("path", ["/v0.1/offers", "/v0.1/proposals"])
def test_unhandled_gateway_error_is_json_with_private_server_request_id(api, monkeypatch, caplog, path):
    _, _, state = api
    private_message = "private upstream credential and approval capability must not be exposed"

    def broken(*args, **kwargs):
        raise RuntimeError(private_message)

    if path.endswith("/offers"):
        monkeypatch.setattr(state.gateway, "get_offer", broken)
        body = {"customerId": M1.customerId, "requestedDate": MONDAY.isoformat(), "items": [
            {"itemId": "I1", "productId": "SYN-PASTA", "quantity": "1", "unit": "kg"},
        ]}
    else:
        monkeypatch.setattr(state.gateway, "get_registered_offer", broken)
        body = {"customerId": M1.customerId, "offerId": "SYN-OFFER", "offerVersion": 1, "request": {
            "requestText": "Synthetic pasta", "requestedDate": MONDAY.isoformat(),
            "items": [item("I1", "pasta", "1", "kg")], "assumptions": [],
        }}

    with TestClient(app, raise_server_exceptions=False) as client:
        responses = [client.post(path, json=body, headers={"X-Request-ID": "untrusted-caller-value"}) for _ in range(2)]
    ids = []
    for response in responses:
        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["cache-control"] == "no-store"
        error = response.json()["error"]
        assert error["code"] == "INTERNAL_ERROR"
        request_id = error["details"]["requestId"]
        assert re.fullmatch(r"[0-9a-f]{32}", request_id)
        assert response.headers["X-Request-ID"] == request_id
        assert request_id in caplog.text
        assert private_message not in response.text
        ids.append(request_id)
    assert ids[0] != ids[1]
    assert private_message not in caplog.text


def test_expected_validation_errors_keep_their_status_and_shape(api):
    client, _, _ = api
    response = client.post("/v0.1/offers", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
