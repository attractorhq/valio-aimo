"""Authenticated caller for existing domain/transport tests, not for access-control tests.

Select public demo credentials from each test's explicit buyer or its returned proposal.
The access-control regression suite deliberately uses the unmodified Starlette client.
"""

from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app.policy import M1, M2


class DemoTestClient(TestClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.proposal_customers = {}
        self.customer = M1.customerId

    def request(self, method, url, **kwargs):
        path = urlsplit(str(url)).path
        params = {**{k: v[0] for k, v in parse_qs(urlsplit(str(url)).query).items()},
                  **dict(kwargs.get("params") or {})}
        body = kwargs.get("json")
        data = body if isinstance(body, dict) else {}
        if path.rstrip("/") == "/mcp":
            data = data.get("params", {}).get("arguments", {})
        proposal_id = params.get("proposalId")
        if "/proposals/" in path:
            proposal_id = path.split("/proposals/", 1)[1].split("/", 1)[0]
        customer = data.get("customerId") or params.get("customerId") or self.proposal_customers.get(proposal_id)
        if customer in {M1.customerId, M2.customerId}:
            self.customer = customer
        else:
            customer = self.customer
        headers = dict(kwargs.get("headers") or {})
        if path.startswith("/v0.1/admin/"):
            headers.setdefault("X-Demo-Admin-Key", "demo-admin")
        elif path.startswith("/v0.1/approver/"):
            if path.endswith("/proposals") and not params.get("customerId"):
                headers.setdefault("X-Demo-Admin-Key", "demo-admin")
            elif not (headers.get("X-Approval-Token") or ".sbx1." in data.get("approvalToken", "")):
                role = M2.approver.role if customer == M2.customerId else M1.approver.role
                headers.setdefault("X-Demo-Approver-Role", role)
        elif path.startswith("/v0.1/audit") and not (params.get("proposalId") or params.get("customerId")):
            headers.setdefault("X-Demo-Admin-Key", "demo-admin")
        if path.startswith(("/v0.1/", "/mcp")):
            headers.setdefault("Authorization", f"Bearer demo-agent-{customer}")
        kwargs["headers"] = headers
        response = super().request(method, url, **kwargs)
        if "application/json" in response.headers.get("content-type", ""):
            self._remember(response.json())
        return response

    def _remember(self, value):
        if isinstance(value, dict):
            if isinstance(value.get("proposalId"), str) and isinstance(value.get("customerId"), str):
                self.proposal_customers[value["proposalId"]] = value["customerId"]
            for child in value.values():
                self._remember(child)
        elif isinstance(value, list):
            for child in value:
                self._remember(child)
