"""T47: the MCP docs cite only customers that exist, and OpenAPI states the authentication model."""

import json
import re

from fastapi.testclient import TestClient
from test_mcp import mcp_env  # noqa: F401 (pytest fixture)

from app.main import app

QUOTED_ID = re.compile(r"'([a-z0-9]+(?:-[a-z0-9]+)+)'")


def _customer_docs(instructions: str, tools: list[dict]) -> list[str]:
    """Every text that names example customers: the server instructions and each customerId description."""
    texts = [instructions]
    for tool in tools:
        if "customer" in tool["description"].lower():
            texts.append(tool["description"])
        for name, prop in tool["inputSchema"].get("properties", {}).items():
            if name == "customerId":
                texts.append(prop.get("description", ""))
    return texts


def test_mcp_docs_only_cite_customer_ids_that_return_200(mcp_env):  # noqa: F811
    mcp, _, _ = mcp_env
    init = mcp.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                  "clientInfo": {"name": "t47", "version": "1"}})
    tools = mcp.rpc("tools/list")["result"]["tools"]
    texts = _customer_docs(init["result"]["instructions"], tools)
    cited = {cid for text in texts for cid in QUOTED_ID.findall(text)}
    assert cited, "the docs should name at least one demo customer"
    for customer_id in sorted(cited):
        resp = mcp.client.get(f"/v0.1/mandates/{customer_id}")
        assert resp.status_code == 200, f"documented customer {customer_id!r} returns {resp.status_code}"
    assert "helsinki-hospital-kitchen" not in json.dumps(tools) + init["result"]["instructions"]


def test_openapi_states_the_authentication_model():
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    assert schemes["agentKey"]["type"] == "http" and schemes["agentKey"]["scheme"] == "bearer"
    assert schemes["approverSso"]["type"] == "http" and schemes["approverSso"]["scheme"] == "bearer"
    assert schemes["demoAdmin"]["name"] == "X-Demo-Admin-Key"
    assert schemes["demoApprover"]["name"] == "X-Demo-Approver-Role"
    assert "Not enforced in this demo" in schemes["approverSso"]["description"]
    assert spec["security"] == [{}]  # read-only discovery is still public
    assert spec["paths"]["/v0.1/offers"]["post"]["security"] == [{"agentKey": []}]
    assert spec["paths"]["/v0.1/admin/mandates/{customerId}"]["patch"]["security"] == [{"demoAdmin": []}]
    assert "Demo access:" in spec["info"]["description"]
    # The approver channel stays off the agent channel in the generated spec (unchanged).
    assert "/v0.1/approver/proposals/{proposalId}/decision" in spec["paths"]
