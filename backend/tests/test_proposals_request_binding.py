"""T79: proposal request items truthfully describe the offer (synthetic records only)."""

from decimal import Decimal

import pytest
from test_api import api as api
from test_api import item, offer_and_propose
from test_proposals_gateway import MONDAY

from app.contracts import CreateProposalRequest
from app.gateway.proposals import ProposalRequestMismatchError
from app.policy import M1


def quote(client, *, quantity="10", product_id="SYN-MILK", unit="l", extra=()):
    response = client.post("/v0.1/offers", json={
        "customerId": M1.customerId, "requestedDate": MONDAY.isoformat(),
        "items": [{"itemId": "I1", "productId": product_id, "quantity": quantity, "unit": unit}, *extra],
    })
    assert response.status_code == 200, response.text
    return response.json()


def proposal_body(offer, items):
    return {
        "customerId": M1.customerId, "offerId": offer["offerId"], "offerVersion": offer["offerVersion"],
        "request": {"requestText": "Synthetic purchasing request.", "requestedDate": MONDAY.isoformat(),
                    "items": items, "assumptions": []},
    }


def test_ten_litres_cannot_bind_to_five_thousand_litres(api):
    client, provider, state = api
    offer = quote(client, quantity="5000")
    body = proposal_body(offer, [item("I1", "milk drink", "10", "l")])
    entries_before = state.gateway.log.entries()
    # Enforced at the domain boundary too, not just by the REST adapter.
    with pytest.raises(ProposalRequestMismatchError, match="item I1 requests 10 l; offer line L1 is 5,000 l"):
        state.gateway.create_proposal(CreateProposalRequest.model_validate(body))
    for _ in range(2):
        response = client.post("/v0.1/proposals", json=body)
        assert response.status_code == 422, response.text
        assert response.json()["error"] == {
            "code": "HTTP_ERROR",
            "message": "item I1 requests 10 l; offer line L1 is 5,000 l; exceeds sales-unit rounding",
        }
    assert state.gateway.log.entries() == entries_before
    assert not state.gateway._proposals
    assert len(provider.calls) == 1  # No confirmation/recheck, approval issuance or proposal binding.
    # A rejected create did not consume the offer: its truthful request can still be proposed.
    corrected = client.post("/v0.1/proposals", json=proposal_body(offer, [item("I1", "milk drink", "5000", "l")]))
    assert corrected.status_code == 201, corrected.text


@pytest.mark.parametrize("product_id,what,requested,unit,delivered", [
    ("SYN-MILK", "milk drink", "10", "l", "10"),
    ("SYN-MILK", "milk drink", "10.2", "l", "11"),
    ("SYN-PASTA", "pasta", "12", "kg", "15"),  # +25 %: still AUTO (T100 asks above that)
])
def test_matching_and_whole_sales_unit_rounding_remain_confirmable(api, product_id, what, requested, unit, delivered):
    client, _, _ = api
    _, created = offer_and_propose(
        client, M1.customerId, [("I1", product_id, requested, unit)],
        "Synthetic purchasing request.", [item("I1", what, requested, unit)],
    )
    assert created.status_code == 201, created.text
    proposal = created.json()
    assert (proposal["decision"], proposal["state"]) == ("AUTO", "CONFIRMED_SIMULATED")
    assert Decimal(proposal["lines"][0]["deliveredQuantity"]["value"]) == Decimal(delivered)


@pytest.mark.parametrize("request_items,message", [
    ([item("I1", "milk drink", "12", "l")], "delivered quantity is below the request"),
    ([item("I1", "milk drink", "10", "kg")], "units do not match"),
    ([item("I1", "milk drink", "10", "l")] * 2, "request itemIds must be unique"),
    ([item("I2", "milk drink", "10", "l")], "item I1, which is missing from request items"),
    ([item("I1", "milk drink", "10", "l"), item("I2", "pasta", "5", "kg")], "item I2 has no matching offer line"),
])
def test_invalid_item_bindings_return_422_before_creating_a_proposal(api, request_items, message):
    client, provider, state = api
    offered = quote(client)
    response = client.post("/v0.1/proposals", json=proposal_body(offered, request_items))
    assert response.status_code == 422, response.text
    assert message in response.json()["error"]["message"]
    assert not state.gateway._proposals
    assert len(provider.calls) == 1


def test_extra_offered_item_cannot_be_hidden_by_omitting_it_from_request(api):
    client, _, _ = api
    offered = quote(client, extra=[{"itemId": "I2", "productId": "SYN-OAT", "quantity": "20", "unit": "l"}])
    response = client.post("/v0.1/proposals", json=proposal_body(offered, [item("I1", "milk drink", "10", "l")]))
    assert response.status_code == 422, response.text
    assert "offer line L2 refers to item I2" in response.json()["error"]["message"]


@pytest.mark.parametrize("requested,status", [("120.2", 201), ("10", 422), ("150", 422)])
def test_split_lines_are_checked_as_combined_coverage(api, requested, status):
    client, provider, state = api
    # Two synthetic pasta products share an item: 95 kg from A, rounded top-up from B.
    primary = state.gateway.products["SYN-PASTA"]
    substitute = primary.model_copy(update={"productId": "SYN-TOP-UP"})
    state.gateway.products[substitute.productId] = substitute
    provider.prices[substitute.productId] = {**provider.prices[primary.productId], "productId": substitute.productId}
    provider.stocks[substitute.productId] = {
        **provider.stocks[primary.productId], "productId": substitute.productId,
    }
    offered = quote(client, product_id=primary.productId, quantity="120.2", unit="kg", extra=[{
        "itemId": "I1", "productId": substitute.productId, "quantity": "120.2", "unit": "kg",
    }])
    assert len(offered["lines"]) == 2
    assert sum(Decimal(line["deliveredQuantity"]["value"]) for line in offered["lines"]) == Decimal("125")
    response = client.post("/v0.1/proposals", json=proposal_body(offered, [item("I1", "pasta", requested, "kg")]))
    assert response.status_code == status, response.text
    if status == 201:
        # T100: the top-up is the same product type at the same price, so the split needs no human.
        assert response.json()["state"] == "CONFIRMED_SIMULATED"
        assert response.json()["reasons"] == ["SPLIT"]
    else:
        assert "offer line L1 is 95 kg + offer line L2 is 30 kg" in response.json()["error"]["message"]


@pytest.mark.parametrize("requested,status", [("120", 201), ("200", 422), ("10", 422)])
def test_explicit_partial_offer_keeps_approval_but_cannot_describe_a_different_need(api, requested, status):
    client, _, _ = api
    offered = quote(client, product_id="SYN-PASTA", quantity="120", unit="kg")
    assert offered["lines"][0]["deliveredQuantity"]["value"] == "95"
    response = client.post("/v0.1/proposals", json=proposal_body(offered, [item("I1", "pasta", requested, "kg")]))
    assert response.status_code == status, response.text
    if status == 201:
        assert response.json()["state"] == "PENDING_APPROVAL"
        assert "PARTIAL_AVAILABILITY" in response.json()["reasons"]


def test_named_product_change_remains_an_explicit_substitution_requiring_approval(api):
    client, _, _ = api
    offered = quote(client, product_id="SYN-OAT")
    requested = {**item("I1", "milk drink", "10", "l"), "productId": "SYN-MILK"}
    response = client.post("/v0.1/proposals", json=proposal_body(offered, [requested]))
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert proposal["state"] == "PENDING_APPROVAL"
    assert "AGENT_SUBSTITUTE" in proposal["reasons"]
    assert proposal["lines"][0]["substitutionSource"] == "AGENT"
    assert proposal["lines"][0]["substituteFor"] == "SYN-MILK"
    view = client.client.get(f"/v0.1/approver/proposals/{proposal['proposalId']}").json()
    approved = client.client.post(f"/v0.1/approver/proposals/{proposal['proposalId']}/decision", json={
        "offerVersion": proposal["offerVersion"], "approvalToken": view["approvalToken"], "action": "APPROVE",
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "CONFIRMED_SIMULATED"
