import pytest
from conftest import BUYER, HOTEL, commit, propose


@pytest.mark.parametrize(
    "code,restrictions,status,reason",
    [
        ("DEMO-MLK-01", ["milk"], "REJECTED", "ALLERGEN_VIOLATION"),
        ("DEMO-SUP-01", ["milk"], "NEEDS_APPROVAL", "ALLERGEN_DATA_UNKNOWN"),
        ("DEMO-MLK-04", [], "REJECTED", "UNAVAILABLE"),
    ],
)
def test_safety_constraints(context, payload, code, restrictions, status, reason):
    client = context[0]
    payload["items"][0]["product_code"] = code
    payload["required_dietary"] = []
    payload["excluded_allergens"] = restrictions
    p = propose(client, payload)
    assert p["status"] == status
    assert reason in {r["code"] for r in p["reasons"]}
    assert commit(client, p["id"]).status_code == 409


def test_dietary_false_and_unknown(context, payload):
    client = context[0]
    payload["items"][0]["product_code"] = "DEMO-MLK-03"
    assert propose(client, payload)["status"] == "REJECTED"
    payload["items"][0]["product_code"] = "DEMO-SUP-01"
    assert propose(client, payload)["status"] == "NEEDS_APPROVAL"


def test_equivalent_substitution_is_buyer_specific(context, payload):
    client = context[0]
    payload["items"][0]["substitute_for"] = "DEMO-MLK-01"
    assert propose(client, payload, BUYER)["status"] == "NEEDS_APPROVAL"
    assert propose(client, payload, HOTEL)["status"] == "AUTO_APPROVED"


def test_allergen_filter_excludes_unknown_and_conflicts(context):
    found = context[0].get("/api/v1/products", headers=BUYER, params={"exclude_allergen": "milk"}).json()
    assert found
    assert all(p["allergens"] is not None and "milk" not in p["allergens"] for p in found)


def test_confirmed_unavailable_cannot_be_approved(context, payload):
    client, provider, _, _ = context
    payload["budget"] = "100"
    p = propose(client, payload)
    provider.stock["DEMO-MLK-02"] = 0
    from conftest import MANAGER

    assert (
        client.post(
            f"/api/v1/proposals/{p['id']}/approve", headers=MANAGER, json={"note": "Cannot override stock"}
        ).status_code
        == 409
    )
