"""Decision-log export (T30): one proposal's or one customer's audit trail as a JSON or CSV download.

Read-only and built from the same `AuditTrail` as `GET /v0.1/audit`. A decision-log entry never holds an
approval token, URL or QR payload (P3), so the export cannot leak one. Free text in the log (request text,
approver comments) is untrusted data: CSV cells that a spreadsheet would run as a formula are neutralised.
"""

import csv
import io
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.contracts import AuditEntry, AuditTrail

from .auth import audit_scope
from .errors import ERROR_RESPONSES
from .state import GatewayState, get_state

# Same channel as getAuditTrail: read-only, holds no approval capability (checked by the OpenAPI channel test).
router = APIRouter(prefix="/v0.1", tags=["Agent channel"], responses=ERROR_RESPONSES)
State = Annotated[GatewayState, Depends(get_state)]

CSV_COLUMNS = (
    "timestamp", "entryId", "customerId", "proposalId", "offerId", "offerVersion", "offerHash",
    "mandateVersion", "actor", "event", "fromState", "toState", "rule", "reasons", "summary", "comment", "facts",
)
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _cell(value: object) -> str:
    """Plain text for a CSV cell; a leading formula character is escaped with an apostrophe (CWE-1236)."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def _facts(entry: AuditEntry) -> str:
    return " | ".join(f"{f.fact}: {f.display} [{f.level}{f' · {f.conflict.code}' if f.conflict else ''}]"
                      for f in entry.facts)


def audit_csv(trail: AuditTrail) -> str:
    """One row per log entry, oldest first, in the order the gateway recorded them."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for e in trail.entries:
        writer.writerow([_cell(v) for v in (
            e.timestamp.isoformat(), e.entryId, e.customerId, e.proposalId, e.offerId, e.offerVersion, e.offerHash,
            e.mandateVersion, e.actor, e.event, e.fromState, e.toState, e.rule, ";".join(e.reasons), e.summary,
            e.comment, _facts(e),
        )])
    return out.getvalue()


@router.get(
    "/audit/export",
    operation_id="exportAuditTrail",
    summary="Download the decision log of one proposal or one customer as JSON or CSV",
    response_class=Response,
    responses={200: {"content": {"application/json": {}, "text/csv": {}},
                     "description": "The audit trail as an attachment."}},
)
def export_audit_trail(
    request: Request,
    state: State,
    proposalId: str | None = None,
    customerId: str | None = None,
    format: Annotated[Literal["json", "csv"], Query(description="json (AuditTrail) or csv")] = "json",
) -> Response:
    if (proposalId is None) == (customerId is None):
        raise HTTPException(status_code=400, detail="Pass exactly one of proposalId or customerId.")
    audit_scope(request, state, proposalId, customerId)
    trail = state.gateway.get_audit_trail(proposal_id=proposalId, customer_id=customerId)
    scope = f"proposal-{proposalId}" if proposalId else f"customer-{customerId}"
    filename = f"decision-log-{_SAFE_NAME.sub('_', scope)}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"}
    if format == "csv":
        # UTF-8 with BOM so spreadsheet apps read €, ä and ö correctly.
        return Response("﻿" + audit_csv(trail), media_type="text/csv; charset=utf-8", headers=headers)
    return Response(trail.model_dump_json(indent=2), media_type="application/json", headers=headers)
