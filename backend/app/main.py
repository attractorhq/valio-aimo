"""Verified or Ask · Trusted Trade Gateway v0.1 (FastAPI, OpenAPI 3.1).

Two channels on one origin:
- Agent channel `/v0.1/*`: searchProducts, getOffer, createProposal, getProposal, getAuditTrail, getMandate.
- Approver channel `/v0.1/approver/*`: the approver view and decideApproval. Never an agent tool.
`recheckAndConfirm` has no route: the server runs it before every simulated confirmation.
"""

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import GatewayState, GatewayUnavailable, agent_router, approver_router, register_error_handlers
from app.api.log_routes import router as log_router
from app.api.mandate_routes import router as mandate_router
from app.api.mcp import router as mcp_router
from app.api.state import try_state
from app.config import get_settings
from app.contracts import VERSION

TAGS = [
    {"name": "Agent channel",
     "description": "Operations any purchasing agent may call. The agent cannot choose an outcome, approve or "
                    "confirm; agent responses never contain an approval token, URL or QR payload."},
    {"name": "Approver channel",
     "description": "The named human's sign-off on one exact proposal version (single-use demo link). "
                    "Never exposed as an agent or MCP tool."},
    {"name": "Buyer admin",
     "description": "A human buyer admin changes the mandate (limits, allergen mode). Each change bumps the "
                    "mandate version, is audited, and re-decides open proposals live. Never an agent tool."},
    {"name": "Operations", "description": "Health and service metadata."},
]

app = FastAPI(
    title="Verified or Ask · Trusted Trade Gateway",
    version=VERSION,
    openapi_tags=TAGS,
    description=(
        "Agent Trust Labels v0.1 gateway for Valio Aimo. The agent may act alone only when every fact it relies "
        "on is strong enough and the order is inside the buyer's mandate; everything else goes to a named human. "
        "Money is EUR excl. VAT as decimal strings. Every order is simulated: "
        "**Simulated order — mock environment**. No order is sent to Valio.\n\n"
        "**Authentication:** none in this demo. `components.securitySchemes` documents the production plan: "
        "scoped agent keys for the agent channel, and organisation SSO (OAuth 2.0 / OIDC) for approvers and "
        "buyer admins."
    ),
)
# Authentication model (T47). The demo enforces none: every route is open, and an approval needs the
# single-use approval token from the Approver panel. The schemes below document the production plan.
SECURITY_SCHEMES = {
    "agentKey": {
        "type": "apiKey",
        "in": "header",
        "name": "X-Agent-Key",
        "description": "Production plan: a scoped API key per purchasing agent and buyer. It covers the agent "
                       "channel only (searchProducts, getOffer, createProposal, getProposal, getAuditTrail, "
                       "getMandate) and can never reach the approver channel or buyer admin. "
                       "Not enforced in this demo.",
    },
    "approverSso": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Production plan: approvers and buyer admins sign in with their organisation's SSO "
                       "(OAuth 2.0 / OpenID Connect). decideApproval would need this token as well as the "
                       "single-use approval token for the exact proposal version. Not enforced in this demo.",
    },
}


def openapi_with_security() -> dict:
    """The generated spec plus `components.securitySchemes`, and an explicit no-auth `security` for the demo."""
    schema = FastAPI.openapi(app)  # cached on app.openapi_schema after the first call
    schema.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
    schema["security"] = [{}]  # demo: no authentication required on any operation
    return schema


app.openapi = openapi_with_security  # type: ignore[method-assign]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().frontend_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "PATCH"],  # PATCH: buyer-admin mandate change (T21) from the dev UI
    allow_headers=["Content-Type"],
)
register_error_handlers(app)
app.include_router(agent_router)
app.include_router(approver_router)
app.include_router(mandate_router)
app.include_router(log_router)
app.include_router(mcp_router)


# T48: the git commit baked into the image (BUILD_SHA build arg), or the file scripts/deploy writes into
# the gitignored data/catalog/ that the image copies. "dev" for a local run.
BUILD_SHA_FILE = Path(__file__).resolve().parents[2] / "data" / "catalog" / "BUILD_SHA"


def build_sha() -> str:
    env = os.environ.get("BUILD_SHA", "").strip()
    if env and env != "unknown":
        return env
    try:
        return BUILD_SHA_FILE.read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"


@app.get("/health", tags=["Operations"], operation_id="health")
def health(state: Annotated[GatewayState | GatewayUnavailable, Depends(try_state)]) -> dict:
    """Liveness plus whether the gateway can serve orders (catalogue + live key or recorded fixtures)."""
    if isinstance(state, GatewayUnavailable):
        return {"status": "degraded", "version": VERSION, "build": build_sha(), "ready": False, "reason": str(state)}
    return {
        "status": "ok",
        "version": VERSION,
        "build": build_sha(),
        "ready": True,
        "mode": state.mode,
        "catalogProducts": len(state.catalog),
    }
