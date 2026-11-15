"""Aimo Connect · Trusted Trade Gateway v0.1 (FastAPI, OpenAPI 3.1).

Two channels on one origin:
- Agent channel `/v0.1/*`: searchProducts, getOffer, createProposal, getProposal, getAuditTrail, getMandate.
- Approver channel `/v0.1/approver/*`: the approver view and decideApproval. Never an agent tool.
`recheckAndConfirm` has no route: the server runs it before every simulated confirmation.
"""

import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import GatewayState, GatewayUnavailable, agent_router, approver_router, register_error_handlers
from app.api.log_routes import router as log_router
from app.api.mandate_routes import router as mandate_router
from app.api.mcp import router as mcp_router
from app.api.state import SANDBOX_COOKIE, SANDBOX_ID_RE, sandbox_count, try_state
from app.config import get_settings
from app.contracts import VERSION
from app.gateway.signing import GatewaySigner

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
    title="Aimo Connect · Trusted Trade Gateway",
    version=VERSION,
    openapi_tags=TAGS,
    description=(
        "Agent Trust Labels v0.1 gateway for Valio Aimo. The agent may act alone only when every fact it relies "
        "on is strong enough and the order is inside the buyer's mandate; everything else goes to a named human. "
        "Money is EUR excl. VAT as decimal strings. Every order is simulated: "
        "**Simulated order — mock environment**. No order is sent to Valio.\n\n"
        "**Demo access:** customer-scoped Bearer agent keys, an explicit approver-role header, and "
        "X-Demo-Admin-Key for admin writes/global audit. These public demo credentials are not production "
        "identity; production requires scoped secrets and organisation SSO (OAuth 2.0 / OIDC).\n\n"
        "**Sessions:** every API client works in its own private session. A call without a session gets a new one, "
        "returned in the `X-Demo-Sandbox` response header: send it back as `X-Demo-Sandbox` on every later call "
        "(an offer and its proposal must be in the same session). MCP clients use their `Mcp-Session-Id`; send it "
        "as `X-Demo-Sandbox` to continue the same session over REST. Totals, proposals and suspensions never "
        "cross sessions."
    ),
)
# Public demo credentials illustrate scope; production replaces them with authenticated identities.
SECURITY_SCHEMES = {
    "agentKey": {
        "type": "http", "scheme": "bearer",
        "description": "Enforced demo key: demo-agent-<customerId>. Binds agent writes, MCP and audit reads "
                       "to that buyer; GET /v0.1/audit/verify with only this key verifies that buyer's own "
                       "chain. Public demo keys are not production secrets.",
    },
    "demoAdmin": {"type": "apiKey", "in": "header", "name": "X-Demo-Admin-Key",
                  "description": "Public demo key: demo-admin. Required for admin writes and the global audit "
                                 "(full-log verify)."},
    "demoApprover": {"type": "apiKey", "in": "header", "name": "X-Demo-Approver-Role",
                     "description": "Demo sign-in: Head Chef or Purchasing Manager. No password. "
                                    "A signed single-use phone capability carries its issued role."},
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
    """Describe the access gates actually enforced by the demo, per operation."""
    schema = FastAPI.openapi(app)  # cached on app.openapi_schema after the first call
    schema.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
    schema["security"] = [{}]  # catalogue/mandate/proposal read-only discovery remains public
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if path.startswith("/mcp") or (path.startswith("/v0.1/") and method == "post"
                                           and not path.startswith(("/v0.1/admin", "/v0.1/approver"))):
                operation["security"] = [{"agentKey": []}]
            elif path.startswith("/v0.1/admin") and method in {"patch", "post"}:
                operation["security"] = [{"demoAdmin": []}]
            elif path.startswith("/v0.1/approver"):
                operation["security"] = [{"demoApprover": []}]
                if path == "/v0.1/approver/proposals":
                    operation["security"].append({"demoAdmin": []})
            elif path.startswith("/v0.1/audit"):
                operation["security"] = [{"agentKey": []}, {"demoAdmin": []}]
            elif path == "/v0.1/proposals":
                operation["security"] = [{"agentKey": []}]
    return schema


app.openapi = openapi_with_security  # type: ignore[method-assign]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().frontend_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Demo-Sandbox", "X-Approval-Token", "Mcp-Session-Id", "Authorization",
                   "X-Demo-Admin-Key", "X-Demo-Approver-Role"],
    expose_headers=["X-Demo-Sandbox", "Mcp-Session-Id", "X-Agent-Warning"],
)


@app.middleware("http")
async def demo_session_cookie(request: Request, call_next):
    cookie = request.cookies.get(SANDBOX_COOKIE, "")
    # Only a real browser gets a cookie session. Browsers send Sec-Fetch-Site; Node/undici `fetch` sends only
    # Sec-Fetch-Mode and keeps no cookies, so treating it as a browser gave every API call a fresh session and an
    # external agent's offer was "unknown" to its next proposal call (judge 20261114-195211).
    browser = bool(request.headers.get("sec-fetch-site")) or "text/html" in request.headers.get("accept", "")
    browser_cookie = browser and request.headers.get("x-demo-sandbox") is None
    new_cookie = browser_cookie and not SANDBOX_ID_RE.fullmatch(cookie)
    if new_cookie:
        request.state.demo_sandbox = secrets.token_hex(16)
    response = await call_next(request)
    if browser_cookie:
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0] == "https"
        # Renew the browser cookie without rotating its id: expiry follows inactivity, not creation time.
        session_id = request.state.demo_sandbox if new_cookie else cookie
        response.set_cookie(SANDBOX_COOKIE, session_id, httponly=True, samesite="lax", secure=https,
                            max_age=6 * 60 * 60)
    if request.url.path.startswith(("/v0.1/", "/mcp", "/health", "/.well-known/")):
        response.headers["Cache-Control"] = "no-store"
    # T116: a cookieless client learns its private session id and echoes it to stay in it. Never sent for a browser
    # cookie session (the cookie is httpOnly; the header would expose it to scripts).
    if (session := getattr(request.state, "response_sandbox", None)) and "x-demo-sandbox" not in response.headers:
        response.headers["X-Demo-Sandbox"] = session
    # T111: the refusal before the suspending one carries a warning, also on error responses.
    if (warning := getattr(request.state, "agent_warning", None)) and "x-agent-warning" not in response.headers:
        response.headers["X-Agent-Warning"] = warning
    return response


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
        return {"status": "degraded", "version": VERSION, "build": build_sha(), "ready": False, "reason": str(state),
                "sandboxes": sandbox_count()}
    return {
        "status": "ok",
        "version": VERSION,
        "build": build_sha(),
        "ready": True,
        "mode": state.mode,
        "catalogProducts": len(state.catalog),
        "sandboxes": sandbox_count(),
    }


def _origin(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip() or request.url.scheme
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip() or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


@app.get("/.well-known/aimo-connect-keys.json", tags=["Operations"], operation_id="getSigningKeys")
def get_signing_keys(state: Annotated[GatewayState | GatewayUnavailable, Depends(try_state)]) -> dict:
    """RFC 8037 JWK Set publishing the Ed25519 public key used to sign offers, decisions and confirmations."""
    signer = GatewaySigner() if isinstance(state, GatewayUnavailable) else state.gateway.signer
    return signer.jwks()


@app.get("/.well-known/aimo-connect.json", tags=["Operations"], operation_id="getDiscovery")
def get_discovery(
    request: Request,
    state: Annotated[GatewayState | GatewayUnavailable, Depends(try_state)],
) -> dict:
    """Gateway service discovery pointing to OpenAPI, MCP, the open specification page and the JWK Set."""
    base = _origin(request)
    signer = GatewaySigner() if isinstance(state, GatewayUnavailable) else state.gateway.signer
    return {
        "name": "Aimo Connect · Trusted Trade Gateway",
        "version": VERSION,
        "openapi": f"{base}/openapi.json",
        "docs": f"{base}/docs",
        "mcp": f"{base}/mcp",
        "spec": f"{base}/spec",
        "jwks": f"{base}/.well-known/aimo-connect-keys.json",
        "kid": signer.kid,
        "signingAlgorithm": "EdDSA",
    }
