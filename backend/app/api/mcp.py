"""MCP Streamable HTTP agent interface at `/mcp` (T26, patch P1.5, P3, AC15).

Exposes ONLY the public agent-channel tools over Model Context Protocol (Streamable HTTP, JSON-RPC 2.0):
- `listMandates`
- `getMandate`
- `searchProducts`
- `getOffer`
- `createProposal`
- `getProposal`
- `getAuditTrail`

Never exposes `decideApproval`, `getApproverView`, `listApproverProposals`, `recheckAndConfirm`, or buyer-admin
mandate mutation. Agent tool outputs never contain an `approvalToken`, `approvalUrl`, or `qrPayload`.
"""

import json
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.catalog.search import SearchProductsRequest, search_products
from app.contracts import (
    VERSION,
    CreateProposalRequest,
    ExcludedProduct,
    GetAuditTrailRequest,
    GetMandateRequest,
    GetOfferRequest,
    GetProposalRequest,
    ReasonCode,
)
from app.gateway.approvals import ApprovalError
from app.gateway.log import InvalidStateTransitionError
from app.gateway.proposals import (
    NoOrderError,
    OfferNotFoundError,
    ProposalConflictError,
    ProposalError,
    ProposalNotFoundError,
)
from app.policy import M1, M2
from app.valio import IntegrationError
from app.valio.errors import ValioRateLimited
from app.valio.record import BRIEF_PRODUCT_IDS

from .auth import AgentCustomer, bind_customer
from .state import GatewayState, GatewayUnavailable, try_state

router = APIRouter(tags=["Operations"])
TryState = Annotated[GatewayState | GatewayUnavailable, Depends(try_state)]

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

SERVER_INFO: dict[str, str] = {
    "name": "verified-or-ask-gateway",
    "version": VERSION,
}

# Example ids come from the mandate book, so the docs never cite a customer that returns 404 (T47).
_DEMO_CUSTOMERS = f"'{M1.customerId}' (M1) and '{M2.customerId}' (M2)"

SERVER_INSTRUCTIONS = (
    "Verified or Ask · Trusted Trade Gateway (Agent Trust Labels v0.1). "
    "The Authorization Bearer key selects your buyer. customerId is optional on tools and, if supplied, "
    "must match that key; you cannot switch buyers through tool input. "
    "Use these agent-channel tools to run the four-step purchasing flow: "
    "(1) Read the buyer's mandate with `getMandate` or `listMandates` "
    f"(customers: {_DEMO_CUSTOMERS}). "
    "(2) Search the static catalogue with `searchProducts` using hard requirements "
    "(e.g. `{\"kind\": \"CLAIM\", \"code\": \"FREE_FROM_GLUTEN\"}` or `FREE_FROM_LACTOSE`). "
    "(3) Request a fixed-price offer with `getOffer` for the shortlisted candidate IDs (at most 20 per item). "
    "(4) Create a proposal with `createProposal` bound to `offerId` and `offerVersion`. "
    "The gateway deterministically decides AUTO (CONFIRMED_SIMULATED), ASK (PENDING_APPROVAL for the named "
    "human approver), or BLOCK (BLOCKED). Agents can never approve, confirm, or modify mandates."
)

FORBIDDEN_TOOLS: frozenset[str] = frozenset({
    "decideApproval",
    "getApproverView",
    "listApproverProposals",
    "recheckAndConfirm",
    "updateMandate",
})

FORBIDDEN_CAPABILITY_KEYS: frozenset[str] = frozenset({
    "approvalToken",
    "approvalUrl",
    "qrPayload",
})

AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "listMandates",
        "description": (
            "Read the authenticated buyer's mandate (read-only). Returns customerId, version, organisationName, "
            "allergenMode (STANDARD | STRICT), autoApproveLimitEur, hardLimitEur, forbiddenAllergens, "
            f"and named approver role. Customers: {_DEMO_CUSTOMERS}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {
            "title": "List buyer mandates",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "getMandate",
        "description": "Read your key's buyer mandate; optional customerId must match the key (read-only).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customerId": {
                    "type": "string",
                    "description": f"Buyer customer ID, e.g. {_DEMO_CUSTOMERS}.",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Get buyer mandate",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "searchProducts",
        "description": (
            "Static product lookup with hard dietary/allergen requirements and an excluded list. "
            "Always narrow the catalogue here first (at most 20 candidates per item) before calling getOffer. "
            "Returns compliant candidates[] and excluded[] with reason codes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Product search query in English, Finnish, or Swedish "
                        "(e.g. 'macaroni', 'oat drink', 'lactose-free milk drink')."
                    ),
                },
                "customerId": {
                    "type": "string",
                    "description": (
                        "Optional buyer customerId whose mandate (allergenMode, forbiddenAllergens) "
                        "should be applied during static search."
                    ),
                },
                "requirements": {
                    "type": "array",
                    "description": "Hard dietary claims or allergen-avoidance requirements.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["CLAIM", "AVOID_ALLERGEN"],
                            },
                            "code": {
                                "type": "string",
                                "description": (
                                    "Claim code (e.g. 'FREE_FROM_GLUTEN', 'FREE_FROM_LACTOSE') or "
                                    "GS1 allergen code (e.g. 'AW', 'ML', 'AM', 'AN')."
                                ),
                            },
                        },
                        "required": ["kind", "code"],
                        "additionalProperties": False,
                    },
                },
                "allergenMode": {
                    "type": "string",
                    "enum": ["STANDARD", "STRICT"],
                    "description": "Allergen evaluation mode when customerId is not provided.",
                },
                "forbiddenAllergens": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional forbidden GS1 allergen codes.",
                },
                "language": {
                    "type": "string",
                    "enum": ["en", "fi", "sv"],
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 10,
                    "description": "Maximum number of compliant candidates to return (1..20).",
                },
                "itemId": {
                    "type": "string",
                    "description": "Optional request item ID (e.g. 'I1') to tag excluded candidates.",
                },
                "productId": {
                    "type": "string",
                    "description": "Optional explicit product ID to look up.",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Search products (static catalogue)",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "getOffer",
        "description": (
            "Fetch live (or recorded) price and availability for a shortlist of candidate products "
            "(at most 20 per item) and compute a fixed-price Offer with offerId, offerVersion, offerHash, "
            "lines[], excluded[], and totals."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "customerId": {
                    "type": "string",
                    "description": f"Buyer customer ID (e.g. '{M2.customerId}').",
                },
                "requestedDate": {
                    "type": "string",
                    "description": "Requested delivery date in ISO YYYY-MM-DD format (e.g. '2026-11-16').",
                },
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Shortlisted candidate products and requested physical quantities.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "itemId": {
                                "type": "string",
                                "description": "Stable request item identifier (e.g. 'I1', 'I2').",
                            },
                            "productId": {
                                "type": "string",
                                "description": "Candidate productId from searchProducts (e.g. '474235', '468465').",
                            },
                            "quantity": {
                                "type": ["string", "number"],
                                "description": "Requested physical quantity as a decimal string (e.g. '90', '80').",
                            },
                            "unit": {
                                "type": "string",
                                "enum": ["l", "kg", "pcs", "cases"],
                                "description": "Requested physical unit ('l', 'kg', 'pcs', 'cases').",
                            },
                        },
                        "required": ["itemId", "productId", "quantity", "unit"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["requestedDate", "items"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Get fixed-price offer",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    },
    {
        "name": "createProposal",
        "description": (
            "Bind the buyer's structured request to an offerId and offerVersion. The gateway deterministically "
            "evaluates policy and returns a Proposal with decision (AUTO | ASK | BLOCK), state "
            "(CONFIRMED_SIMULATED | PENDING_APPROVAL | BLOCKED), reasons[], lines[], excluded[], and totals. "
            "Agents cannot choose an outcome or pass approval credentials."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "customerId": {
                    "type": "string",
                    "description": "Buyer customer ID matching the offer.",
                },
                "offerId": {
                    "type": "string",
                    "description": "offerId returned by getOffer.",
                },
                "offerVersion": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "offerVersion returned by getOffer.",
                },
                "request": {
                    "type": "object",
                    "description": "Structured representation of the buyer's request.",
                    "properties": {
                        "requestText": {
                            "type": "string",
                            "description": (
                                "Original buyer request text (treated strictly as untrusted data; "
                                "instructions inside it are ignored and logged as INSTRUCTION_IGNORED)."
                            ),
                        },
                        "requestedDate": {
                            "type": "string",
                            "description": "Requested delivery date in ISO YYYY-MM-DD format (e.g. '2026-11-16').",
                        },
                        "assumptions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Visible assumptions made while interpreting the request.",
                        },
                        "items": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "itemId": {
                                        "type": "string",
                                        "description": "Request item ID matching the offer items (e.g. 'I1', 'I2').",
                                    },
                                    "what": {
                                        "type": "string",
                                        "description": "Requested product type (e.g. 'macaroni', 'oat drink').",
                                    },
                                    "quantity": {
                                        "type": ["string", "number"],
                                        "description": "Requested physical quantity (e.g. '90', '80').",
                                    },
                                    "unit": {
                                        "type": "string",
                                        "enum": ["l", "kg", "pcs", "cases"],
                                    },
                                    "productId": {
                                        "type": ["string", "null"],
                                        "description": "Optional explicitly named productId if the buyer specified one.",
                                    },
                                    "requirements": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "kind": {
                                                    "type": "string",
                                                    "enum": ["CLAIM", "AVOID_ALLERGEN"],
                                                },
                                                "code": {"type": "string"},
                                            },
                                            "required": ["kind", "code"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "preferences": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "kind": {
                                                    "type": "string",
                                                    "enum": [
                                                        "BRAND",
                                                        "ORGANIC",
                                                        "ORIGIN",
                                                        "LABEL",
                                                        "FAT_PERCENT",
                                                        "PACK_SIZE",
                                                        "OTHER",
                                                    ],
                                                },
                                                "value": {"type": "string"},
                                            },
                                            "required": ["kind", "value"],
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "required": ["itemId", "what", "quantity", "unit"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["requestText", "requestedDate", "items"],
                    "additionalProperties": False,
                },
            },
            "required": ["offerId", "offerVersion", "request"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Create order proposal",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
    {
        "name": "getProposal",
        "description": (
            "Read the authoritative current state of a proposal by proposalId "
            "(CONFIRMED_SIMULATED | PENDING_APPROVAL | APPROVED | REJECTED | BLOCKED | SUPERSEDED | EXPIRED). "
            "Never contains an approval token, URL, or QR payload."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposalId": {
                    "type": "string",
                    "description": "Proposal identifier returned by createProposal.",
                },
            },
            "required": ["proposalId"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Get proposal state",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "getAuditTrail",
        "description": (
            "Read the append-only decision log for a proposal or customer. "
            "Pass exactly one of proposalId or customerId."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposalId": {
                    "type": "string",
                    "description": "Proposal ID to inspect (mutually exclusive with customerId).",
                },
                "customerId": {
                    "type": "string",
                    "description": "Customer ID to inspect (mutually exclusive with proposalId).",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Get audit trail",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]

_TOOLS_BY_NAME: dict[str, dict[str, Any]] = {tool["name"]: tool for tool in AGENT_TOOLS}


def _assert_no_approval_capabilities(value: Any) -> None:
    """Defense-in-depth (patch P3, AC15): ensure no capability field ever leaves `/mcp`."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in FORBIDDEN_CAPABILITY_KEYS:
                raise RuntimeError(f"Security boundary violation: forbidden capability key '{k}' in MCP output")
            _assert_no_approval_capabilities(v)
    elif isinstance(value, list):
        for item in value:
            _assert_no_approval_capabilities(item)


def _curate_search_excluded(excluded: list[ExcludedProduct], *, max_items: int) -> list[ExcludedProduct]:
    """Keep high-signal exclusions (`SUBSTITUTE_REJECTED`, `DATA_CONFLICT`, `ALLERGEN_*`) first so MCP
    tool responses stay compact while preserving every decisive exclusion."""
    if len(excluded) <= max_items:
        return excluded
    priority: list[ExcludedProduct] = []
    rest: list[ExcludedProduct] = []
    high_signal = {
        ReasonCode.SUBSTITUTE_REJECTED,
        ReasonCode.DATA_CONFLICT,
        ReasonCode.ALLERGEN_UNKNOWN,
        ReasonCode.ALLERGEN_IMPLICIT,
        ReasonCode.ALLERGEN_MAY_CONTAIN,
    }
    for ex in excluded:
        if any(code in high_signal for code in ex.reasons) or ex.productId in BRIEF_PRODUCT_IDS:
            priority.append(ex)
        else:
            rest.append(ex)
    combined = [*priority, *rest]
    return combined[: max(max_items, len(priority))]


def _recorded_product_ids(state: GatewayState, customer_id: str, requested_date: str) -> set[str]:
    """Return productIds covered by the recorded fixture capture when running in RECORDED mode."""
    manifest = getattr(state.gateway.provider, "manifest", None)
    if isinstance(manifest, dict):
        matched: set[str] = set()
        for batch in manifest.get("batches", []):
            if (
                isinstance(batch, dict)
                and batch.get("customerId") == customer_id
                and batch.get("requestedDate") == requested_date
            ):
                matched.update(str(pid) for pid in batch.get("productIds", []))
        if matched:
            return matched
    return set(BRIEF_PRODUCT_IDS)


def _default_missing_item_ids(raw_items: Any) -> Any:
    if not isinstance(raw_items, list):
        return raw_items
    out: list[Any] = []
    for idx, elem in enumerate(raw_items, start=1):
        if isinstance(elem, dict) and "itemId" not in elem:
            out.append({"itemId": f"I{idx}", **elem})
        else:
            out.append(elem)
    return out


def _execute_tool(name: str, arguments: dict[str, Any], state: GatewayState,
                  customer: str) -> dict[str, Any]:
    if name in {"getMandate", "searchProducts", "getOffer", "createProposal"}:
        arguments = {**arguments, "customerId": bind_customer(customer, arguments.get("customerId"))}
    if name == "getAuditTrail" and "proposalId" not in arguments and "customerId" not in arguments:
        arguments = {**arguments, "customerId": customer}
    if name == "listMandates":
        if arguments:
            raise ValueError("listMandates does not accept any arguments.")
        book = state.gateway.mandates
        mandates = [book.get(customer).model_dump(mode="json")]
        return {"mandates": mandates}

    if name == "getMandate":
        req = GetMandateRequest.model_validate(arguments)
        return state.gateway.mandates.get(req.customerId).model_dump(mode="json")

    if name == "searchProducts":
        args = dict(arguments)
        customer_id = args.pop("customerId", None)
        if "limit" not in args:
            args["limit"] = 10
        elif isinstance(args.get("limit"), int) and args["limit"] > 20:
            args["limit"] = 20
        mandate = state.gateway.mandates.get(str(customer_id)) if customer_id is not None else None
        search_req = SearchProductsRequest.model_validate(args)
        res = search_products(search_req, mandate=mandate, products=state.catalog)
        curated_excluded = _curate_search_excluded(res.excluded, max_items=max(search_req.limit * 2, 20))
        res = res.model_copy(update={"excluded": curated_excluded})
        return res.model_dump(mode="json")

    if name == "getOffer":
        args = dict(arguments)
        if "items" in args:
            args["items"] = _default_missing_item_ids(args["items"])
        offer_req = GetOfferRequest.model_validate(args)
        try:
            offer = state.gateway.get_offer(offer_req).offer
        except IntegrationError as exc:
            if "No recorded batch" not in str(exc):
                raise
            allowed_ids = _recorded_product_ids(
                state,
                offer_req.customerId,
                offer_req.requestedDate.isoformat(),
            )
            narrowed_items = [item for item in offer_req.items if item.productId in allowed_ids]
            requested_item_ids = {item.itemId for item in offer_req.items}
            narrowed_item_ids = {item.itemId for item in narrowed_items}
            if not narrowed_items or narrowed_item_ids != requested_item_ids:
                raise
            offer = state.gateway.get_offer(
                offer_req.model_copy(update={"items": narrowed_items})
            ).offer
        return offer.model_dump(mode="json")

    if name == "createProposal":
        args = dict(arguments)
        if isinstance(args.get("request"), dict) and "items" in args["request"]:
            req_copy = dict(args["request"])
            req_copy["items"] = _default_missing_item_ids(req_copy["items"])
            args["request"] = req_copy
        prop_req = CreateProposalRequest.model_validate(args)
        offer = state.gateway.get_registered_offer(prop_req.offerId, prop_req.offerVersion)
        proposal = state.gateway.create_proposal(prop_req, now=state.now_for(offer.mode))
        state.remember(proposal.proposalId)
        return proposal.model_dump(mode="json")

    if name == "getProposal":
        req = GetProposalRequest.model_validate(arguments)
        current = state.gateway.get_proposal(req.proposalId)
        bind_customer(customer, current.customerId)
        proposal = state.gateway.get_proposal(req.proposalId, now=state.now_for(current.mode))
        return proposal.model_dump(mode="json")

    if name == "getAuditTrail":
        req = GetAuditTrailRequest.model_validate(arguments)
        if (req.proposalId is None) == (req.customerId is None):
            raise ValueError("Pass exactly one of proposalId or customerId.")
        if req.proposalId is not None:
            bind_customer(customer, state.gateway.get_proposal(req.proposalId).customerId)
        else:
            bind_customer(customer, req.customerId)
        trail = state.gateway.get_audit_trail(proposal_id=req.proposalId, customer_id=req.customerId)
        return trail.model_dump(mode="json")

    raise KeyError(name)


def _format_domain_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HTTPException):
        return {"error": {"code": "FORBIDDEN" if exc.status_code == 403 else "UNAUTHORIZED", "message": exc.detail}}
    if isinstance(exc, ValidationError):
        return {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Tool argument validation failed",
                "details": [
                    {"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]}
                    for err in exc.errors()
                ],
            }
        }
    if isinstance(exc, NoOrderError):
        decision = exc.decision
        return {
            "error": {
                "code": "NO_ORDER",
                "message": str(exc) or "No confirmable order",
                "details": {
                    "reasons": [str(code) for code in decision.reasons],
                    "excluded": [e.model_dump(mode="json") for e in decision.excluded_products()],
                },
            }
        }
    if isinstance(exc, (ProposalNotFoundError, OfferNotFoundError)):
        msg = str(exc.args[0]) if exc.args else str(exc)
        return {"error": {"code": "NOT_FOUND", "message": msg}}
    if isinstance(exc, ProposalConflictError):
        return {"error": {"code": "CONFLICT", "message": str(exc)}}
    if isinstance(exc, InvalidStateTransitionError):
        return {"error": {"code": "INVALID_STATE", "message": str(exc)}}
    if isinstance(exc, (ProposalError, ApprovalError, ValueError)):
        return {"error": {"code": "BAD_REQUEST", "message": str(exc)}}
    if isinstance(exc, KeyError):
        msg = str(exc.args[0]) if exc.args else str(exc)
        return {"error": {"code": "NOT_FOUND", "message": f"Unknown customer or resource: {msg}"}}
    if isinstance(exc, ValioRateLimited):
        return {"error": {"code": exc.code, "message": str(exc),
                          "details": {"retryAfterSeconds": exc.retry_after}}}
    if isinstance(exc, IntegrationError):
        return {"error": {"code": "UPSTREAM_ERROR", "message": str(exc)}}
    if isinstance(exc, GatewayUnavailable):
        return {"error": {"code": "GATEWAY_UNAVAILABLE", "message": str(exc)}}
    return {"error": {"code": "INTERNAL_ERROR", "message": str(exc) or exc.__class__.__name__}}


def _jsonrpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _handle_rpc_message(
    msg: Any,
    state: GatewayState | GatewayUnavailable,
    customer: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Process one JSON-RPC 2.0 message. Returns `(response_dict_or_None, new_session_id_or_None)`."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        req_id = msg.get("id") if isinstance(msg, dict) else None
        return _jsonrpc_error(req_id, -32600, "Invalid JSON-RPC 2.0 request"), None

    method = msg.get("method")
    has_id = "id" in msg
    req_id = msg.get("id")
    params = msg.get("params")

    # Notifications (no "id") and client responses (no "method"): acknowledge with 202 Accepted.
    if not has_id:
        return None, None

    if not isinstance(method, str) or not method:
        return _jsonrpc_error(req_id, -32600, "Missing or invalid JSON-RPC method"), None

    if params is not None and not isinstance(params, dict):
        return _jsonrpc_error(req_id, -32602, "JSON-RPC params must be an object"), None
    params_obj: dict[str, Any] = params or {}

    if method == "initialize":
        requested_version = str(params_obj.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION)
        negotiated_version = (
            requested_version
            if requested_version in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION
        )
        session_id = f"mcp-{uuid.uuid4().hex}"
        return (
            _jsonrpc_result(
                req_id,
                {
                    "protocolVersion": negotiated_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": SERVER_INSTRUCTIONS,
                },
            ),
            session_id,
        )

    if method == "ping":
        return _jsonrpc_result(req_id, {}), None

    if method == "tools/list":
        return _jsonrpc_result(req_id, {"tools": AGENT_TOOLS}), None

    if method == "resources/list":
        return _jsonrpc_result(req_id, {"resources": []}), None

    if method == "resources/templates/list":
        return _jsonrpc_result(req_id, {"resourceTemplates": []}), None

    if method == "prompts/list":
        return _jsonrpc_result(req_id, {"prompts": []}), None

    if method == "tools/call":
        tool_name = params_obj.get("name")
        raw_args = params_obj.get("arguments")
        if not isinstance(tool_name, str) or not tool_name:
            return _jsonrpc_error(req_id, -32602, "tools/call requires a string 'name'"), None
        if raw_args is not None and not isinstance(raw_args, dict):
            return _jsonrpc_error(req_id, -32602, "tools/call 'arguments' must be an object"), None
        arguments: dict[str, Any] = raw_args or {}

        if tool_name in FORBIDDEN_TOOLS:
            return (
                _jsonrpc_error(
                    req_id,
                    -32601,
                    (
                        f"Tool '{tool_name}' is forbidden on the MCP agent channel. "
                        "Approval, confirmation, and mandate administration are restricted to human channels."
                    ),
                ),
                None,
            )

        if tool_name not in _TOOLS_BY_NAME:
            return _jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}"), None

        if isinstance(state, GatewayUnavailable):
            err_payload = _format_domain_error(state)
            return (
                _jsonrpc_result(
                    req_id,
                    {
                        "content": [{"type": "text", "text": json.dumps(err_payload, ensure_ascii=False)}],
                        "structuredContent": err_payload,
                        "isError": True,
                    },
                ),
                None,
            )

        try:
            output = _execute_tool(tool_name, arguments, state, customer)
            _assert_no_approval_capabilities(output)
            return (
                _jsonrpc_result(
                    req_id,
                    {
                        "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
                        "structuredContent": output,
                        "isError": False,
                    },
                ),
                None,
            )
        except (
            HTTPException,
            ValidationError,
            NoOrderError,
            ProposalNotFoundError,
            OfferNotFoundError,
            ProposalConflictError,
            InvalidStateTransitionError,
            ProposalError,
            ApprovalError,
            IntegrationError,
            GatewayUnavailable,
            KeyError,
            ValueError,
        ) as exc:
            err_payload = _format_domain_error(exc)
            _assert_no_approval_capabilities(err_payload)
            return (
                _jsonrpc_result(
                    req_id,
                    {
                        "content": [{"type": "text", "text": json.dumps(err_payload, ensure_ascii=False)}],
                        "structuredContent": err_payload,
                        "isError": True,
                    },
                ),
                None,
            )

    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}"), None


def _sse_messages(payloads: list[dict[str, Any]]) -> Iterator[str]:
    for item in payloads:
        yield f"event: message\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"


@router.post("/mcp", operation_id="mcpPost", summary="MCP Streamable HTTP endpoint (agent-channel tools only)")
@router.post("/mcp/", include_in_schema=False)
async def mcp_post(request: Request, state: TryState, customer: AgentCustomer) -> Response:
    incoming_session = getattr(request.state, "mcp_session_id", None) or request.headers.get("mcp-session-id")
    accept = (request.headers.get("accept") or "").lower()
    raw_body = await request.body()

    try:
        parsed = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(
            status_code=400,
            content=_jsonrpc_error(None, -32700, "Parse error: invalid JSON"),
        )

    is_batch = isinstance(parsed, list)
    messages = parsed if is_batch else [parsed]
    if is_batch and not messages:
        return JSONResponse(
            status_code=400,
            content=_jsonrpc_error(None, -32600, "Invalid JSON-RPC batch: empty array"),
        )

    responses: list[dict[str, Any]] = []
    session_id = incoming_session
    for msg in messages:
        resp_obj, new_session = _handle_rpc_message(msg, state, customer)
        if new_session and not session_id:
            session_id = new_session
        if resp_obj is not None:
            responses.append(resp_obj)

    headers: dict[str, str] = {"Cache-Control": "no-cache"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    if not responses:
        return Response(status_code=202, headers=headers)

    wants_sse_only = (
        "text/event-stream" in accept
        and "application/json" not in accept
        and "*/*" not in accept
    )
    if wants_sse_only:
        return StreamingResponse(
            _sse_messages(responses),
            media_type="text/event-stream",
            headers={**headers, "X-Accel-Buffering": "no"},
        )

    body_out: Any = responses if is_batch else responses[0]
    return JSONResponse(status_code=200, content=body_out, headers=headers)


@router.get("/mcp", operation_id="mcpGet", summary="MCP Streamable HTTP SSE stream / discovery")
@router.get("/mcp/", include_in_schema=False)
def mcp_get(request: Request, customer: AgentCustomer) -> Response:
    accept = (request.headers.get("accept") or "").lower()
    session_id = request.headers.get("mcp-session-id")
    headers: dict[str, str] = {"Cache-Control": "no-cache"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    if "text/event-stream" in accept and "application/json" not in accept and "*/*" not in accept:
        def _keepalive() -> Iterator[str]:
            yield ": mcp streamable-http\n\n"

        return StreamingResponse(
            _keepalive(),
            media_type="text/event-stream",
            headers={**headers, "X-Accel-Buffering": "no"},
        )

    return JSONResponse(
        status_code=200,
        headers=headers,
        content={
            "transport": "streamable-http",
            "protocolVersion": DEFAULT_PROTOCOL_VERSION,
            "supportedProtocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "serverInfo": SERVER_INFO,
            "tools": [t["name"] for t in AGENT_TOOLS],
        },
    )


@router.delete("/mcp", operation_id="mcpDelete", summary="Close an MCP Streamable HTTP session")
@router.delete("/mcp/", include_in_schema=False)
def mcp_delete(customer: AgentCustomer) -> Response:
    return Response(status_code=204)


__all__ = ["AGENT_TOOLS", "FORBIDDEN_TOOLS", "router"]
