"""Order proposals: binding, state machine, approvals, recheckAndConfirm and decision log (T07).

Implements patch P1, P2 and P3:
- `create_proposal` (`createProposal`) binds `customerId + mandateVersion + request + offerId/offerVersion/offerHash + totals`.
- Repeated `create_proposal` calls for the same `(customerId, offerId, offerVersion, request)` are idempotent.
- Server-side policy evaluation (`app.policy.decide`) chooses `AUTO | ASK | BLOCK`.
- `recheck_and_confirm` (`recheckAndConfirm`, internal only) re-fetches live price + availability (`force_refresh=True`)
  and verifies:
    a. `mandateVersion` unchanged (authority);
    b. `unitPriceExclVat` and `vatRate` unchanged and valid (price);
    c. verified quantity still sufficient and sizing unchanged (quantity);
    d. `deliveryDate`, `closingTime` (cut-off not passed) and `salesBlock` (delivery);
    e. hard requirements still proven and policy decision/reasons unchanged.
- On any difference at the re-check: the proposal transitions to `SUPERSEDED`, any prior approval is voided,
  and a new offer version (`offerVersion + 1`) and proposal version are created and decided from scratch.
"""

import uuid
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.normalise import apply_price_ratio_content
from app.contracts import (
    REASON_REGISTRY,
    ApprovalAction,
    ApproverView,
    AuditTrail,
    CreateProposalRequest,
    DecisionOutcome,
    ExcludedProduct,
    GetAuditTrailRequest,
    GetOfferRequest,
    Mandate,
    Offer,
    OfferItem,
    OfferLine,
    Product,
    Proposal,
    ProposalState,
    Quantity,
    ReasonCode,
    StructuredRequest,
    TrustLabel,
    TrustSource,
    format_date,
    format_money,
    format_time,
)
from app.db.models import OfferRow, ProposalRow
from app.gateway.approvals import (
    DEMO_APPROVER_ACTOR,
    ApprovalExpiredError,
    ApprovalNotAllowedError,
    ApprovalStore,
)
from app.gateway.log import (
    PRE_CONFIRMATION_STATES,
    TERMINAL_STATES,
    DecisionLog,
    decision_summary,
    decisive_facts,
    rule_summary,
    validate_transition,
)
from app.gateway.offers import (
    Lookup,
    OfferResult,
    _line_input,
    _partial_coverage_label,
    _reliability,
    _stock_kind,
    _totals,
    build_offer,
    content_basis,
    get_offer,
    shortlists,
    size,
    with_requirement_facts,
)
from app.policy import (
    DEMO_MANDATES,
    LineInput,
    LineVerdict,
    MandateBook,
    PolicyDecision,
    StockKind,
    check_requirements,
    decide,
    evaluate_line,
)
from app.policy.limits import delivery_dates, limit_sentence
from app.policy.lines import split_price_guard_detail, split_price_rise_pct
from app.valio.facts import SENTINEL_METHOD, is_stock_sentinel

# T62: proposal states whose total counts against the same-day limits (the split-order guard).
COMMITTED_STATES = frozenset({"PENDING_APPROVAL", "APPROVED", "AUTO_APPROVED", "RECHECKED", "CONFIRMED_SIMULATED"})


class ProposalError(ValueError):
    """Base error for proposal operations."""


class OfferNotFoundError(ProposalError, KeyError):
    """Raised when an offerId/offerVersion is not registered in the gateway."""


class ProposalNotFoundError(ProposalError, KeyError):
    """Raised when a proposalId is not found in the gateway."""


class ProposalConflictError(ProposalError):
    """Raised when an offer version is already bound to a different request or customer."""


class NoOrderError(ProposalError):
    """Raised when policy evaluation returns NO_ORDER (e.g. NO_CANDIDATE or CLARIFICATION_NEEDED)."""

    def __init__(
        self,
        decision: PolicyDecision,
        *,
        excluded: Sequence[ExcludedProduct] | None = None,
    ):
        self.policy_decision = decision
        self.decision = decision
        self.excluded = list(excluded) if excluded is not None else decision.excluded_products()
        line_codes = [str(r) for entry in self.excluded for r in entry.reasons]
        all_codes = list(dict.fromkeys([*(str(c) for c in decision.reasons), *line_codes]))
        self.reasons = all_codes
        codes = ", ".join(all_codes)
        super().__init__(f"NO_ORDER ({codes}): no confirmable proposal can be created")


@dataclass(frozen=True)
class StoredOffer:
    offer: Offer
    verdicts: tuple[LineVerdict, ...] = ()
    offer_request: GetOfferRequest | None = None
    partial_items: frozenset[str] = frozenset()


_WEEKDAY_NAMES: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _format_qty(value: Decimal, unit: str) -> str:
    norm = value.normalize()
    text = str(int(norm)) if norm == norm.to_integral() else format(norm, "f")
    return f"{text} {unit}"


def _inferred_offer_partial_items(offer: Offer) -> frozenset[str]:
    """Identify items on `offer` that `build_offer` explicitly marked as partial-delivery."""
    partial: set[str] = set()
    for line in offer.lines:
        if ReasonCode.PARTIAL_AVAILABILITY in line.reasons or any(
            f.fact == "coverage.partial" for f in line.facts
        ):
            partial.add(line.itemId)
    return frozenset(partial)


def _inferred_proposal_partial_items(proposal: Proposal) -> frozenset[str]:
    """Identify items on `proposal` that are explicitly partial-delivery."""
    partial = set(_inferred_offer_partial_items(
        Offer(
            offerId=proposal.offerId,
            offerVersion=proposal.offerVersion,
            offerHash=proposal.offerHash,
            customerId=proposal.customerId,
            requestedDate=proposal.requestedDate,
            createdAt=proposal.createdAt,
            validUntil=proposal.validUntil,
            mode=proposal.mode,
            recordedAt=proposal.recordedAt,
            lines=proposal.lines,
            excluded=proposal.excluded,
            totals=proposal.totals,
        )
    ))
    if ReasonCode.PARTIAL_AVAILABILITY in proposal.reasons:
        delivered_by_item: dict[str, Decimal] = {}
        for line in proposal.lines:
            delivered_by_item[line.itemId] = (
                delivered_by_item.get(line.itemId, Decimal(0)) + line.deliveredQuantity.value
            )
        for item in proposal.request.items:
            deliv = delivered_by_item.get(item.itemId, Decimal(0))
            if Decimal(0) < deliv < item.quantity:
                partial.add(item.itemId)
    return frozenset(partial)


_FROZEN_WORDS: tuple[str, ...] = ("frozen", "pakaste", "pakastettu", "jäädytetty", "fryst", "iqf")


def _line_price_per_unit(line: OfferLine) -> Decimal | None:
    if line.contentPerSalesUnit.value > 0:
        return line.unitPriceExclVat / line.contentPerSalesUnit.value
    if line.deliveredQuantity.value > 0:
        return line.lineTotalExclVat / line.deliveredQuantity.value
    return None


def _line_is_frozen(line: OfferLine, product: Product | None = None) -> bool:
    names = [line.name, line.nameFi or ""]
    if product is not None:
        names.extend(product.names.values())
        if (product.category or "").startswith("41"):
            return True
    text_lower = " ".join(n for n in names if n).lower()
    return any(w in text_lower for w in _FROZEN_WORDS)


def split_state_detail(
    line_a: OfferLine,
    line_b: OfferLine,
    product_a: Product | None = None,
    product_b: Product | None = None,
) -> str | None:
    """When two lines in a split differ in fresh vs. frozen state, name the trade-off (T69/T77)."""
    frozen_a = _line_is_frozen(line_a, product_a)
    frozen_b = _line_is_frozen(line_b, product_b)
    if frozen_a == frozen_b:
        return None
    if frozen_b:
        return "Line 2 is frozen; line 1 is fresh"
    return "Line 2 is fresh; line 1 is frozen"


def split_detail_lines(
    proposal: Proposal,
    *,
    products: Mapping[str, Product] | None = None,
) -> list[str]:
    """Detail lines for a split top-up proposal: price guard (> 10 %) and fresh vs. frozen trade-off (T69)."""
    details: list[str] = []
    for item in proposal.request.items:
        item_lines = [ln for ln in proposal.lines if ln.itemId == item.itemId]
        deliv_total = sum((ln.deliveredQuantity.value for ln in item_lines), Decimal(0))
        if len(item_lines) < 2 or deliv_total < item.quantity:
            continue
        line_a, line_b = item_lines[0], item_lines[1]
        price_a = _line_price_per_unit(line_a)
        price_b = _line_price_per_unit(line_b)
        guard = split_price_guard_detail(price_a, price_b, line_b.deliveredQuantity.unit)
        if guard is not None:
            details.append(guard)
        prod_a = products.get(line_a.productId) if products else None
        prod_b = products.get(line_b.productId) if products else None
        state_det = split_state_detail(line_a, line_b, prod_a, prod_b)
        if state_det is not None:
            details.append(state_det)
    return details


def _format_split_clauses(proposal: Proposal) -> list[str]:
    clauses: list[str] = []
    for item in proposal.request.items:
        item_lines = [ln for ln in proposal.lines if ln.itemId == item.itemId]
        deliv_total = sum((ln.deliveredQuantity.value for ln in item_lines), Decimal(0))
        if len(item_lines) < 2 or deliv_total < item.quantity:
            continue
        line_a, line_b = item_lines[0], item_lines[1]
        price_a = _line_price_per_unit(line_a)
        price_b = _line_price_per_unit(line_b)
        unit = line_b.deliveredQuantity.unit
        diff_clause = ""
        if price_a is not None and price_b is not None:
            diff = (price_b - price_a).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if diff > Decimal(0):
                diff_clause = f" ({format_money(diff)}/{unit} more)"
            elif diff < Decimal(0):
                diff_clause = f" ({format_money(abs(diff))}/{unit} less)"
        part_a = f"{_format_qty(line_a.deliveredQuantity.value, line_a.deliveredQuantity.unit)} of {line_a.name}"
        part_b = (
            f"{_format_qty(line_b.deliveredQuantity.value, line_b.deliveredQuantity.unit)} "
            f"of {line_b.name}{diff_clause}"
        )
        clauses.append(f"{part_a} + {part_b}")
    return clauses


def format_approval_statement(
    proposal: Proposal,
    *,
    mandate: Mandate | None = None,
    today: date | None = None,
) -> str | None:
    """Plain-language summary of what approving this proposal version means (patch P8 B2, P10, T69).

    For Scenario B2 (partial delivery + later date):
      "Approving means 95 kg instead of 120 kg, on Monday instead of today."
    For a split top-up (T69):
      "Approving means 70 kg of A + 50 kg of B (€0.40/kg more)."
    """
    if proposal.decision != "ASK":
        return None

    reasons = set(proposal.reasons)
    delivered_by_item: dict[str, Decimal] = {}
    for line in proposal.lines:
        delivered_by_item[line.itemId] = (
            delivered_by_item.get(line.itemId, Decimal(0)) + line.deliveredQuantity.value
        )

    shortfalls: list[str] = []
    if ReasonCode.PARTIAL_AVAILABILITY in reasons:
        for item in proposal.request.items:
            deliv = delivered_by_item.get(item.itemId, Decimal(0))
            if Decimal(0) < deliv < item.quantity:
                shortfalls.append(
                    f"{_format_qty(deliv, item.unit)} instead of {_format_qty(item.quantity, item.unit)}"
                )

    date_clause: str | None = None
    if ReasonCode.DATE_LATER in reasons and proposal.lines:
        earliest_delivery = max(line.deliveryDate for line in proposal.lines)
        if earliest_delivery > proposal.requestedDate:
            ref_today = today or proposal.createdAt.date()
            req_lower = proposal.request.requestText.lower()
            asked_today = proposal.requestedDate == ref_today or any(
                w in req_lower for w in ("today", "tänään", "idag")
            )
            req_label = "today" if asked_today else format_date(proposal.requestedDate)
            days_ahead = (earliest_delivery - proposal.requestedDate).days
            deliv_label = (
                _WEEKDAY_NAMES[earliest_delivery.weekday()]
                if 0 < days_ahead <= 7
                else format_date(earliest_delivery)
            )
            date_clause = f"on {deliv_label} instead of {req_label}"

    split_clauses = _format_split_clauses(proposal)
    if split_clauses:
        details = split_detail_lines(proposal)
        detail_suffix = (" " + ". ".join(d.rstrip(".") for d in details) + ".") if details else ""
        if date_clause:
            return f"Approving means {', '.join(split_clauses)}, {date_clause}.{detail_suffix}"
        return f"Approving means {', '.join(split_clauses)}.{detail_suffix}"

    if shortfalls and date_clause:
        return f"Approving means {', '.join(shortfalls)}, {date_clause}."
    if shortfalls:
        return f"Approving means {', '.join(shortfalls)}."
    if date_clause:
        return f"Approving means delivery {date_clause}."

    if list(proposal.reasons) == [ReasonCode.OVER_AUTO_LIMIT] and mandate is not None:
        if proposal.totals.exclVat <= mandate.autoApproveLimitEur:
            # T62: the day's committed orders, not this order alone, take it over the limit.
            return (
                limit_sentence(ReasonCode.OVER_AUTO_LIMIT, proposal.totals.exclVat, mandate.autoApproveLimitEur,
                               delivery_dates(proposal.lines, proposal.requestedDate))
                + ". Everything else is verified or declared."
            )
        return (
            f"{format_money(proposal.totals.exclVat)} is above your "
            f"{format_money(mandate.autoApproveLimitEur)} auto-approve limit. "
            "Everything else is verified or declared."
        )

    return None


def _infer_stock_from_offer_line(line: OfferLine) -> tuple[StockKind, Decimal | None]:
    """Reconstruct `(StockKind, stock_amount)` from an `OfferLine`'s trust-labelled facts."""
    if ReasonCode.NOT_AVAILABLE in line.reasons:
        return StockKind.NOT_DELIVERABLE, None
    if ReasonCode.SUPPLIER_STOCK_UNKNOWN in line.reasons:
        return StockKind.SUPPLIER, None
    for label in line.facts:
        if label.fact == "stock.quantity":
            display = (label.display or "").lower()
            if "supplier" in display:
                return StockKind.SUPPLIER, None
            if "plenty" in display or label.method == SENTINEL_METHOD:
                return StockKind.PLENTY, None
            if label.value is not None:
                try:
                    qty = Decimal(str(label.value))
                except ArithmeticError:
                    return StockKind.NOT_DELIVERABLE, None
                if qty <= 0:
                    return StockKind.OUT, Decimal(0)
                if is_stock_sentinel(qty):
                    return StockKind.PLENTY, None
                return StockKind.EXACT, qty
    return StockKind.PLENTY, None


def line_input_from_offer_line(line: OfferLine, product: Product) -> LineInput:
    """Build a `LineInput` from a frozen `OfferLine` and its catalogue `Product`."""
    stock, stock_amount = _infer_stock_from_offer_line(line)
    sales_blocked = ReasonCode.SALES_BLOCKED in line.reasons or any(
        f.fact == "order.blocked" and f.value is True for f in line.facts
    )
    unit_mismatch = ReasonCode.UNIT_MISMATCH in line.reasons or any(
        f.conflict is not None and f.conflict.code == "K4" for f in line.facts
    )
    return LineInput(
        line_id=line.lineId,
        item_id=line.itemId,
        product=product,
        quantity_sales_units=line.quantitySalesUnits,
        delivered_quantity=line.deliveredQuantity,
        line_total_excl_vat=line.lineTotalExclVat,
        price_valid=ReasonCode.PRICE_UNAVAILABLE not in line.reasons,
        sales_blocked=sales_blocked,
        stock=stock,
        stock_amount=stock_amount if stock is StockKind.EXACT else None,
        available_date=line.deliveryDate,
        closing_time=line.closingTime,
        content_basis=content_basis(line.contentPerSalesUnit),
        unit_mismatch=unit_mismatch,
        reliability=_reliability(product),
        substitution=line.substitutionSource,
        substitute_for=line.substituteFor,
        facts=tuple(line.facts),
    )


def _derive_offer_request(offer: Offer, request: StructuredRequest | None = None) -> GetOfferRequest:
    """Reconstruct a `GetOfferRequest` for an offer when none was explicitly stored."""
    req_items = {item.itemId: item for item in (request.items if request else ())}
    items: list[OfferItem] = []
    seen: set[tuple[str, str]] = set()
    for line in offer.lines:
        req_item = req_items.get(line.itemId)
        qty = req_item.quantity if req_item is not None else line.deliveredQuantity.value
        unit = req_item.unit if req_item is not None else line.deliveredQuantity.unit
        key = (line.itemId, line.productId)
        if key not in seen:
            seen.add(key)
            items.append(OfferItem(itemId=line.itemId, productId=line.productId, quantity=qty, unit=unit))
    return GetOfferRequest(customerId=offer.customerId, requestedDate=offer.requestedDate, items=items)


def _rejected_valio_substitutes(
    kept_lines: Sequence[OfferLine],
    request: StructuredRequest,
    mandate: Mandate,
    products: Mapping[str, Product],
    existing_excluded: Sequence[ExcludedProduct],
) -> list[ExcludedProduct]:
    """Check Valio-listed substitutes on kept lines against hard requirements (patch P8 B)."""
    items = {item.itemId: item for item in request.items}
    already = {(e.productId, e.substituteFor) for e in existing_excluded}
    added: list[ExcludedProduct] = []
    for line in kept_lines:
        product = products.get(line.productId)
        item = items.get(line.itemId)
        if product is None or item is None:
            continue
        for sub_id in product.substitutions:
            if (sub_id, product.productId) in already:
                continue
            sub_product = products.get(sub_id)
            if sub_product is None:
                continue
            findings = check_requirements(
                sub_product,
                item.requirements,
                mandate,
                named=False,
                item_id=item.itemId,
                line_id=line.lineId,
            )
            if findings:
                codes = list(
                    dict.fromkeys([*(ReasonCode(f.code) for f in findings), ReasonCode.SUBSTITUTE_REJECTED])
                )
                added.append(
                    ExcludedProduct(
                        productId=sub_product.productId,
                        name=sub_product.name,
                        itemId=item.itemId,
                        reasons=codes,
                        facts=list(sub_product.facts),
                        substituteFor=product.productId,
                    )
                )
                already.add((sub_id, product.productId))
    return added


def _merge_excluded(*groups: Sequence[ExcludedProduct]) -> list[ExcludedProduct]:
    merged: list[ExcludedProduct] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for group in groups:
        for item in group:
            key = (item.productId, item.itemId, item.substituteFor)
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def _default_offer_id(request: GetOfferRequest) -> str:
    return f"OF-{request.requestedDate.isoformat()}-{uuid.uuid4().hex[:12]}"


def _default_proposal_id(offer: Offer) -> str:
    stem = offer.offerId.removeprefix("OF-")
    return f"PR-{stem}-v{offer.offerVersion}"


class ProposalGateway:
    """Server-side proposal state machine, approval verifier, live re-checker and decision log."""

    def __init__(
        self,
        *,
        products: Mapping[str, Product] | None = None,
        mandates: MandateBook | Iterable[Mandate] | None = None,
        provider: Lookup | None = None,
        log: DecisionLog | None = None,
        approvals: ApprovalStore | None = None,
        session: Session | None = None,
        signing_key: str | bytes | None = None,
    ):
        self.products: dict[str, Product] = dict(products or {})
        if isinstance(mandates, MandateBook):
            self.mandates = mandates
        elif mandates is not None:
            self.mandates = MandateBook(mandates)
        else:
            self.mandates = MandateBook(DEMO_MANDATES)
        self.provider = provider
        self._session = session
        self.log = log if log is not None else DecisionLog(session=session)
        self.approvals = (
            approvals if approvals is not None else ApprovalStore(signing_key=signing_key, session=session)
        )
        self._offers: dict[tuple[str, int], StoredOffer] = {}
        self._proposals: dict[str, Proposal] = {}
        self._by_offer_key: dict[tuple[str, str, int], str] = {}
        self._partial_items: dict[str, frozenset[str]] = {}
        if session is not None:
            self._hydrate_from_session(session)

    def _hydrate_from_session(self, session: Session) -> None:
        for offer_row in session.scalars(select(OfferRow)).all():
            offer = Offer.model_validate(offer_row.data)
            req = (
                GetOfferRequest.model_validate(offer_row.request_data)
                if offer_row.request_data is not None
                else None
            )
            self._offers[(offer.offerId, offer.offerVersion)] = StoredOffer(
                offer=offer,
                offer_request=req,
                partial_items=_inferred_offer_partial_items(offer),
            )
        for prop_row in session.scalars(select(ProposalRow)).all():
            proposal = Proposal.model_validate(prop_row.data)
            self._proposals[proposal.proposalId] = proposal
            self._by_offer_key[(proposal.customerId, proposal.offerId, proposal.offerVersion)] = (
                proposal.proposalId
            )
            self._partial_items[proposal.proposalId] = _inferred_proposal_partial_items(proposal)

    def _save_offer(self, stored: StoredOffer) -> None:
        offer = stored.offer
        self._offers[(offer.offerId, offer.offerVersion)] = stored
        if self._session is not None:
            row = self._session.get(OfferRow, (offer.offerId, offer.offerVersion))
            req_dump = stored.offer_request.model_dump(mode="json") if stored.offer_request else None
            if row is None:
                self._session.add(
                    OfferRow(
                        offer_id=offer.offerId,
                        offer_version=offer.offerVersion,
                        customer_id=offer.customerId,
                        offer_hash=offer.offerHash,
                        data=offer.model_dump(mode="json"),
                        request_data=req_dump,
                        created_at=offer.createdAt,
                    )
                )
            else:
                row.data = offer.model_dump(mode="json")
                row.request_data = req_dump
            self._session.commit()

    def _save_proposal(self, proposal: Proposal) -> None:
        self._proposals[proposal.proposalId] = proposal
        self._by_offer_key[(proposal.customerId, proposal.offerId, proposal.offerVersion)] = proposal.proposalId
        if self._session is not None:
            row = self._session.get(ProposalRow, proposal.proposalId)
            if row is None:
                self._session.add(
                    ProposalRow(
                        proposal_id=proposal.proposalId,
                        customer_id=proposal.customerId,
                        offer_id=proposal.offerId,
                        offer_version=proposal.offerVersion,
                        offer_hash=proposal.offerHash,
                        mandate_version=proposal.mandateVersion,
                        state=proposal.state,
                        decision=proposal.decision,
                        superseded_by=proposal.supersededBy,
                        data=proposal.model_dump(mode="json"),
                        created_at=proposal.createdAt,
                    )
                )
            else:
                row.state = proposal.state
                row.decision = proposal.decision
                row.superseded_by = proposal.supersededBy
                row.data = proposal.model_dump(mode="json")
            self._session.commit()

    def register_offer(
        self,
        offer_or_result: Offer | OfferResult,
        *,
        offer_request: GetOfferRequest | None = None,
    ) -> Offer:
        """Register an `Offer` (or `OfferResult`) and append its `OFFERED` decision-log entry."""
        if isinstance(offer_or_result, OfferResult):
            offer = offer_or_result.offer
            verdicts = offer_or_result.verdicts
            partial = offer_or_result.partial_items | _inferred_offer_partial_items(offer)
        else:
            offer = offer_or_result
            verdicts = ()
            partial = _inferred_offer_partial_items(offer)
        key = (offer.offerId, offer.offerVersion)
        if key not in self._offers:
            mandate = self.mandates.get(offer.customerId)
            self._save_offer(
                StoredOffer(
                    offer=offer,
                    verdicts=verdicts,
                    offer_request=offer_request,
                    partial_items=partial,
                )
            )
            self.log.record_transition(
                customer_id=offer.customerId,
                mandate_version=mandate.version,
                timestamp=offer.createdAt,
                actor="gateway:offers",
                event="OFFER_CREATED",
                from_state=None,
                to_state="OFFERED",
                offer_id=offer.offerId,
                offer_version=offer.offerVersion,
                offer_hash=offer.offerHash,
                rule="P1.3",
                facts=decisive_facts(offer.lines),
                summary=(
                    f"Offer {offer.offerId} v{offer.offerVersion} created at "
                    f"{format_money(offer.totals.exclVat)} excl. VAT ({len(offer.lines)} line(s))."
                ),
            )
        return offer

    def get_offer(
        self,
        request: GetOfferRequest,
        *,
        offer_id: str | None = None,
        offer_version: int = 1,
        force_refresh: bool = False,
    ) -> OfferResult:
        """Run `offers.get_offer`, register the resulting offer and log `None -> OFFERED`."""
        if self.provider is None:
            raise ProposalError("No lookup provider configured on ProposalGateway")
        mandate = self.mandates.get(request.customerId)
        resolved_offer_id = offer_id or _default_offer_id(request)
        result = get_offer(
            request,
            products=self.products,
            provider=self.provider,
            mandate=mandate,
            offer_id=resolved_offer_id,
            offer_version=offer_version,
            force_refresh=force_refresh,
        )
        self.register_offer(result, offer_request=request)
        return result

    def get_registered_offer(self, offer_id: str, offer_version: int = 1) -> Offer:
        if self._session is not None and (offer_id, offer_version) not in self._offers:
            self._hydrate_from_session(self._session)
        stored = self._offers.get((offer_id, offer_version))
        if stored is None:
            raise OfferNotFoundError(f"Unknown offer {offer_id} v{offer_version}")
        return stored.offer

    def get_proposal(self, proposal_id: str, *, now: datetime | None = None) -> Proposal:
        """Return the current `Proposal` snapshot for `proposal_id` (expiring if `now > validUntil`)."""
        if self._session is not None and proposal_id not in self._proposals:
            self._hydrate_from_session(self._session)
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(f"Unknown proposal {proposal_id}")
        if now is not None and now > proposal.validUntil and proposal.state in PRE_CONFIRMATION_STATES:
            return self.expire_proposal(proposal_id, now=now)
        return proposal

    def get_approval_statement(self, proposal_id: str, *, today: date | None = None) -> str | None:
        """Return the plain-language approver summary for `proposal_id` (patch P8 B2, P10)."""
        proposal = self.get_proposal(proposal_id)
        mandate = self.mandates.get_version(proposal.customerId, proposal.mandateVersion)
        return format_approval_statement(proposal, mandate=mandate, today=today)

    def get_approver_view(
        self,
        proposal_id: str,
        *,
        approval_url: str | None = None,
        qr_payload: str | None = None,
    ) -> ApproverView:
        """Build the approver-channel `ApproverView` for a `PENDING_APPROVAL` proposal."""
        proposal = self.get_proposal(proposal_id)
        mandate = self.mandates.get_version(proposal.customerId, proposal.mandateVersion)
        statement = format_approval_statement(proposal, mandate=mandate)
        view = self.approvals.issue(
            proposal,
            mandate.approver,
            approval_statement=statement,
            approval_url=approval_url,
            qr_payload=qr_payload,
        )
        self.log.register_secret_token(view.approvalToken)
        return view

    def get_audit_trail(
        self,
        request: GetAuditTrailRequest | None = None,
        *,
        proposal_id: str | None = None,
        customer_id: str | None = None,
    ) -> AuditTrail:
        return self.log.get_audit_trail(request, proposal_id=proposal_id, customer_id=customer_id)

    def _line_inputs_for(self, stored: StoredOffer) -> list[LineInput]:
        if stored.verdicts and len(stored.verdicts) == len(stored.offer.lines):
            inputs: list[LineInput] = []
            for offer_line, verdict in zip(stored.offer.lines, stored.verdicts, strict=True):
                ln = verdict.line
                if offer_line.substitutionSource or offer_line.substituteFor:
                    ln = replace(
                        ln,
                        substitution=offer_line.substitutionSource,
                        substitute_for=offer_line.substituteFor,
                    )
                inputs.append(ln)
            return inputs
        inputs = []
        for offer_line in stored.offer.lines:
            product = self.products.get(offer_line.productId)
            if product is None:
                raise ProposalError(f"Product {offer_line.productId} is missing from the gateway catalogue")
            inputs.append(line_input_from_offer_line(offer_line, product))
        return inputs

    def committed_by_day(
        self, customer_id: str, days: date | Iterable[date], *, exclude: Sequence[str] = ()
    ) -> dict[date, Decimal]:
        """T62: what the customer already has committed for delivery on each of `days` (excl. VAT).

        Keyed on the lines' delivery dates, never the requested date: orders requested for different days can
        arrive on the same day. Confirmed (simulated) orders and proposals on their way to confirmation (waiting
        for sign-off, approved, being re-checked) count; superseded, rejected, expired and blocked ones do not.
        """
        if self._session is not None:
            self._hydrate_from_session(self._session)
        wanted = {days} if isinstance(days, date) else set(days)
        committed: dict[date, Decimal] = {}
        for p in self._proposals.values():
            if p.customerId != customer_id or p.state not in COMMITTED_STATES or p.proposalId in exclude:
                continue
            for line in p.lines:
                if line.deliveryDate in wanted:
                    committed[line.deliveryDate] = committed.get(line.deliveryDate, Decimal(0)) + line.lineTotalExclVat
        return committed

    def committed_total(
        self, customer_id: str, days: date | Iterable[date], *, exclude: Sequence[str] = ()
    ) -> Decimal:
        """T62: the customer's committed total (excl. VAT) for delivery on any of `days`."""
        return sum(self.committed_by_day(customer_id, days, exclude=exclude).values(), Decimal(0))

    def create_proposal(
        self,
        request_or_customer: CreateProposalRequest | str,
        offer_id: str | None = None,
        offer_version: int | None = None,
        structured_request: StructuredRequest | None = None,
        *,
        offer: Offer | OfferResult | None = None,
        proposal_id: str | None = None,
        now: datetime | None = None,
        partial_items: frozenset[str] | None = None,
        instruction_ignored: bool = False,
        clarification_needed: bool = False,
        auto_confirm: bool = True,
        allow_no_order_block: bool = False,
        replaces: str | None = None,
    ) -> Proposal:
        """Bind `customerId + mandateVersion + request + offerId/offerVersion/offerHash + totals` and decide.

        `replaces` is the proposal a new version supersedes: it no longer counts towards the committed total.

        Accepts either a `CreateProposalRequest` or `(customerId, offerId, offerVersion, request)`.
        Repeated calls for the same `(customerId, offerId, offerVersion, request)` are idempotent.
        """
        if isinstance(request_or_customer, CreateProposalRequest):
            create_req = request_or_customer
        else:
            if offer_id is None or offer_version is None or structured_request is None:
                raise TypeError(
                    "create_proposal requires either CreateProposalRequest or "
                    "(customer_id, offer_id, offer_version, structured_request)"
                )
            create_req = CreateProposalRequest(
                customerId=request_or_customer,
                offerId=offer_id,
                offerVersion=offer_version,
                request=structured_request,
            )

        if offer is not None:
            self.register_offer(offer)

        if self._session is not None:
            self._hydrate_from_session(self._session)

        # Idempotent create check (T07 Scope, docs/API.md).
        offer_binding_key = (create_req.customerId, create_req.offerId, create_req.offerVersion)
        if existing_id := self._by_offer_key.get(offer_binding_key):
            existing = self._proposals[existing_id]
            if existing.request != create_req.request:
                raise ProposalConflictError(
                    f"Offer {create_req.offerId} v{create_req.offerVersion} is already bound to a different request"
                )
            return existing

        stored = self._offers.get((create_req.offerId, create_req.offerVersion))
        if stored is None:
            raise OfferNotFoundError(f"Unknown offer {create_req.offerId} v{create_req.offerVersion}")
        bound_offer = stored.offer
        if bound_offer.customerId != create_req.customerId:
            raise ProposalConflictError(
                f"Offer {bound_offer.offerId} belongs to customer {bound_offer.customerId}, "
                f"not {create_req.customerId}"
            )
        if bound_offer.requestedDate != create_req.request.requestedDate:
            raise ProposalConflictError(
                f"Offer requestedDate {bound_offer.requestedDate} does not match "
                f"request requestedDate {create_req.request.requestedDate}"
            )
        for existing_key, existing_prop_id in self._by_offer_key.items():
            _, ex_offer_id, ex_offer_ver = existing_key
            if ex_offer_id == create_req.offerId and ex_offer_ver == create_req.offerVersion:
                raise ProposalConflictError(
                    f"Offer {create_req.offerId} v{create_req.offerVersion} is already bound to "
                    f"proposal {existing_prop_id}"
                )

        mandate = self.mandates.get(create_req.customerId)
        eval_now = now or bound_offer.createdAt
        if eval_now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        line_inputs = self._line_inputs_for(stored)
        effective_partial = (
            frozenset(stored.partial_items | _inferred_offer_partial_items(bound_offer))
            if partial_items is None
            else frozenset(partial_items)
        )
        line_inputs, effective_partial = self._prepare_split_and_partial(
            line_inputs,
            create_req.request,
            mandate,
            eval_now,
            effective_partial,
            explicit_partial=partial_items is not None,
        )
        # T62: the limits apply to this order plus what is committed for delivery on the days its lines arrive.
        committed_by_day = self.committed_by_day(
            create_req.customerId,
            delivery_dates(bound_offer.lines, create_req.request.requestedDate),
            exclude=(replaces,) if replaces else (),
        )
        committed = sum(committed_by_day.values(), Decimal(0))
        committed_days = tuple(sorted(d for d, amount in committed_by_day.items() if amount > 0))
        policy_decision = decide(
            mandate=mandate,
            request=create_req.request,
            lines=line_inputs,
            now=eval_now,
            partial_items=effective_partial,
            instruction_ignored=instruction_ignored,
            clarification_needed=clarification_needed,
            committed_excl_vat=committed,
            committed_days=committed_days,
        )

        verdict_by_line = {v.line.line_id: v for v in policy_decision.lines}
        items_by_id = {item.itemId: item for item in create_req.request.items}

        def evaluated_facts(product_id: str, item_id: str, facts: Sequence[TrustLabel]) -> list[TrustLabel]:
            # T41: the requirement facts the policy decided on, so the card, footer and log show the same.
            product = self.products.get(product_id)
            if product is None:
                return list(facts)
            item = items_by_id.get(item_id)
            return with_requirement_facts(facts, product, item.requirements if item else [], mandate)

        if policy_decision.outcome == "NO_ORDER" and not allow_no_order_block:
            no_order_excluded = [
                e.model_copy(update={"facts": evaluated_facts(e.productId, e.itemId, e.facts)})
                for e in _merge_excluded(bound_offer.excluded, policy_decision.excluded_products())
            ]
            raise NoOrderError(policy_decision, excluded=no_order_excluded)

        kept_lines = self._build_kept_lines(
            bound_offer=bound_offer,
            verdict_by_line=verdict_by_line,
            items_by_id=items_by_id,
            effective_partial=effective_partial,
            policy_reasons=policy_decision.reasons,
            evaluated_facts=evaluated_facts,
        )
        totals = bound_offer.totals if len(kept_lines) == len(bound_offer.lines) else _totals(kept_lines)
        sub_excluded = _rejected_valio_substitutes(
            kept_lines, create_req.request, mandate, self.products, bound_offer.excluded
        )
        excluded = [
            e.model_copy(update={"facts": evaluated_facts(e.productId, e.itemId, e.facts)})
            for e in _merge_excluded(bound_offer.excluded, policy_decision.excluded_products(), sub_excluded)
        ]

        pid = proposal_id or _default_proposal_id(bound_offer)
        self._partial_items[pid] = effective_partial
        self.log.bind_offer_entries(bound_offer.offerId, bound_offer.offerVersion, pid)

        # 1. OFFERED -> PROPOSED
        self.log.record_transition(
            customer_id=create_req.customerId,
            proposal_id=pid,
            offer_id=bound_offer.offerId,
            offer_version=bound_offer.offerVersion,
            offer_hash=bound_offer.offerHash,
            mandate_version=mandate.version,
            timestamp=eval_now,
            actor="gateway:proposal",
            event="PROPOSAL_CREATED",
            from_state="OFFERED",
            to_state="PROPOSED",
            rule="P1.2",
            facts=decisive_facts(kept_lines),
            summary=(
                f"Proposal {pid} bound to customer {create_req.customerId} (mandate v{mandate.version}), "
                f"offer {bound_offer.offerId} v{bound_offer.offerVersion} ({bound_offer.offerHash[:12]}) "
                f"and total {format_money(totals.exclVat)} excl. VAT."
            ),
        )

        outcome: DecisionOutcome = (
            "BLOCK" if policy_decision.outcome == "NO_ORDER" else policy_decision.outcome
        )
        reasons = [ReasonCode(r) for r in policy_decision.reasons]
        approver_role = mandate.approver.role if outcome == "ASK" else None

        # Check if the offer is already expired at creation time (P1.4).
        if eval_now > bound_offer.validUntil:
            self.log.record_transition(
                customer_id=create_req.customerId,
                proposal_id=pid,
                offer_id=bound_offer.offerId,
                offer_version=bound_offer.offerVersion,
                offer_hash=bound_offer.offerHash,
                mandate_version=mandate.version,
                timestamp=eval_now,
                actor="gateway:lifecycle",
                event="PROPOSAL_EXPIRED",
                from_state="PROPOSED",
                to_state="EXPIRED",
                reasons=reasons,
                rule="P1.4",
                facts=decisive_facts(kept_lines),
                summary=f"Offer {bound_offer.offerId} v{bound_offer.offerVersion} expired at {format_time(bound_offer.validUntil)}.",
            )
            expired_proposal = Proposal(
                proposalId=pid,
                customerId=create_req.customerId,
                mandateVersion=mandate.version,
                offerId=bound_offer.offerId,
                offerVersion=bound_offer.offerVersion,
                offerHash=bound_offer.offerHash,
                state="EXPIRED",
                decision=outcome,
                reasons=reasons,
                approverRole=approver_role,
                request=create_req.request,
                requestedDate=bound_offer.requestedDate,
                createdAt=bound_offer.createdAt,
                validUntil=bound_offer.validUntil,
                mode=bound_offer.mode,
                recordedAt=bound_offer.recordedAt,
                lines=kept_lines,
                excluded=excluded,
                totals=totals,
            )
            self._save_proposal(expired_proposal)
            return expired_proposal

        # 2. PROPOSED -> AUTO_APPROVED | PENDING_APPROVAL | BLOCKED
        target_state: ProposalState = {
            "AUTO": "AUTO_APPROVED",
            "ASK": "PENDING_APPROVAL",
            "BLOCK": "BLOCKED",
        }[outcome]
        preview_proposal = Proposal(
            proposalId=pid,
            customerId=create_req.customerId,
            mandateVersion=mandate.version,
            offerId=bound_offer.offerId,
            offerVersion=bound_offer.offerVersion,
            offerHash=bound_offer.offerHash,
            state=target_state,
            decision=outcome,
            reasons=reasons,
            approverRole=approver_role,
            request=create_req.request,
            requestedDate=bound_offer.requestedDate,
            createdAt=bound_offer.createdAt,
            validUntil=bound_offer.validUntil,
            mode=bound_offer.mode,
            recordedAt=bound_offer.recordedAt,
            lines=kept_lines,
            excluded=excluded,
            totals=totals,
        )
        base_summary = decision_summary(
            outcome, reasons, total_excl_vat=totals.exclVat, mandate=mandate,
            committed_excl_vat=committed, committed_days=committed_days,
        )
        approval_statement = (
            format_approval_statement(preview_proposal, mandate=mandate, today=eval_now.date())
            if outcome == "ASK" and (
                ReasonCode.PARTIAL_AVAILABILITY in reasons
                or ReasonCode.DATE_LATER in reasons
                or ReasonCode.SPLIT in reasons
            )
            else None
        )
        policy_summary = f"{base_summary} {approval_statement}" if approval_statement else base_summary
        self.log.record_transition(
            customer_id=create_req.customerId,
            proposal_id=pid,
            offer_id=bound_offer.offerId,
            offer_version=bound_offer.offerVersion,
            offer_hash=bound_offer.offerHash,
            mandate_version=mandate.version,
            timestamp=eval_now,
            actor="gateway:policy",
            event="POLICY_DECIDED",
            from_state="PROPOSED",
            to_state=target_state,
            reasons=reasons,
            rule=rule_summary(policy_decision.findings),
            facts=decisive_facts(kept_lines),
            summary=policy_summary,
        )

        proposal = preview_proposal
        self._save_proposal(proposal)

        if target_state == "PENDING_APPROVAL":
            view = self.approvals.issue(proposal, mandate.approver)
            self.log.register_secret_token(view.approvalToken)
            return proposal

        if target_state == "AUTO_APPROVED" and auto_confirm and self.provider is not None:
            return self.recheck_and_confirm(proposal.proposalId, now=now)

        return proposal

    def expire_proposal(self, proposal_id: str, *, now: datetime) -> Proposal:
        """Transition an expired pre-confirmation proposal to `EXPIRED` and void any approval."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(f"Unknown proposal {proposal_id}")
        if proposal.state == "EXPIRED":
            return proposal
        validate_transition(proposal.state, "EXPIRED")
        self.approvals.void_for_proposal(proposal_id)
        self.log.record_transition(
            customer_id=proposal.customerId,
            proposal_id=proposal.proposalId,
            offer_id=proposal.offerId,
            offer_version=proposal.offerVersion,
            offer_hash=proposal.offerHash,
            mandate_version=proposal.mandateVersion,
            timestamp=now,
            actor="gateway:lifecycle",
            event="PROPOSAL_EXPIRED",
            from_state=proposal.state,
            to_state="EXPIRED",
            reasons=proposal.reasons,
            rule="P1.4",
            facts=decisive_facts(proposal.lines),
            summary=f"Proposal {proposal.proposalId} expired at {format_time(proposal.validUntil)}.",
        )
        expired = proposal.model_copy(update={"state": "EXPIRED"})
        self._save_proposal(expired)
        return expired

    def decide_approval(
        self,
        proposal_id: str,
        offer_version: int,
        approval_token: str,
        action: ApprovalAction | str,
        comment: str | None = None,
        *,
        now: datetime | None = None,
        actor: str = DEMO_APPROVER_ACTOR,
        auto_recheck: bool = True,
    ) -> Proposal:
        """Process an approver's `APPROVE` or `REJECT` decision on the approver channel (P1.3, P2.5, P3)."""
        if self._session is not None and proposal_id not in self._proposals:
            self._hydrate_from_session(self._session)
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(f"Unknown proposal {proposal_id}")

        eval_now = now or proposal.createdAt
        if eval_now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        if proposal.decision == "BLOCK" or proposal.state == "BLOCKED":
            raise ApprovalNotAllowedError("BLOCK proposals can never be approved", proposal=proposal)

        if eval_now > proposal.validUntil and proposal.state in PRE_CONFIRMATION_STATES:
            expired = self.expire_proposal(proposal_id, now=eval_now)
            raise ApprovalExpiredError(
                f"Offer {proposal.offerId} v{proposal.offerVersion} expired at {proposal.validUntil.isoformat()}",
                proposal=expired,
            )

        self.approvals.verify_and_consume(
            proposal,
            offer_version=offer_version,
            approval_token=approval_token,
            action=str(action),
            now=eval_now,
        )

        if action == "REJECT":
            self.log.record_transition(
                customer_id=proposal.customerId,
                proposal_id=proposal.proposalId,
                offer_id=proposal.offerId,
                offer_version=proposal.offerVersion,
                offer_hash=proposal.offerHash,
                mandate_version=proposal.mandateVersion,
                timestamp=eval_now,
                actor=actor,
                event="APPROVAL_REJECTED",
                from_state="PENDING_APPROVAL",
                to_state="REJECTED",
                reasons=proposal.reasons,
                rule="P1.3/P2.5",
                facts=decisive_facts(proposal.lines),
                comment=comment,
                summary=(
                    f"Rejected proposal {proposal.proposalId} (offer v{proposal.offerVersion})"
                    + (f": {comment}" if comment else ".")
                ),
            )
            rejected = proposal.model_copy(update={"state": "REJECTED"})
            self._save_proposal(rejected)
            return rejected

        # action == "APPROVE"
        self.log.record_transition(
            customer_id=proposal.customerId,
            proposal_id=proposal.proposalId,
            offer_id=proposal.offerId,
            offer_version=proposal.offerVersion,
            offer_hash=proposal.offerHash,
            mandate_version=proposal.mandateVersion,
            timestamp=eval_now,
            actor=actor,
            event="APPROVAL_GRANTED",
            from_state="PENDING_APPROVAL",
            to_state="APPROVED",
            reasons=proposal.reasons,
            rule="P1.3/P2.5",
            facts=decisive_facts(proposal.lines),
            comment=comment,
            summary=(
                f"Approved proposal {proposal.proposalId} (offer v{proposal.offerVersion}, "
                f"{format_money(proposal.totals.exclVat)} excl. VAT)"
                + (f": {comment}" if comment else ".")
            ),
        )
        approved = proposal.model_copy(update={"state": "APPROVED"})
        self._save_proposal(approved)

        if auto_recheck and self.provider is not None:
            return self.recheck_and_confirm(proposal.proposalId, now=now)
        return approved

    def _prepare_split_and_partial(
        self,
        line_inputs: list[LineInput],
        request: StructuredRequest,
        mandate: Mandate,
        eval_now: datetime,
        effective_partial: frozenset[str],
        *,
        explicit_partial: bool,
    ) -> tuple[list[LineInput], frozenset[str]]:
        """T69: mark compliant top-up lines in a multi-line split with `split_top_up=True` and `AGENT_SUBSTITUTE`.

        If a top-up line fails a hard requirement (e.g. `CLAIM_MISSING`), it is excluded by `evaluate_line` and
        there is no split version; the primary partial-stock line falls back to `PARTIAL_AVAILABILITY`.
        """
        updated = list(line_inputs)
        partial = set(effective_partial)
        for item in request.items:
            item_indices = [idx for idx, ln in enumerate(updated) if ln.item_id == item.itemId]
            if len(item_indices) <= 1:
                continue
            ok_indices = [
                idx
                for idx in item_indices
                if evaluate_line(
                    updated[idx],
                    item=item,
                    mandate=mandate,
                    requested_date=request.requestedDate,
                    now=eval_now,
                ).status
                != "EXCLUDED"
            ]
            covered = sum((updated[idx].covered_quantity() for idx in ok_indices), Decimal(0))
            if len(ok_indices) >= 2 and covered >= item.quantity and item.itemId not in partial:
                primary_line = updated[ok_indices[0]]
                primary_ppu = primary_line.price_per_delivered_unit()
                for top_idx in ok_indices[1:]:
                    ln = updated[top_idx]
                    updated[top_idx] = replace(
                        ln,
                        split_top_up=True,
                        substitution=ln.substitution or "AGENT",
                        substitute_for=ln.substitute_for or primary_line.product.productId,
                        primary_price_per_unit=primary_ppu,
                    )
            elif not explicit_partial and Decimal(0) < covered < item.quantity:
                partial.add(item.itemId)
        return updated, frozenset(partial)

    def _build_kept_lines(
        self,
        *,
        bound_offer: Offer,
        verdict_by_line: Mapping[str, LineVerdict],
        items_by_id: Mapping[str, object],
        effective_partial: frozenset[str],
        policy_reasons: Sequence[ReasonCode],
        evaluated_facts,
    ) -> list[OfferLine]:
        kept_lines: list[OfferLine] = []
        primary_line_by_item: dict[str, OfferLine] = {}
        for line in bound_offer.lines:
            if line.lineId not in verdict_by_line:
                continue
            verdict = verdict_by_line[line.lineId]
            facts = list(evaluated_facts(line.productId, line.itemId, line.facts))
            if line.itemId not in primary_line_by_item:
                primary_line_by_item[line.itemId] = line
                if (
                    line.itemId in effective_partial
                    and ReasonCode.PARTIAL_AVAILABILITY in policy_reasons
                    and not any(f.fact == "coverage.partial" for f in facts)
                ):
                    req_item = items_by_id.get(line.itemId)
                    if req_item is not None:
                        facts.append(
                            _partial_coverage_label(
                                line.deliveredQuantity,
                                Quantity(value=req_item.quantity, unit=req_item.unit),
                                line.deliveryDate,
                                retrieved_at=bound_offer.createdAt,
                            )
                        )
            if verdict.line.split_top_up:
                primary_offer_line = primary_line_by_item[line.itemId]
                guard_detail = split_price_guard_detail(
                    verdict.line.primary_price_per_unit,
                    verdict.line.price_per_delivered_unit(),
                    line.deliveredQuantity.unit,
                )
                if guard_detail is not None and not any(f.fact == "split.priceGuard" for f in facts):
                    pct = split_price_rise_pct(
                        verdict.line.primary_price_per_unit,
                        verdict.line.price_per_delivered_unit(),
                    )
                    pct_val = str(int(pct)) if pct is not None and pct == pct.to_integral() else str(pct)
                    facts.append(
                        TrustLabel(
                            fact="split.priceGuard",
                            value=f"+{pct_val}%",
                            display=guard_detail,
                            level="VERIFIED",
                            source=TrustSource(
                                system="valio-aimo.price-api",
                                field="catalogPrice.value",
                                retrievedAt=bound_offer.createdAt,
                            ),
                            method="split-price-guard (>10%)",
                            conflict=None,
                        )
                    )
                state_det = split_state_detail(
                    primary_offer_line,
                    line,
                    self.products.get(primary_offer_line.productId),
                    self.products.get(line.productId),
                )
                if state_det is not None and not any(f.fact == "split.variant" for f in facts):
                    facts.append(
                        TrustLabel(
                            fact="split.variant",
                            value="fresh_vs_frozen",
                            display=state_det,
                            level="DECLARED",
                            source=TrustSource(
                                system="valio-aimo.product-data",
                                field="synkkaData.names",
                                retrievedAt=None,
                            ),
                            method="split-variant-guard",
                            conflict=None,
                        )
                    )
            reasons = list(
                dict.fromkeys([
                    *(ReasonCode(r) for r in verdict.reasons),
                    *(
                        [ReasonCode.PARTIAL_AVAILABILITY]
                        if line.itemId in effective_partial
                        and ReasonCode.PARTIAL_AVAILABILITY in policy_reasons
                        else []
                    ),
                ])
            )
            kept_lines.append(
                line.model_copy(
                    update={
                        "facts": facts,
                        "reasons": reasons,
                        "substitutionSource": verdict.line.substitution or line.substitutionSource,
                        "substituteFor": verdict.line.substitute_for or line.substituteFor,
                    }
                )
            )
        return kept_lines

    def _recheck_differences(
        self,
        proposal: Proposal,
        *,
        current_mandate: Mandate,
        snapshot,
        check_now: datetime,
    ) -> tuple[list[str], list[TrustLabel]]:
        """Check P1.3 step 5 (a–e) and return `(differences, refreshed_facts)`."""
        diffs: list[str] = []
        refreshed_facts: list[TrustLabel] = []

        # a. mandateVersion unchanged (authority)
        if current_mandate.version != proposal.mandateVersion:
            diffs.append(f"mandateVersion v{proposal.mandateVersion} → v{current_mandate.version}")

        items_by_id = {item.itemId: item for item in proposal.request.items}
        used_stock: dict[str, Decimal] = {}
        refreshed_line_inputs: list[LineInput] = []
        primary_by_item: dict[str, tuple[str, Decimal | None]] = {}
        partial_for_prop = self._partial_items.get(proposal.proposalId, frozenset())

        for line in proposal.lines:
            product = self.products.get(line.productId)
            facts = snapshot.products.get(line.productId)
            req_item = items_by_id.get(line.itemId)
            if product is None or facts is None or req_item is None:
                diffs.append(f"product {line.productId} missing from catalogue or live snapshot")
                continue

            refreshed_facts.extend([*facts.facts, *product.facts])
            price = facts.price
            avail = facts.availability

            # b. unit prices and VAT unchanged and valid (price)
            if price.unit_price is None or price.vat_rate is None or ReasonCode.PRICE_UNAVAILABLE in price.reasons:
                diffs.append(f"price for {line.productId} unavailable")
            else:
                if price.unit_price != line.unitPriceExclVat:
                    diffs.append(
                        f"unit price for {line.productId} "
                        f"{format_money(line.unitPriceExclVat)} → {format_money(price.unit_price)}"
                    )
                if price.vat_rate != line.vatRate:
                    diffs.append(f"VAT rate for {line.productId} {line.vatRate}% → {price.vat_rate}%")

            # c. verified quantity still sufficient and pack sizing unchanged (quantity)
            sized_product = apply_price_ratio_content(
                product, price.unit_price, price.comparison_price, price.comparison_unit
            )
            sizing = size(sized_product, line.deliveredQuantity.value, line.deliveredQuantity.unit)
            if sizing.unit_mismatch or ReasonCode.UNIT_MISMATCH in avail.reasons:
                diffs.append(f"unit mismatch for {line.productId}")
            if sizing.content != line.contentPerSalesUnit:
                diffs.append(f"pack content for {line.productId} changed")

            stock_kind = _stock_kind(facts)
            if stock_kind in (StockKind.OUT, StockKind.NOT_DELIVERABLE):
                diffs.append(f"stock for {line.productId} is no longer available")
            elif stock_kind is StockKind.EXACT:
                free = (avail.available_quantity or Decimal(0)) - used_stock.get(line.productId, Decimal(0))
                if free < line.quantitySalesUnits:
                    diffs.append(
                        f"verified stock for {line.productId} ({free}) < required ({line.quantitySalesUnits})"
                    )
                used_stock[line.productId] = used_stock.get(line.productId, Decimal(0)) + line.quantitySalesUnits
            elif stock_kind is StockKind.SUPPLIER:
                if (
                    ReasonCode.SUPPLIER_STOCK_UNKNOWN not in line.reasons
                    and not current_mandate.autoAcceptSupplierStock
                ):
                    diffs.append(f"stock for {line.productId} changed to supplier stock")

            # d. availableDate, cut-off not passed, no sales block (delivery)
            if avail.sales_block or ReasonCode.SALES_BLOCKED in avail.reasons:
                diffs.append(f"sales blocked for {line.productId}")
            if avail.delivery_date != line.deliveryDate:
                diffs.append(f"delivery date for {line.productId} {line.deliveryDate} → {avail.delivery_date}")
            if avail.delivery_date is None or (
                avail.delivery_date > proposal.requestedDate
                and ReasonCode.DATE_LATER not in proposal.reasons
            ):
                diffs.append(
                    f"delivery date {avail.delivery_date} is later than requested {proposal.requestedDate}"
                )
            if current_mandate.enforceCutoff and (
                avail.closing_time is None
                or avail.closing_time < check_now
                or ReasonCode.CUTOFF_PASSED in avail.reasons
            ):
                diffs.append(f"order cut-off passed for {line.productId}")
            if avail.closing_time != line.closingTime:
                diffs.append(f"cut-off time for {line.productId} changed")

            # e. hard requirements still proven (line rules L1–L14)
            probe = _line_input(line.lineId, line.itemId, sized_product, facts, sizing)
            if probe.stock is StockKind.EXACT:
                prev_used = used_stock.get(line.productId, Decimal(0)) - line.quantitySalesUnits
                probe = replace(probe, stock_amount=(avail.available_quantity or Decimal(0)) - prev_used)
            if line.substitutionSource or line.substituteFor:
                probe = replace(
                    probe,
                    substitution=line.substitutionSource,
                    substitute_for=line.substituteFor,
                )
            if line.itemId not in primary_by_item:
                primary_by_item[line.itemId] = (line.productId, probe.price_per_delivered_unit())
            elif line.itemId not in partial_for_prop:
                primary_pid, primary_ppu = primary_by_item[line.itemId]
                probe = replace(
                    probe,
                    split_top_up=True,
                    substitution=line.substitutionSource or "AGENT",
                    substitute_for=line.substituteFor or primary_pid,
                    primary_price_per_unit=primary_ppu,
                )
            verdict = evaluate_line(
                probe,
                item=req_item,
                mandate=current_mandate,
                requested_date=proposal.requestedDate,
                now=check_now,
            )
            if verdict.status != "OK":
                diffs.append(
                    f"line {line.lineId} ({line.productId}) failed rules: "
                    f"{', '.join(str(c) for c in verdict.exclusion_codes)}"
                )
            else:
                expected_line_reasons = [
                    r for r in line.reasons if r != ReasonCode.PARTIAL_AVAILABILITY
                ]
                actual_line_reasons = [
                    r for r in verdict.reasons if r != ReasonCode.PARTIAL_AVAILABILITY
                ]
                if actual_line_reasons != expected_line_reasons:
                    diffs.append(
                        f"line {line.lineId} reasons changed {list(line.reasons)} → {list(verdict.reasons)}"
                    )
            refreshed_line_inputs.append(probe)

        if not diffs and len(refreshed_line_inputs) == len(proposal.lines):
            partial = self._partial_items.get(proposal.proposalId, frozenset())
            rechecked_decision = decide(
                mandate=current_mandate,
                request=proposal.request,
                lines=refreshed_line_inputs,
                now=check_now,
                partial_items=partial,
            )
            if rechecked_decision.outcome != proposal.decision:
                diffs.append(f"policy outcome changed {proposal.decision} → {rechecked_decision.outcome}")
            if list(rechecked_decision.reasons) != list(proposal.reasons):
                diffs.append(
                    f"order reasons changed {list(proposal.reasons)} → {list(rechecked_decision.reasons)}"
                )

        return diffs, refreshed_facts

    def recheck_and_confirm(
        self,
        proposal_id: str,
        *,
        now: datetime | None = None,
    ) -> Proposal:
        """Internal server-only confirmation gate (P1.3 step 5).

        Re-fetches live price and availability for the proposal lines (`force_refresh=True`),
        verifies mandate, price, quantity, delivery and hard requirements:
        - If unchanged: transitions `AUTO_APPROVED | APPROVED -> RECHECKED -> CONFIRMED_SIMULATED`.
        - On any difference: transitions to `SUPERSEDED`, voids any approval, and creates + decides
          a new offer version (`offerVersion + 1`) and proposal version from scratch.
        """
        if self._session is not None and proposal_id not in self._proposals:
            self._hydrate_from_session(self._session)
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(f"Unknown proposal {proposal_id}")

        if proposal.decision == "BLOCK" or proposal.state == "BLOCKED":
            raise ApprovalNotAllowedError("BLOCK proposals can never be confirmed", proposal=proposal)
        if proposal.state == "PENDING_APPROVAL":
            raise ApprovalNotAllowedError(
                "PENDING_APPROVAL proposal cannot be confirmed without human approval",
                proposal=proposal,
            )
        if proposal.state in TERMINAL_STATES:
            raise ApprovalNotAllowedError(
                f"Proposal in terminal state {proposal.state} cannot be confirmed",
                proposal=proposal,
            )
        if proposal.state not in ("AUTO_APPROVED", "APPROVED"):
            raise ApprovalNotAllowedError(
                f"Proposal in state {proposal.state} must be AUTO_APPROVED or APPROVED before re-check",
                proposal=proposal,
            )
        if proposal.state == "APPROVED" and not self.approvals.is_approval_valid(
            proposal.proposalId, proposal.offerVersion, proposal.offerHash
        ):
            raise ApprovalNotAllowedError(
                "No valid approval exists for this proposal version",
                proposal=proposal,
            )

        if now is not None and now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if now is not None and now > proposal.validUntil:
            return self.expire_proposal(proposal_id, now=now)

        if self.provider is None:
            raise ProposalError("No lookup provider configured for recheckAndConfirm")

        stored = self._offers.get((proposal.offerId, proposal.offerVersion))
        offer_req = (
            stored.offer_request
            if stored is not None and stored.offer_request is not None
            else _derive_offer_request(
                stored.offer if stored is not None else self._offer_from_proposal(proposal),
                proposal.request,
            )
        )
        lists: OrderedDict[str, list[str]] = OrderedDict(
            (k, list(v)) for k, v in shortlists(offer_req).items()
        )
        for line in proposal.lines:
            codes = lists.setdefault(line.itemId, [])
            if line.productId not in codes:
                codes.append(line.productId)
        wanted = {code for codes in lists.values() for code in codes}
        snapshot = self.provider.lookup(
            dict(lists),
            offer_req.lookupDate or proposal.requestedDate,
            proposal.customerId,
            products={code: self.products[code] for code in wanted if code in self.products},
            force_refresh=True,
        )

        check_now = now or snapshot.now
        if check_now > proposal.validUntil:
            return self.expire_proposal(proposal_id, now=check_now)

        current_mandate = self.mandates.get(proposal.customerId)
        diffs, refreshed_facts = self._recheck_differences(
            proposal,
            current_mandate=current_mandate,
            snapshot=snapshot,
            check_now=check_now,
        )

        if not diffs:
            # Re-check passed: AUTO_APPROVED | APPROVED -> RECHECKED -> CONFIRMED_SIMULATED
            self.log.record_transition(
                customer_id=proposal.customerId,
                proposal_id=proposal.proposalId,
                offer_id=proposal.offerId,
                offer_version=proposal.offerVersion,
                offer_hash=proposal.offerHash,
                mandate_version=proposal.mandateVersion,
                timestamp=check_now,
                actor="gateway:recheck",
                event="PROPOSAL_RECHECKED",
                from_state=proposal.state,
                to_state="RECHECKED",
                reasons=[ReasonCode.REQUOTED, *proposal.reasons],
                rule="P1.3",
                facts=refreshed_facts or decisive_facts(proposal.lines),
                summary=REASON_REGISTRY[ReasonCode.REQUOTED].template.format(
                    changes="mandate, prices, quantities, delivery dates, cut-offs and hard requirements unchanged"
                ),
            )
            rechecked = proposal.model_copy(update={"state": "RECHECKED"})
            self._save_proposal(rechecked)

            self.log.record_transition(
                customer_id=proposal.customerId,
                proposal_id=proposal.proposalId,
                offer_id=proposal.offerId,
                offer_version=proposal.offerVersion,
                offer_hash=proposal.offerHash,
                mandate_version=proposal.mandateVersion,
                timestamp=check_now,
                actor="gateway:simulator",
                event="ORDER_CONFIRMED_SIMULATED",
                from_state="RECHECKED",
                to_state="CONFIRMED_SIMULATED",
                reasons=proposal.reasons,
                rule="P1.3",
                facts=refreshed_facts or decisive_facts(proposal.lines),
                summary=(
                    f"Simulated order — mock environment. Confirmed {proposal.proposalId} at "
                    f"{format_money(proposal.totals.exclVat)} excl. VAT."
                ),
            )
            confirmed = rechecked.model_copy(update={"state": "CONFIRMED_SIMULATED"})
            self._save_proposal(confirmed)
            return confirmed

        # Re-check failed -> SUPERSEDED, void old approval, build Offer v(N+1) and Proposal v(N+1) from scratch
        return self._supersede_with_new_version(
            proposal,
            offer_req=offer_req,
            snapshot=snapshot,
            current_mandate=current_mandate,
            check_now=check_now,
            diffs=diffs,
            refreshed_facts=refreshed_facts,
        )

    def _offer_from_proposal(self, proposal: Proposal) -> Offer:
        return Offer(
            offerId=proposal.offerId,
            offerVersion=proposal.offerVersion,
            offerHash=proposal.offerHash,
            customerId=proposal.customerId,
            requestedDate=proposal.requestedDate,
            createdAt=proposal.createdAt,
            validUntil=proposal.validUntil,
            mode=proposal.mode,
            recordedAt=proposal.recordedAt,
            lines=proposal.lines,
            excluded=proposal.excluded,
            totals=proposal.totals,
        )

    def _supersede_with_new_version(
        self,
        proposal: Proposal,
        *,
        offer_req: GetOfferRequest,
        snapshot,
        current_mandate: Mandate,
        check_now: datetime,
        diffs: Sequence[str],
        refreshed_facts: Sequence[TrustLabel],
    ) -> Proposal:
        """Void old approval, create Offer v(N+1) + Proposal v(N+1) from scratch, and mark vN SUPERSEDED."""
        self.approvals.void_for_proposal(proposal.proposalId)

        next_version = proposal.offerVersion + 1
        new_offer_result = build_offer(
            offer_req,
            products=self.products,
            snapshot=snapshot,
            mandate=current_mandate,
            offer_id=proposal.offerId,
            offer_version=next_version,
        )
        self.register_offer(new_offer_result, offer_request=offer_req)

        partial = self._partial_items.get(proposal.proposalId, frozenset())
        new_proposal = self.create_proposal(
            CreateProposalRequest(
                customerId=proposal.customerId,
                offerId=proposal.offerId,
                offerVersion=next_version,
                request=proposal.request,
            ),
            now=check_now,
            partial_items=partial,
            auto_confirm=False,
            allow_no_order_block=True,
            replaces=proposal.proposalId,
        )

        changes_text = "; ".join(diffs)
        self.log.record_transition(
            customer_id=proposal.customerId,
            proposal_id=proposal.proposalId,
            offer_id=proposal.offerId,
            offer_version=proposal.offerVersion,
            offer_hash=proposal.offerHash,
            mandate_version=proposal.mandateVersion,
            timestamp=check_now,
            actor="gateway:recheck",
            event="PROPOSAL_SUPERSEDED",
            from_state=proposal.state,
            to_state="SUPERSEDED",
            reasons=[ReasonCode.REQUOTED],
            rule="P1.3",
            facts=list(refreshed_facts) or decisive_facts(proposal.lines),
            summary=(
                REASON_REGISTRY[ReasonCode.REQUOTED].template.format(changes=changes_text)
                + f" Superseded by {new_proposal.proposalId} (offer v{new_proposal.offerVersion}); "
                "prior approval is void."
            ),
        )
        superseded = proposal.model_copy(
            update={"state": "SUPERSEDED", "supersededBy": new_proposal.proposalId}
        )
        self._save_proposal(superseded)
        return superseded

    # CamelCase aliases matching P1.5 operation names
    getOffer = get_offer
    createProposal = create_proposal
    getProposal = get_proposal
    decideApproval = decide_approval
    recheckAndConfirm = recheck_and_confirm
    getAuditTrail = get_audit_trail


def create_proposal(
    gateway: ProposalGateway,
    request_or_customer: CreateProposalRequest | str,
    offer_id: str | None = None,
    offer_version: int | None = None,
    structured_request: StructuredRequest | None = None,
    **kwargs,
) -> Proposal:
    """Module-level helper delegating to `ProposalGateway.create_proposal`."""
    return gateway.create_proposal(
        request_or_customer,
        offer_id,
        offer_version,
        structured_request,
        **kwargs,
    )


def get_proposal(gateway: ProposalGateway, proposal_id: str, *, now: datetime | None = None) -> Proposal:
    """Module-level helper delegating to `ProposalGateway.get_proposal`."""
    return gateway.get_proposal(proposal_id, now=now)


def decide_approval(
    gateway: ProposalGateway,
    proposal_id: str,
    offer_version: int,
    approval_token: str,
    action: ApprovalAction | str,
    comment: str | None = None,
    **kwargs,
) -> Proposal:
    """Module-level helper delegating to `ProposalGateway.decide_approval`."""
    return gateway.decide_approval(proposal_id, offer_version, approval_token, action, comment, **kwargs)


def recheck_and_confirm(
    gateway: ProposalGateway,
    proposal_id: str,
    *,
    now: datetime | None = None,
) -> Proposal:
    """Module-level helper delegating to `ProposalGateway.recheck_and_confirm`."""
    return gateway.recheck_and_confirm(proposal_id, now=now)


def get_audit_trail(
    gateway: ProposalGateway,
    request: GetAuditTrailRequest | None = None,
    *,
    proposal_id: str | None = None,
    customer_id: str | None = None,
) -> AuditTrail:
    """Module-level helper delegating to `ProposalGateway.get_audit_trail`."""
    return gateway.get_audit_trail(request, proposal_id=proposal_id, customer_id=customer_id)


createProposal = create_proposal
getProposal = get_proposal
decideApproval = decide_approval
recheckAndConfirm = recheck_and_confirm
getAuditTrail = get_audit_trail

__all__ = [
    "NoOrderError",
    "OfferNotFoundError",
    "ProposalConflictError",
    "ProposalError",
    "ProposalGateway",
    "ProposalNotFoundError",
    "StoredOffer",
    "createProposal",
    "create_proposal",
    "decideApproval",
    "decide_approval",
    "format_approval_statement",
    "getAuditTrail",
    "getProposal",
    "get_audit_trail",
    "get_proposal",
    "line_input_from_offer_line",
    "recheckAndConfirm",
    "recheck_and_confirm",
]
