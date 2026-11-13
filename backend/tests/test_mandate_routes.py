"""T21: buyer-admin mandate controls re-decide open proposals live, with no model call (synthetic data)."""

from test_api import APPROVER, api, scenario_a, scenario_b  # noqa: F401  (api is a pytest fixture)

from app.policy import M1, M2

ADMIN = "/v0.1/admin/mandates"


def _audit_events(client, **params):
    return [(e["event"], e["summary"]) for e in client.get("/v0.1/audit", params=params).json()["entries"]]


def test_raising_m2_limit_to_510_turns_b_into_auto(api):  # noqa: F811
    client, provider, _ = api
    _, created = scenario_b(client)
    pending = created.json()
    assert (pending["decision"], pending["state"]) == ("ASK", "PENDING_APPROVAL")

    changed = client.client.patch(f"{ADMIN}/{M2.customerId}", json={"autoApproveLimitEur": "510.00"})
    assert changed.status_code == 200, changed.text
    result = changed.json()
    assert result["mandate"]["version"] == M2.version + 1
    assert result["mandate"]["autoApproveLimitEur"] == "510.00"
    assert result["superseded"] == [pending["proposalId"]]
    (new,) = result["redecided"]
    assert (new["decision"], new["reasons"], new["state"]) == ("AUTO", [], "CONFIRMED_SIMULATED")
    assert new["mandateVersion"] == M2.version + 1
    assert new["offerVersion"] == pending["offerVersion"] + 1
    assert new["totals"]["exclVat"] == "506.16"

    old = client.get(f"/v0.1/proposals/{pending['proposalId']}").json()
    assert (old["state"], old["supersededBy"]) == ("SUPERSEDED", new["proposalId"])

    customer_log = _audit_events(client, customerId=M2.customerId)
    assert ("MANDATE_CHANGED", "Policy changed by buyer admin: autoApproveLimitEur €500.00 → €510.00") in customer_log
    old_log = _audit_events(client, proposalId=pending["proposalId"])
    assert any(event == "PROPOSAL_SUPERSEDED" and "changed by buyer admin" in summary for event, summary in old_log)


def test_the_old_approval_link_is_void_after_the_change(api):  # noqa: F811
    client, _, _ = api
    _, created = scenario_b(client)
    pending = created.json()
    view = client.client.get(f"{APPROVER}/proposals/{pending['proposalId']}").json()
    client.client.patch(f"{ADMIN}/{M2.customerId}", json={"autoApproveLimitEur": "510.00"})
    late = client.client.post(f"{APPROVER}/proposals/{pending['proposalId']}/decision", json={
        "offerVersion": pending["offerVersion"], "approvalToken": view["approvalToken"], "action": "APPROVE",
    })
    assert late.status_code in (403, 409, 410)


def test_lowering_a_limit_keeps_the_new_version_asking(api):  # noqa: F811
    client, _, _ = api
    _, created = scenario_b(client)
    pending = created.json()
    result = client.client.patch(f"{ADMIN}/{M2.customerId}", json={"autoApproveLimitEur": "400.00"}).json()
    (new,) = result["redecided"]
    assert (new["decision"], new["reasons"], new["state"]) == ("ASK", ["OVER_AUTO_LIMIT"], "PENDING_APPROVAL")
    assert new["proposalId"] != pending["proposalId"]


def test_confirmed_proposals_and_other_customers_are_untouched(api):  # noqa: F811
    client, _, _ = api
    _, a = scenario_a(client)
    confirmed = a.json()
    assert confirmed["state"] == "CONFIRMED_SIMULATED"
    result = client.client.patch(f"{ADMIN}/{M1.customerId}", json={"allergenMode": "STRICT"}).json()
    assert (result["superseded"], result["redecided"]) == ([], [])
    assert client.get(f"/v0.1/proposals/{confirmed['proposalId']}").json()["state"] == "CONFIRMED_SIMULATED"
    assert result["mandate"]["allergenMode"] == "STRICT"
    assert client.get(f"/v0.1/mandates/{M1.customerId}").json()["version"] == M1.version + 1


def test_invalid_changes_are_refused(api):  # noqa: F811
    client, _, _ = api
    assert client.client.patch(f"{ADMIN}/{M2.customerId}", json={}).status_code == 400
    below = client.client.patch(f"{ADMIN}/{M2.customerId}", json={"hardLimitEur": "100.00"})
    assert below.status_code == 400
    same = client.client.patch(f"{ADMIN}/{M2.customerId}", json={"autoApproveLimitEur": "500.00"})
    assert same.status_code == 400
    assert client.client.patch(f"{ADMIN}/nobody", json={"autoApproveLimitEur": "1.00"}).status_code == 404
    unknown = client.client.patch(f"{ADMIN}/{M2.customerId}", json={"version": 9})
    assert unknown.status_code == 422


def test_the_agent_channel_still_cannot_change_a_mandate(api):  # noqa: F811
    client, _, _ = api
    assert client.client.patch(f"/v0.1/mandates/{M2.customerId}", json={"autoApproveLimitEur": "510"}).status_code == 405
