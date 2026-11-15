"""T108: a NO_CANDIDATE refusal from createProposal (MCP tool and REST) carries the buyer UI's no-order help."""

from test_mcp import GLUTEN_FREE, MONDAY
from test_mcp import mcp_env as mcp_env

from app.policy import M1

OFFER_ARGS = {
    "customerId": M1.customerId,
    "requestedDate": MONDAY.isoformat(),
    "items": [{"itemId": "I1", "productId": "SYN-MILK", "quantity": "10", "unit": "l"}],
}


def _proposal_args(offer):
    return {
        "customerId": M1.customerId,
        "offerId": offer["offerId"],
        "offerVersion": offer["offerVersion"],
        "request": {
            "requestText": "10 litres of gluten-free milk drink by Monday.",
            "requestedDate": MONDAY.isoformat(),
            "items": [{
                "itemId": "I1", "what": "milk drink", "quantity": "10", "unit": "l",
                "requirements": [GLUTEN_FREE],
            }],
        },
    }


def _check(no_order):
    (item,) = no_order["items"]
    assert item["itemId"] == "I1"
    assert item["searched"] == "milk drink · gluten-free"
    assert item["why"]
    # T113: the same-type candidate that was excluded is listed with its reason, never ordered.
    assert [(m["productId"], m["lacks"]) for m in item["closestMatches"]] == [
        ("SYN-MILK", "not declared gluten-free"),
    ]
    assert "earliestDeliverableDate" in no_order and "retryHint" in no_order
    assert no_order["earliestDeliverableDate"] is None and no_order["retryHint"] is None


def test_mcp_create_proposal_no_candidate_carries_no_order_help(mcp_env):
    mcp, _, state = mcp_env
    offer = mcp.call_tool("getOffer", OFFER_ARGS)
    error = mcp.call_tool("createProposal", _proposal_args(offer), expect_error=True)["error"]
    assert error["code"] == "NO_ORDER"
    assert "NO_CANDIDATE" in error["details"]["reasons"]
    _check(error["details"]["noOrder"])
    assert not state.proposal_ids


def test_rest_create_proposal_no_candidate_carries_no_order_help(mcp_env):
    mcp, _, _ = mcp_env
    offer = mcp.client.post("/v0.1/offers", json=OFFER_ARGS)
    assert offer.status_code == 200, offer.text
    response = mcp.client.post("/v0.1/proposals", json=_proposal_args(offer.json()))
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "NO_ORDER"
    _check(error["details"]["noOrder"])
