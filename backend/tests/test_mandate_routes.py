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


# --------------------------------------------------------------------------- T36: reset to the default


def test_reset_restores_the_default_as_a_new_version_and_b_asks_again(api):  # noqa: F811
    client, _, _ = api
    raised = client.client.patch(f"{ADMIN}/{M2.customerId}", json={"autoApproveLimitEur": "510.00"}).json()
    assert raised["mandate"]["version"] == 2

    view = client.client.get(f"{ADMIN}/{M2.customerId}/default").json()
    assert view["changedFromDefault"] is True
    assert view["differences"] == [{"field": "autoApproveLimitEur", "current": "510.00", "default": "500.00"}]

    reset = client.client.post(f"{ADMIN}/{M2.customerId}/reset")
    assert reset.status_code == 200, reset.text
    result = reset.json()
    assert (result["mandate"]["version"], result["mandate"]["autoApproveLimitEur"]) == (3, "500.00")
    assert result["auditEntry"]["event"] == "MANDATE_RESET"
    assert client.client.get(f"{ADMIN}/{M2.customerId}/default").json()["changedFromDefault"] is False

    _, created = scenario_b(client)
    proposal = created.json()
    assert (proposal["decision"], proposal["reasons"], proposal["mandateVersion"]) == (
        "ASK", ["OVER_AUTO_LIMIT"], 3)

    log = _audit_events(client, customerId=M2.customerId)
    assert ("MANDATE_CHANGED", "Policy changed by buyer admin: autoApproveLimitEur €500.00 → €510.00") in log
    assert ("MANDATE_RESET", "Policy reset to default by buyer admin: autoApproveLimitEur €510.00 → €500.00") in log


def test_reset_re_decides_open_proposals(api):  # noqa: F811
    client, _, _ = api
    client.client.patch(f"{ADMIN}/{M2.customerId}", json={"autoApproveLimitEur": "400.00"})
    _, created = scenario_b(client)
    pending = created.json()
    assert pending["state"] == "PENDING_APPROVAL"

    result = client.client.post(f"{ADMIN}/{M2.customerId}/reset").json()
    assert result["superseded"] == [pending["proposalId"]]
    (new,) = result["redecided"]
    assert (new["decision"], new["reasons"], new["mandateVersion"]) == ("ASK", ["OVER_AUTO_LIMIT"], 3)
    assert client.get(f"/v0.1/proposals/{pending['proposalId']}").json()["state"] == "SUPERSEDED"


def test_reset_of_several_fields_and_refusals(api):  # noqa: F811
    client, _, _ = api
    assert client.client.post(f"{ADMIN}/{M1.customerId}/reset").status_code == 400  # already at default
    assert client.client.post(f"{ADMIN}/nobody/reset").status_code == 404
    client.client.patch(f"{ADMIN}/{M1.customerId}", json={"allergenMode": "STRICT", "hardLimitEur": "6000.00"})
    view = client.client.get(f"{ADMIN}/{M1.customerId}/default").json()
    assert {d["field"] for d in view["differences"]} == {"allergenMode", "hardLimitEur"}
    result = client.client.post(f"{ADMIN}/{M1.customerId}/reset").json()
    assert (result["mandate"]["allergenMode"], result["mandate"]["hardLimitEur"]) == ("STANDARD", "15000.00")
    assert result["mandate"]["version"] == 3


def test_the_agent_channel_cannot_reset(api):  # noqa: F811
    client, _, _ = api
    assert client.client.post(f"/v0.1/mandates/{M2.customerId}/reset").status_code in (404, 405)


def test_t68_expired_mandate_blocks_open_proposals_until_renewed(api):  # noqa: F811
    client, _, _ = api
    _, created = scenario_b(client)
    pending = created.json()
    expired = client.client.patch(f"{ADMIN}/{M2.customerId}", json={"validTo": "2026-01-31"})
    assert expired.status_code == 200, expired.text
    assert expired.json()["mandate"]["validTo"] == "2026-01-31"
    (new,) = expired.json()["redecided"]
    # The expired mandate is the first reason; the trade-off it also carries can no longer be signed off.
    assert (new["decision"], new["state"]) == ("BLOCK", "BLOCKED")
    assert new["reasons"] == ["MANDATE_NOT_VALID", "OVER_AUTO_LIMIT"]
    assert new["approverRole"] is None

    renewed = client.client.patch(f"{ADMIN}/{M2.customerId}", json={"validTo": None})
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["mandate"]["validTo"] is None
    assert ("MANDATE_CHANGED", "Policy changed by buyer admin: validTo Sat 31 Jan → none") in _audit_events(
        client, customerId=M2.customerId)
    assert pending["proposalId"] in expired.json()["superseded"]


def test_t68_allowed_categories_change_and_bad_window(api):  # noqa: F811
    client, _, _ = api
    changed = client.client.patch(f"{ADMIN}/{M1.customerId}", json={"allowedCategories": [" 211 ", "", "211"]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["mandate"]["allowedCategories"] == ["211"]
    bad = client.client.patch(f"{ADMIN}/{M1.customerId}", json={"validFrom": "2026-12-02", "validTo": "2026-12-01"})
    assert bad.status_code == 400
    default = client.client.get(f"{ADMIN}/{M1.customerId}/default").json()
    assert default["differences"] == [{"field": "allowedCategories", "current": ["211"], "default": []}]
