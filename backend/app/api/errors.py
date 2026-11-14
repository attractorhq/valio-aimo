"""One error model for both channels: `{"error": {"code", "message", "details"?}}` with a fitting status."""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.gateway.approvals import (
    ApprovalError,
    ApprovalExpiredError,
    ApprovalNotAllowedError,
    ApprovalSupersededError,
    InvalidApprovalActionError,
    InvalidApprovalTokenError,
    TokenAlreadyUsedError,
    TokenBindingMismatchError,
)
from app.gateway.log import InvalidStateTransitionError
from app.gateway.proposals import (
    NoOrderError,
    OfferNotFoundError,
    ProposalConflictError,
    ProposalError,
    ProposalNotFoundError,
)
from app.valio import IntegrationError
from app.valio.errors import ValioRateLimited

from .state import GatewayUnavailable

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict] | dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid request"},
    404: {"model": ErrorResponse, "description": "Unknown proposal, offer or customer"},
    409: {"model": ErrorResponse, "description": "Conflict with the current proposal state"},
    422: {"model": ErrorResponse, "description": "Validation failed, or no confirmable order"},
    500: {"model": ErrorResponse, "description": "Unexpected gateway error; details include a requestId"},
    502: {"model": ErrorResponse, "description": "Valio Aimo API error"},
    503: {"model": ErrorResponse, "description": "Gateway not configured or Valio rate limited"},
}


def error(status: int, code: str, message: str, details: list[dict] | dict | None = None) -> JSONResponse:
    body = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status, content={"error": body})


# Most specific first: approval errors subclass ValueError, offer/proposal errors subclass KeyError.
_MAPPING: tuple[tuple[type[Exception], int, str], ...] = (
    (ApprovalExpiredError, 410, "APPROVAL_EXPIRED"),
    (TokenAlreadyUsedError, 409, "TOKEN_ALREADY_USED"),
    (ApprovalSupersededError, 409, "PROPOSAL_SUPERSEDED"),
    (InvalidApprovalTokenError, 403, "APPROVAL_TOKEN_INVALID"),
    (TokenBindingMismatchError, 403, "APPROVAL_TOKEN_INVALID"),
    (ApprovalNotAllowedError, 409, "NOT_APPROVABLE"),
    (InvalidApprovalActionError, 400, "INVALID_ACTION"),
    (ApprovalError, 400, "APPROVAL_ERROR"),
    (ProposalNotFoundError, 404, "NOT_FOUND"),
    (OfferNotFoundError, 404, "NOT_FOUND"),
    (ProposalConflictError, 409, "CONFLICT"),
    (InvalidStateTransitionError, 409, "INVALID_STATE"),
    (ProposalError, 400, "PROPOSAL_ERROR"),
    (IntegrationError, 502, "UPSTREAM_ERROR"),
    (GatewayUnavailable, 503, "GATEWAY_UNAVAILABLE"),
)


def _message(exc: Exception) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc) or exc.__class__.__name__


_HTTP_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ValioRateLimited)
    async def rate_limited(_: Request, exc: ValioRateLimited):
        response = error(503, exc.code, str(exc), {"retryAfterSeconds": exc.retry_after})
        response.headers["Retry-After"] = str(exc.retry_after)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        response = error(exc.status_code, _HTTP_CODES.get(exc.status_code, "HTTP_ERROR"), str(exc.detail))
        response.headers.update(exc.headers or {})
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_failed(_: Request, exc: RequestValidationError):
        return error(422, "VALIDATION_ERROR", "Request validation failed", [
            {"field": ".".join(str(part) for part in e["loc"]), "message": e["msg"]} for e in exc.errors()
        ])

    @app.exception_handler(NoOrderError)
    async def no_order(_: Request, exc: NoOrderError):
        # O5/O6: nothing to confirm or approve. The reasons say why; no proposal is created.
        decision = exc.policy_decision
        excluded = getattr(exc, "excluded", None) or decision.excluded_products()
        reasons = getattr(exc, "reasons", None) or [str(code) for code in decision.reasons]
        return error(422, "NO_ORDER", _message(exc), {
            "reasons": list(reasons),
            "excluded": [e.model_dump(mode="json") for e in excluded],
        })

    for exc_type, status, code in _MAPPING:
        def handler(_: Request, exc: Exception, status=status, code=code):
            return error(status, code, _message(exc))

        app.add_exception_handler(exc_type, handler)

    @app.exception_handler(KeyError)
    async def unknown_key(_: Request, exc: KeyError):
        return error(404, "NOT_FOUND", f"Unknown customer or resource: {_message(exc)}")

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception):
        request_id = uuid4().hex
        # Exceptions can contain upstream credentials or approval capabilities. Do not echo their
        # text, request bodies, URLs or tracebacks to the caller or ordinary application logs.
        logger.error("Unhandled gateway error: type=%s requestId=%s", type(exc).__name__, request_id)
        response = error(500, "INTERNAL_ERROR", "An unexpected gateway error occurred.", {"requestId": request_id})
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        return response
