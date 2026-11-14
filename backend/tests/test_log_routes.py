"""T30: decision-log export as JSON and CSV (synthetic data)."""

import csv
import io

from test_api import APPROVER, api, scenario_b  # noqa: F401  (api is a pytest fixture)

from app.api.log_routes import _cell
from app.policy import M2

EXPORT = "/v0.1/audit/export"


def _approve_b(client):
    _, created = scenario_b(client)
    proposal = created.json()
    view = client.client.get(f"{APPROVER}/proposals/{proposal['proposalId']}").json()
    decided = client.client.post(f"{APPROVER}/proposals/{proposal['proposalId']}/decision", json={
        "offerVersion": proposal["offerVersion"], "approvalToken": view["approvalToken"], "action": "APPROVE",
        "comment": "=HYPERLINK(\"http://example.invalid\") fine for the school",
    })
    assert decided.status_code == 200, decided.text
    return proposal["proposalId"], view["approvalToken"]


def _transitions(entries):
    return [(e["fromState"], e["toState"]) for e in entries if e["toState"]]


def test_scenario_b_json_export_contains_every_transition(api):  # noqa: F811
    client, _, _ = api
    proposal_id, token = _approve_b(client)
    trail = client.get("/v0.1/audit", params={"proposalId": proposal_id}).json()["entries"]

    response = client.client.get(EXPORT, params={"proposalId": proposal_id, "format": "json"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="decision-log-proposal-{proposal_id}.json"'
    )
    exported = response.json()["entries"]
    assert exported == trail
    states = [t for _, t in _transitions(exported)]
    for state in ("OFFERED", "PROPOSED", "PENDING_APPROVAL", "APPROVED", "RECHECKED", "CONFIRMED_SIMULATED"):
        assert state in states, state
    assert token not in response.text  # P3: the approval capability is never in the log


def test_scenario_b_csv_export_has_one_row_per_entry_and_neutralises_formulas(api):  # noqa: F811
    client, _, _ = api
    proposal_id, token = _approve_b(client)
    trail = client.get("/v0.1/audit", params={"proposalId": proposal_id}).json()["entries"]

    response = client.client.get(EXPORT, params={"proposalId": proposal_id, "format": "csv"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].endswith(f'proposal-{proposal_id}.csv"')
    body = response.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(body)))
    assert [r["entryId"] for r in rows] == [e["entryId"] for e in trail]
    assert [(r["fromState"] or None, r["toState"] or None) for r in rows if r["toState"]] == _transitions(trail)
    assert any(r["reasons"] == "OVER_AUTO_LIMIT" for r in rows)
    assert any("€506.16" in r["summary"] for r in rows)
    comments = [r["comment"] for r in rows if r["comment"]]
    assert comments and all(c.startswith("'=") for c in comments)
    assert token not in body


def test_customer_export_and_bad_requests(api):  # noqa: F811
    client, _, _ = api
    _approve_b(client)
    response = client.client.get(EXPORT, params={"customerId": M2.customerId, "format": "csv"})
    assert response.status_code == 200
    assert f"customer-{M2.customerId}.csv" in response.headers["content-disposition"]
    assert len(list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))) >= 6
    assert client.client.get(EXPORT).status_code == 400
    both = client.client.get(EXPORT, params={"proposalId": "x", "customerId": M2.customerId})
    assert both.status_code == 400
    assert client.client.get(EXPORT, params={"customerId": M2.customerId, "format": "xml"}).status_code == 422


def test_csv_cell_escaping():
    assert _cell(None) == ""
    assert _cell(3) == "3"
    assert _cell("Proposal created") == "Proposal created"
    for dangerous in ("=1+1", "+cmd", "-2", "@SUM(A1)", "\tx"):
        assert _cell(dangerous) == "'" + dangerous
