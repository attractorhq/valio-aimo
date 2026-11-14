"""Verified or Ask · Trusted Trade Gateway v0.1 (FastAPI, OpenAPI 3.1).

Two channels on one origin:
- Agent channel `/v0.1/*`: searchProducts, getOffer, createProposal, getProposal, getAuditTrail, getMandate.
- Approver channel `/v0.1/approver/*`: the approver view and decideApproval. Never an agent tool.
`recheckAndConfirm` has no route: the server runs it before every simulated confirmation.
"""

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
        "**Simulated order — mock environment**. No order is sent to Valio."
    ),
)
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


@app.get("/health", tags=["Operations"], operation_id="health")
def health(state: Annotated[GatewayState | GatewayUnavailable, Depends(try_state)]) -> dict:
    """Liveness plus whether the gateway can serve orders (catalogue + live key or recorded fixtures)."""
    if isinstance(state, GatewayUnavailable):
        return {"status": "degraded", "version": VERSION, "ready": False, "reason": str(state)}
    return {
        "status": "ok",
        "version": VERSION,
        "ready": True,
        "mode": state.mode,
        "catalogProducts": len(state.catalog),
    }
