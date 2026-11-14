"""End-to-end purchasing agent using ONLY the public agent channel (patch P1.6, brief §7.12, T10).

Flow:
  1. REQUEST: read buyer mandate (`getMandate`) and parse buyer text (`parse_request`).
  2. LOOKUP: static search (`searchProducts`, <= 20 per item), live price + availability probe, §7.6 ranking,
     and Valio-substitute re-check (`SUBSTITUTE_REJECTED`).
  3. ORDER: commit shortlist at fixed prices (`getOffer`).
  4. APPROVAL: submit proposal (`createProposal`), fetch authoritative state (`getProposal`), and explain the
     gateway's decision in <= 2 sentences (`explain_proposal`).

The agent never approves, confirms, or computes the AUTO / ASK / BLOCK decision.
"""

from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import Field

from app.agent.explain import ExplanationResult, LlmExplainer, explain_proposal, no_order_item_reason
from app.agent.llm import LlmInterpreter
from app.agent.parse import ParseResult, parse_request
from app.agent.plan import (
    MAX_SHORTLIST_PER_ITEM,
    AgentPlan,
    CandidateObservation,
    ClosestMatch,
    FeasibleOrder,
    ItemPlan,
    NoOrderDetails,
    NoOrderItem,
    ValioSubstituteCheck,
    build_shortlist_offer_items,
    check_valio_substitutes,
    feasible_within_hard_limit,
    keep_chosen_variants,
    normalise_item_for_planning,
    observation_from_offer_line,
    rank_candidates,
    relaxed_name_queries,
    requirement_display_name,
    select_curated_static_excluded,
    variant_choices,
)
from app.catalog.search import (
    SearchProductsRequest,
    SearchProductsResult,
    evaluate_product_static_requirements,
)
from app.catalog.vocab import expand_query_tokens, extract_inline_requirements
from app.contracts import (
    CreateProposalRequest,
    Decision,
    ExcludedProduct,
    GetOfferRequest,
    Mandate,
    Offer,
    OfferItem,
    OfferLine,
    Product,
    Proposal,
    ReasonCode,
    RequestItem,
    StructuredRequest,
    format_date,
    format_money,
)
from app.contracts.common import Contract
from app.gateway.offers import get_offer as compute_offer
from app.gateway.proposals import NoOrderError
from app.policy.requirements import hard_requirements
from app.valio import IntegrationError
from app.valio.errors import ValioRateLimited
from app.valio.record import BRIEF_PRODUCT_IDS
from app.valio.vendor import HELSINKI

if TYPE_CHECKING:
    from app.api.state import GatewayState

StepName = Literal["REQUEST", "LOOKUP", "ORDER", "APPROVAL", "ERROR"]
StepStatus = Literal["done", "waiting", "blocked"]


class AgentStepEvent(Contract):
    """One step emitted during `POST /v0.1/agent/run` for the four-step UI flow (P10 S1)."""

    step: Literal[1, 2, 3, 4]
    name: StepName
    title: Literal["Request", "Lookup", "Order", "Approval", "Error"]
    status: StepStatus
    summary: str
    request: StructuredRequest | None = None
    clarificationQuestion: str | None = None
    plan: list[ItemPlan] | None = None
    offer: Offer | None = None
    proposal: Proposal | None = None
    decision: Decision | None = None
    explanation: ExplanationResult | None = None
    reasons: list[ReasonCode] = Field(default_factory=list)
    excluded: list[ExcludedProduct] = Field(default_factory=list)
    noOrder: NoOrderDetails | None = None
    error: dict[str, Any] | None = None


class AgentRunResult(Contract):
    """Complete result of an agent run across all four steps. Contains no approval capability fields (P3, AC15)."""

    customerId: str
    mode: Literal["LIVE", "RECORDED"]
    steps: list[AgentStepEvent]
    request: StructuredRequest | None = None
    clarificationQuestion: str | None = None
    plan: AgentPlan | None = None
    offer: Offer | None = None
    proposal: Proposal | None = None
    decision: Decision | None = None
    explanation: ExplanationResult
    reasons: list[ReasonCode] = Field(default_factory=list)
    # T44: on a hard-limit BLOCK, what would fit under the limit. Information only, never ordered.
    feasibleWithinHardLimit: FeasibleOrder | None = None


def feasible_order_for(proposal: Proposal | None, mandate: Mandate, channel: object) -> FeasibleOrder | None:
    """The T44 suggestion for a BLOCKed proposal, sized in each product's allowed lots when known."""
    if proposal is None:
        return None
    products = getattr(getattr(getattr(channel, "state", None), "gateway", None), "products", None) or {}
    lots = {ln.productId: products[ln.productId].allowedLotSize for ln in proposal.lines if ln.productId in products}
    return feasible_within_hard_limit(proposal, mandate, lots)


class AgentChannel(Protocol):
    """Public agent-channel operations (P1.5). Never includes approver-channel or internal re-check methods."""

    def get_mandate(self, customer_id: str) -> Mandate:
        ...

    def search_products(
        self,
        request: SearchProductsRequest,
        *,
        customer_id: str | None = None,
    ) -> SearchProductsResult:
        ...

    def probe_offer(self, request: GetOfferRequest) -> Offer:
        ...

    def get_offer(self, request: GetOfferRequest) -> Offer:
        ...

    def create_proposal(self, request: CreateProposalRequest) -> Proposal:
        ...

    def get_proposal(self, proposal_id: str) -> Proposal:
        ...


_proposal_submission_lock = Lock()


class GatewayAgentChannel:
    """In-process adapter that calls only the public agent-channel route handlers in `app.api.agent_channel`."""

    def __init__(self, state: "GatewayState"):
        self.state = state
        self._products_by_id: dict[str, Product] = {p.productId: p for p in state.catalog}

    @property
    def products_by_id(self) -> Mapping[str, Product]:
        return self._products_by_id

    def now(self) -> datetime:
        if self.state.mode == "LIVE":
            return self.state.clock()
        rec_at = getattr(self.state.gateway.provider, "recorded_at", None)
        if isinstance(rec_at, datetime):
            return rec_at
        return self.state.clock()

    def get_mandate(self, customer_id: str) -> Mandate:
        from app.api import agent_channel

        return agent_channel.get_mandate(customer_id, self.state)

    def search_products(
        self,
        request: SearchProductsRequest,
        *,
        customer_id: str | None = None,
    ) -> SearchProductsResult:
        from app.catalog.search import search_products

        mandate = self.get_mandate(customer_id) if customer_id else None
        res = search_products(request, mandate=mandate, products=self.state.catalog)
        for cand in res.candidates:
            self._products_by_id[cand.productId] = cand
        return res

    def probe_offer(self, request: GetOfferRequest) -> Offer:
        """Read live price + availability via `offers.get_offer` without writing a throwaway audit entry."""
        if self.state.gateway.provider is None:
            return self.get_offer(request)
        mandate = self.get_mandate(request.customerId)
        return compute_offer(
            request,
            products=self.state.gateway.products,
            provider=self.state.gateway.provider,
            mandate=mandate,
        ).offer

    def get_offer(self, request: GetOfferRequest) -> Offer:
        from app.api import agent_channel

        return agent_channel.get_offer(request, self.state, customer=request.customerId)

    def create_proposal(self, request: CreateProposalRequest) -> Proposal:
        from app.api import agent_channel

        return agent_channel.create_proposal(request, self.state, customer=request.customerId)

    def find_pending_proposal(
        self, request: CreateProposalRequest, offer: Offer, mandate: Mandate,
    ) -> Proposal | None:
        request_text = " ".join(request.request.requestText.split())
        for proposal_id in reversed(self.state.proposal_ids):
            candidate = self.state.gateway.get_proposal(proposal_id)
            if (
                candidate.state == "PENDING_APPROVAL"
                and candidate.customerId == request.customerId
                and candidate.mandateVersion == mandate.version
                and candidate.offerHash == offer.offerHash
                and " ".join(candidate.request.requestText.split()) == request_text
            ):
                current = self.get_proposal(proposal_id)
                if current.state == "PENDING_APPROVAL":
                    return current
        return None

    def get_proposal(self, proposal_id: str) -> Proposal:
        from app.api import agent_channel

        return agent_channel.get_proposal(proposal_id, self.state)


class HttpNoOrderError(ValueError):
    """The public proposal endpoint returned a NO_ORDER policy result."""

    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.reasons = [ReasonCode(code) for code in details.get("reasons", [])]
        self.excluded = [ExcludedProduct.model_validate(entry) for entry in details.get("excluded", [])]


class InvalidDeliveryRetry(ValueError):
    """A delivery retry would not move the original deadline forward."""


class HttpAgentChannel:
    """HTTP client adapter that talks strictly to the public `/v0.1/*` REST endpoints."""

    def __init__(self, client: Any, *, clock: Any = None):
        self.client = client
        self._clock = clock
        self._products_by_id: dict[str, Product] = {}

    @property
    def products_by_id(self) -> Mapping[str, Product]:
        return self._products_by_id

    def now(self) -> datetime:
        if callable(self._clock):
            return self._clock()
        return datetime.now(UTC)

    @staticmethod
    def _check(response: Any) -> dict:
        if response.status_code >= 400:
            try:
                body = response.json()
                err = body.get("error") or {}
                code = err.get("code", "")
                msg = err.get("message", response.text)
            except Exception:
                code, msg = "", response.text
            if code == "VALIO_RATE_LIMITED":
                raise ValioRateLimited(err["details"]["retryAfterSeconds"])
            if code == "NO_ORDER":
                raise HttpNoOrderError(msg, err.get("details") or {})
            if code == "UPSTREAM_ERROR" or response.status_code == 502:
                raise IntegrationError(msg)
            raise ValueError(f"Agent channel HTTP {response.status_code} ({code}): {msg}")
        return response.json()

    def get_mandate(self, customer_id: str) -> Mandate:
        resp = self.client.get(f"/v0.1/mandates/{customer_id}")
        return Mandate.model_validate(self._check(resp))

    def search_products(
        self,
        request: SearchProductsRequest,
        *,
        customer_id: str | None = None,
    ) -> SearchProductsResult:
        params = {"customerId": customer_id} if customer_id else None
        resp = self.client.post(
            "/v0.1/products/search",
            params=params,
            json=request.model_dump(mode="json"),
        )
        res = SearchProductsResult.model_validate(self._check(resp))
        for cand in res.candidates:
            self._products_by_id[cand.productId] = cand
        return res

    def probe_offer(self, request: GetOfferRequest) -> Offer:
        return self.get_offer(request)

    def get_offer(self, request: GetOfferRequest) -> Offer:
        resp = self.client.post("/v0.1/offers", json=request.model_dump(mode="json"))
        return Offer.model_validate(self._check(resp))

    def create_proposal(self, request: CreateProposalRequest) -> Proposal:
        resp = self.client.post("/v0.1/proposals", json=request.model_dump(mode="json"))
        return Proposal.model_validate(self._check(resp))

    def get_proposal(self, proposal_id: str) -> Proposal:
        resp = self.client.get(f"/v0.1/proposals/{proposal_id}")
        return Proposal.model_validate(self._check(resp))


def _approval_step_summary(proposal: Proposal) -> tuple[StepStatus, str]:
    """Step 4 summary text matching P10 S1 and `frontend/src/components/flow/StepContent.tsx`."""
    if proposal.decision == "BLOCK":
        return "blocked", "Blocked by mandate"
    if proposal.decision == "AUTO" and proposal.state == "CONFIRMED_SIMULATED":
        return "done", "Agent decided → Confirmed"
    if proposal.state == "CONFIRMED_SIMULATED":
        return "done", f"Approved by {proposal.approverRole or 'approver'} → Confirmed"
    if proposal.state in ("REJECTED", "EXPIRED", "SUPERSEDED"):
        return "blocked", f"Proposal {proposal.state.lower()}"
    return "waiting", "Waiting for approver"


def _merge_excluded_reasons(*groups: Sequence[ExcludedProduct]) -> list[ExcludedProduct]:
    """Merge ExcludedProduct lists, combining reason codes when the same `(productId, itemId)` appears in both."""
    by_key: OrderedDict[tuple[str, str | None], ExcludedProduct] = OrderedDict()
    for group in groups:
        for entry in group:
            key = (entry.productId, entry.itemId)
            norm_reasons = [ReasonCode(r) for r in entry.reasons]
            if key not in by_key:
                by_key[key] = entry.model_copy(update={"reasons": norm_reasons})
            else:
                prev = by_key[key]
                combined_reasons = list(dict.fromkeys([*(ReasonCode(r) for r in prev.reasons), *norm_reasons]))
                combined_facts = list(prev.facts)
                for f in entry.facts:
                    if f not in combined_facts:
                        combined_facts.append(f)
                sub_for = prev.substituteFor or entry.substituteFor
                by_key[key] = prev.model_copy(
                    update={
                        "reasons": combined_reasons,
                        "facts": combined_facts,
                        "substituteFor": sub_for,
                    }
                )
    return list(by_key.values())


def _enrich_proposal_for_display(
    proposal: Proposal,
    *,
    extra_excluded: Sequence[ExcludedProduct],
    sub_checks_by_product: Mapping[str, Sequence[ValioSubstituteCheck]],
    extra_reasons: Sequence[ReasonCode] = (),
) -> Proposal:
    """Attach curated static exclusions and rejected Valio-substitute trust labels to the proposal lines."""
    chosen_ids = {ln.productId for ln in proposal.lines}
    filtered_extra = [
        ex
        for ex in extra_excluded
        if ex.productId not in chosen_ids
        and (ReasonCode.SUBSTITUTE_REJECTED not in ex.reasons or ex.substituteFor in chosen_ids)
    ]
    merged_excluded = _merge_excluded_reasons(proposal.excluded, filtered_extra)

    updated_lines: list[OfferLine] = []
    for ln in proposal.lines:
        checks = sub_checks_by_product.get(ln.productId, ())
        rejected_checks = [c for c in checks if not c.passed]
        base_facts = [f for f in ln.facts if f.fact != "substitute.valio"]
        if rejected_checks:
            base_facts.extend(c.trustLabel for c in rejected_checks)
        norm_line_reasons = [ReasonCode(r) for r in ln.reasons]
        updated_lines.append(ln.model_copy(update={"facts": base_facts, "reasons": norm_line_reasons}))

    merged_reasons = [ReasonCode(r) for r in proposal.reasons]
    for code in extra_reasons:
        rc = ReasonCode(code)
        if rc not in merged_reasons:
            merged_reasons.append(rc)

    return proposal.model_copy(
        update={
            "lines": updated_lines,
            "excluded": merged_excluded,
            "reasons": merged_reasons,
        }
    )


def _probe_candidates(
    channel: AgentChannel,
    *,
    customer_id: str,
    requested_date: date,
    lookup_date: date | None,
    candidates_by_item: Mapping[str, Sequence[Product]],
    items_by_id: Mapping[str, RequestItem],
) -> tuple[dict[str, dict[str, CandidateObservation]], dict[str, list[ExcludedProduct]]]:
    """Probe live price and availability for shortlisted candidates (`<= 20` per item) via `getOffer`."""
    probe_items: list[OfferItem] = []
    item_map: dict[str, str] = {}

    for item_id, cands in candidates_by_item.items():
        req_item = items_by_id[item_id]
        for cand in cands[:MAX_SHORTLIST_PER_ITEM]:
            probe_id = f"{item_id}__{cand.productId}"
            item_map[probe_id] = item_id
            probe_items.append(
                OfferItem(
                    itemId=probe_id,
                    productId=cand.productId,
                    quantity=req_item.quantity,
                    unit=req_item.unit,
                )
            )

    if not probe_items:
        return {}, {}

    probe_req = GetOfferRequest(
        customerId=customer_id,
        requestedDate=requested_date,
        items=probe_items,
        lookupDate=lookup_date,
    )
    try:
        probe_offer = channel.probe_offer(probe_req)
    except IntegrationError as exc:
        if "No recorded batch" not in str(exc):
            raise
        # In recorded mode, a fixture capture created by `scripts/record-fixtures` may contain only
        # `BRIEF_PRODUCT_IDS`. Narrow the probe to recorded brief IDs and retry.
        narrowed_items = [oi for oi in probe_items if oi.productId in BRIEF_PRODUCT_IDS]
        if not narrowed_items:
            raise
        probe_offer = channel.probe_offer(probe_req.model_copy(update={"items": narrowed_items}))

    observations: dict[str, dict[str, CandidateObservation]] = {item_id: {} for item_id in candidates_by_item}
    live_excluded: dict[str, list[ExcludedProduct]] = {item_id: [] for item_id in candidates_by_item}

    for ln in probe_offer.lines:
        orig_item_id = item_map.get(ln.itemId, ln.itemId.split("__", 1)[0])
        observations.setdefault(orig_item_id, {})[ln.productId] = observation_from_offer_line(ln)

    for ex in probe_offer.excluded:
        raw_item_id = ex.itemId or ""
        orig_item_id = item_map.get(raw_item_id, raw_item_id.split("__", 1)[0])
        rewritten = ex.model_copy(update={"itemId": orig_item_id})
        live_excluded.setdefault(orig_item_id, []).append(rewritten)
        observations.setdefault(orig_item_id, {})[ex.productId] = CandidateObservation(
            product_id=ex.productId,
            stock_tier="UNAVAILABLE",
            reasons=tuple(ex.reasons),
            facts=tuple(ex.facts),
        )

    return observations, live_excluded


def _closest_matches(
    channel: AgentChannel,
    customer_id: str,
    search: SearchProductsRequest,
    mandate: Mandate,
) -> list[ClosestMatch]:
    _, inline_requirements = extract_inline_requirements(search.query)
    requirements = list(dict.fromkeys([*search.requirements, *inline_requirements]))
    matches: dict[str, ClosestMatch] = {}
    for query in relaxed_name_queries(search.query):
        tokens, _ = expand_query_tokens(query)
        if not tokens:
            continue
        result = channel.search_products(
            search.model_copy(update={"query": query, "requirements": requirements, "productId": None}),
            customer_id=customer_id,
        )
        for product in result.candidates:
            reasons, _ = evaluate_product_static_requirements(product, requirements, mandate)
            if reasons:
                continue
            names = " ".join((product.name, *product.names.values())).lower()
            if not all(any(token in names for token in group) for group in tokens):
                continue
            matches[product.productId] = ClosestMatch(productId=product.productId, name=product.name)
            if len(matches) == 3:
                return list(matches.values())
    return list(matches.values())


def _coverage_date(lines: Sequence[OfferLine], item: RequestItem) -> date | None:
    covered = Decimal(0)
    for line in sorted(lines, key=lambda line: line.deliveryDate):
        if line.itemId == item.itemId:
            covered += line.deliveredQuantity.value
            if covered >= item.quantity:
                return line.deliveryDate
    return None


def _compliant_offer_lines(
    offer: Offer,
    request: StructuredRequest,
    products: Mapping[str, Product],
    mandate: Mandate,
) -> list[OfferLine]:
    items = {item.itemId: item for item in request.items}
    return [
        line for line in offer.lines
        if line.productId in products and line.itemId in items and not evaluate_product_static_requirements(
            products[line.productId],
            [*items[line.itemId].requirements, *extract_inline_requirements(items[line.itemId].what)[1]],
            mandate,
            named=items[line.itemId].productId == line.productId,
        )[0]
    ]


def _no_order_step(
    *,
    channel: AgentChannel,
    customer_id: str,
    request: StructuredRequest,
    mandate: Mandate,
    item_plans: Sequence[ItemPlan],
    searches: Mapping[str, SearchProductsRequest],
    candidates: Mapping[str, Sequence[Product]],
    observations: Mapping[str, Mapping[str, CandidateObservation]],
    products: Mapping[str, Product],
    excluded: list[ExcludedProduct],
    reasons: Sequence[ReasonCode],
    offer: Offer | None = None,
    offer_request: GetOfferRequest | None = None,
    now: datetime,
) -> AgentStepEvent:
    deadline = request.requestedDate
    effective_lines = _compliant_offer_lines(offer, request, products, mandate) if offer else []
    dates = [_coverage_date(effective_lines, item) for item in request.items]
    earliest = max(day for day in dates if day is not None) if all(dates) else None
    timing_codes = {ReasonCode.CUTOFF_PASSED, ReasonCode.NOT_AVAILABLE}
    timing_exclusions = [entry for entry in excluded if timing_codes.intersection(entry.reasons)]
    # A failed cut-off or empty schedule is not evidence of a later delivery. Check one
    # subsequent date on the same shortlist and only suggest a date confirmed by that quote.
    if earliest is None and timing_exclusions and offer_request and offer_request.lookupDate is None:
        expired_dates = [
            date.fromisoformat(f.value)
            for entry in timing_exclusions if ReasonCode.CUTOFF_PASSED in entry.reasons
            for f in entry.facts if f.fact == "delivery.date" and isinstance(f.value, str)
        ]
        next_day = max(deadline + timedelta(days=1), now.astimezone(HELSINKI).date(),
                       *(day + timedelta(days=1) for day in expired_dates))
        try:
            later = channel.probe_offer(offer_request.model_copy(update={"lookupDate": next_day}))
        except IntegrationError:
            later = None  # The original result is still usable if a later quote is unavailable.
        if later is not None:
            verified_lines = _compliant_offer_lines(later, request, products, mandate)
            later_dates = [_coverage_date(verified_lines, item) for item in request.items]
            dates = [before or after for before, after in zip(dates, later_dates, strict=True)]
            if all(later_dates):
                earliest = max(day for day in later_dates if day is not None)

    items = []
    for plan, delivery in zip(item_plans, dates, strict=True):
        item = plan.item
        if delivery is None:
            delivery = min(
                (candidate.deliveryDate for candidate in plan.rankedCandidates
                 if candidate.deliveryDate is not None and candidate.productId in observations.get(item.itemId, {})),
                default=None,
            )
        search = searches[item.itemId]
        _, inline_requirements = extract_inline_requirements(search.query)
        requirements = hard_requirements(
            [*search.requirements, *inline_requirements], mandate
        )
        labels = [requirement_display_name(req) for req in requirements]
        searched = search.query + (f" · {', '.join(labels)}" if labels else "")
        item_excluded = [entry for entry in excluded if entry.itemId == item.itemId]
        items.append(NoOrderItem(
            itemId=item.itemId,
            searched=searched,
            why=no_order_item_reason(
                has_candidates=bool(candidates[item.itemId]),
                deadline_label=format_date(deadline),
                delivery_label=format_date(delivery) if delivery and delivery > deadline else None,
                delivered=delivery is not None,
                excluded=item_excluded,
                requirements=requirements,
            ),
            closestMatches=(
                _closest_matches(channel, customer_id, search, mandate)
                if not candidates[item.itemId] else []
            ),
        ))
    retry_date = offer_request.lookupDate if offer_request else None
    next_date = earliest if earliest and earliest > deadline and earliest != retry_date else None
    no_order_reasons = list(dict.fromkeys([ReasonCode.NO_CANDIDATE, *reasons]))
    return AgentStepEvent(
        step=4, name="APPROVAL", title="Approval", status="blocked",
        summary="The full request could not be fulfilled",
        request=request,
        explanation=explain_proposal(reasons=no_order_reasons, excluded=excluded, mandate=mandate),
        reasons=no_order_reasons,
        excluded=excluded,
        noOrder=NoOrderDetails(items=items, earliestDelivery=next_date),
    )


def iter_agent_steps(
    customer_id: str,
    request_text: str,
    *,
    channel: AgentChannel,
    lookup_date: date | None = None,
    now: datetime | None = None,
    interpreter: LlmInterpreter | None = None,
    explainer: LlmExplainer | None = None,
    catch_stream_errors: bool = False,
) -> Iterator[AgentStepEvent]:
    """Execute the four-step purchasing flow on `channel`, yielding one `AgentStepEvent` per step."""
    try:
        yield from _iter_agent_steps_inner(
            customer_id,
            request_text,
            channel=channel,
            lookup_date=lookup_date,
            now=now,
            interpreter=interpreter,
            explainer=explainer,
        )
    except InvalidDeliveryRetry as exc:
        if not catch_stream_errors:
            raise
        yield AgentStepEvent(
            step=1, name="ERROR", title="Error", status="blocked", summary=str(exc),
            error={"code": "INVALID_DELIVERY_RETRY", "message": str(exc)},
        )
    except IntegrationError as exc:
        if not catch_stream_errors:
            raise
        err_detail = {
            "code": "UPSTREAM_UNAVAILABLE",
            "message": "Live price/availability could not be read for this shortlist; nothing was ordered.",
        }
        if isinstance(exc, ValioRateLimited):
            err_detail = {"code": exc.code, "message": str(exc)}
        yield AgentStepEvent(
            step=2,
            name="ERROR",
            title="Error",
            status="blocked",
            summary=err_detail["message"],
            error=err_detail,
        )
    except Exception:
        if not catch_stream_errors:
            raise
        err_detail = {
            "code": "AGENT_RUN_FAILED",
            "message": "The purchasing flow could not be completed; nothing was ordered.",
        }
        yield AgentStepEvent(
            step=4,
            name="ERROR",
            title="Error",
            status="blocked",
            summary=err_detail["message"],
            error=err_detail,
        )


def _iter_agent_steps_inner(
    customer_id: str,
    request_text: str,
    *,
    channel: AgentChannel,
    lookup_date: date | None,
    now: datetime | None = None,
    interpreter: LlmInterpreter | None = None,
    explainer: LlmExplainer | None = None,
) -> Iterator[AgentStepEvent]:
    mandate = channel.get_mandate(customer_id)
    eval_now = now or (channel.now() if hasattr(channel, "now") else datetime.now(UTC))

    # ------------------------------------------------------------------ Step 1: REQUEST
    parse_res: ParseResult = parse_request(request_text, eval_now, interpreter=interpreter)
    if parse_res.request is None or parse_res.clarificationQuestion is not None:
        exp = explain_proposal(
            reasons=parse_res.reasons,
            clarification_question=parse_res.clarificationQuestion,
            mandate=mandate,
            explainer=explainer,
        )
        yield AgentStepEvent(
            step=1,
            name="REQUEST",
            title="Request",
            status="blocked",
            summary=parse_res.clarificationQuestion or "Clarification needed",
            request=parse_res.request,
            clarificationQuestion=parse_res.clarificationQuestion,
            explanation=exp,
            reasons=list(parse_res.reasons),
        )
        return

    raw_request = parse_res.request
    if lookup_date is not None and lookup_date <= raw_request.requestedDate:
        raise InvalidDeliveryRetry("The retry date must be later than the original deadline.")

    # ------------------------------------------------------------------ Static Lookup per item
    search_results: dict[str, SearchProductsResult] = {}
    searches: dict[str, SearchProductsRequest] = {}
    products_by_id: dict[str, Product] = dict(getattr(channel, "products_by_id", {}) or {})

    for item in raw_request.items:
        s_req = SearchProductsRequest(
            query=item.what,
            requirements=item.requirements,
            allergenMode=mandate.allergenMode,
            forbiddenAllergens=mandate.forbiddenAllergens,
            limit=MAX_SHORTLIST_PER_ITEM,
            itemId=item.itemId,
            productId=item.productId,
        )
        s_res = channel.search_products(s_req, customer_id=customer_id)
        searches[item.itemId] = s_req
        search_results[item.itemId] = s_res
        for cand in s_res.candidates:
            products_by_id[cand.productId] = cand
            for sub_id in cand.substitutions:
                if sub_id not in products_by_id:
                    sub_res = channel.search_products(
                        SearchProductsRequest(query=sub_id, productId=sub_id, limit=5),
                        customer_id=customer_id,
                    )
                    for sc in sub_res.candidates:
                        products_by_id[sc.productId] = sc

    # Normalise item units/quantities (e.g. cases/packs -> l/kg when pack size is known)
    normalised_items: list[RequestItem] = []
    named_products: dict[str, Product | None] = {}
    extra_assumptions: list[str] = list(raw_request.assumptions)

    for item in raw_request.items:
        norm_item, named_prod, notes = normalise_item_for_planning(
            item,
            search_result=search_results[item.itemId],
            products_by_id=products_by_id,
        )
        normalised_items.append(norm_item)
        named_products[item.itemId] = named_prod
        for note in notes:
            if note not in extra_assumptions:
                extra_assumptions.append(note)

    planned_request = raw_request.model_copy(
        update={"items": normalised_items, "assumptions": extra_assumptions}
    )
    n_items = len(planned_request.items)

    yield AgentStepEvent(
        step=1,
        name="REQUEST",
        title="Request",
        status="done",
        summary=f"{n_items} item{'s' if n_items != 1 else ''} · structured",
        request=planned_request,
        reasons=list(parse_res.reasons),
    )

    # ------------------------------------------------------------------ Step 2: LOOKUP + §7.6 Ranking
    items_by_id = {item.itemId: item for item in planned_request.items}
    candidates_by_item = {
        item.itemId: search_results[item.itemId].candidates[:MAX_SHORTLIST_PER_ITEM]
        for item in planned_request.items
    }

    observations_by_item, live_excluded_by_item = _probe_candidates(
        channel,
        customer_id=customer_id,
        requested_date=planned_request.requestedDate,
        lookup_date=lookup_date,
        candidates_by_item=candidates_by_item,
        items_by_id=items_by_id,
    )

    item_plans: list[ItemPlan] = []
    all_lookup_excluded: list[ExcludedProduct] = []
    sub_checks_by_product: dict[str, list[ValioSubstituteCheck]] = {}
    final_offer_items: list[OfferItem] = []
    updated_request_items: list[RequestItem] = []
    variant_chips: list[str] = []

    for item in planned_request.items:
        s_res = search_results[item.itemId]
        cands = candidates_by_item[item.itemId]
        obs_for_item = observations_by_item.get(item.itemId, {})
        live_ex_for_item = live_excluded_by_item.get(item.itemId, [])
        named_prod = named_products.get(item.itemId)

        ranked = rank_candidates(
            cands,
            item=item,
            mandate=mandate,
            requested_date=planned_request.requestedDate,
            observations=obs_for_item,
            named_product=named_prod,
        )

        # T77: material choices the buyer left open (cream type, fresh/frozen, salt, lactose, organic, pack size)
        # become editable assumption chips; the item is never filled with two different use variants.
        choices = variant_choices(item, ranked, products_by_id)
        offer_ranked = keep_chosen_variants(ranked, choices, products_by_id)
        prefix = f"{item.what}: " if len(planned_request.items) > 1 else ""
        variant_chips.extend(f"{prefix}{c.chip}" for c in choices)

        # If the top ranked candidate is a product-type substitute (AGENT_SUBSTITUTE) and item.productId
        # was not yet set, bind item.productId to the primary requested-type candidate so the gateway's
        # L10 rule deterministically flags AGENT_SUBSTITUTE.
        effective_item = item
        if (
            ranked
            and ranked[0].substitutionSource == "AGENT"
            and effective_item.productId is None
        ):
            fallback_target = (
                named_prod.productId
                if named_prod is not None
                else next(
                    (c.productId for c in cands if c.productId != ranked[0].productId),
                    None,
                )
            )
            if fallback_target is not None:
                effective_item = effective_item.model_copy(update={"productId": fallback_target})
        updated_request_items.append(effective_item)

        chosen_ids = [rc.productId for rc in ranked[:2]]
        sub_checks: list[ValioSubstituteCheck] = []
        for cand in cands:
            if cand.productId in chosen_ids or not chosen_ids:
                checks = check_valio_substitutes(
                    cand,
                    requirements=effective_item.requirements,
                    mandate=mandate,
                    products_by_id=products_by_id,
                    item_id=effective_item.itemId,
                )
                if checks:
                    sub_checks_by_product[cand.productId] = checks
                    sub_checks.extend(checks)

        curated_static_ex = select_curated_static_excluded(
            effective_item,
            s_res,
            products_by_id,
            chosen_product_ids=chosen_ids,
        )
        rejected_sub_ex = [c.excludedEntry for c in sub_checks if c.excludedEntry is not None]
        item_excluded = _merge_excluded_reasons(rejected_sub_ex, live_ex_for_item, curated_static_ex)
        all_lookup_excluded.extend(item_excluded)

        # Live-excluded shortlist IDs that should be included in `getOffer` so `offer.excluded` records them
        recorded_or_all_live_ex = [
            ex.productId
            for ex in live_ex_for_item
            if ex.productId in products_by_id
            and (ex.productId in BRIEF_PRODUCT_IDS or len(live_ex_for_item) <= 6)
        ]
        static_strict_ex_ids = [
            ex.productId
            for ex in curated_static_ex
            if any(c in ex.reasons for c in (ReasonCode.ALLERGEN_UNKNOWN, ReasonCode.DATA_CONFLICT))
            and ex.productId in products_by_id
            and ex.productId in BRIEF_PRODUCT_IDS
            and any(r.productId in BRIEF_PRODUCT_IDS for r in ranked)
        ]

        shortlist_offer_items = build_shortlist_offer_items(
            effective_item,
            ranked_candidates=offer_ranked,
            live_excluded_ids=recorded_or_all_live_ex,
            static_strict_excluded_ids=static_strict_ex_ids,
            limit=MAX_SHORTLIST_PER_ITEM,
        )
        if not shortlist_offer_items and cands:
            # Even if all candidates were excluded live, send the top shortlist candidates so `getOffer`
            # produces a real Offer with `lines=[]` and `excluded=[...]`.
            for cand in cands[:MAX_SHORTLIST_PER_ITEM]:
                shortlist_offer_items.append(
                    OfferItem(
                        itemId=effective_item.itemId,
                        productId=cand.productId,
                        quantity=effective_item.quantity,
                        unit=effective_item.unit,
                    )
                )

        final_offer_items.extend(shortlist_offer_items)
        covered_qty = sum(
            (
                rc.deliveredQuantity.value
                for rc in ranked
                if rc.deliveredQuantity is not None and rc.substitutionSource is None
            ),
            Decimal(0),
        )
        item_plans.append(
            ItemPlan(
                item=effective_item,
                resolvedProductType=s_res.resolvedProductType,
                shortlistIds=[oi.productId for oi in shortlist_offer_items],
                rankedCandidates=ranked,
                excluded=item_excluded,
                substituteChecks=sub_checks,
                partialCoverage=Decimal(0) < covered_qty < effective_item.quantity,
            )
        )

    planned_request = planned_request.model_copy(update={
        "items": updated_request_items,
        "assumptions": list(dict.fromkeys([*planned_request.assumptions, *variant_chips])),
    })

    yield AgentStepEvent(
        step=2,
        name="LOOKUP",
        title="Lookup",
        status="done" if any(p.rankedCandidates for p in item_plans) else "blocked",
        summary="Static catalogue first, then live price + availability for the shortlist only",
        request=planned_request,
        plan=item_plans,
        excluded=all_lookup_excluded,
    )

    def no_order(
        offer: Offer | None = None,
        offer_request: GetOfferRequest | None = None,
        reasons: Sequence[ReasonCode] = (),
        policy_excluded: Sequence[ExcludedProduct] = (),
    ) -> AgentStepEvent:
        return _no_order_step(
            channel=channel, customer_id=customer_id, request=planned_request, mandate=mandate,
            item_plans=item_plans, searches=searches, candidates=candidates_by_item, products=products_by_id,
            observations=observations_by_item,
            excluded=_merge_excluded_reasons(policy_excluded, offer.excluded if offer else [], all_lookup_excluded),
            reasons=[*reasons, *parse_res.reasons], offer=offer, offer_request=offer_request, now=eval_now,
        )

    if not final_offer_items or any(not cands for cands in candidates_by_item.values()):
        yield no_order()
        return

    # ------------------------------------------------------------------ Step 3: ORDER (`getOffer`)
    offer_request = GetOfferRequest(
        customerId=customer_id,
        requestedDate=planned_request.requestedDate,
        items=final_offer_items,
        lookupDate=lookup_date,
    )
    try:
        offer = channel.get_offer(offer_request)
    except IntegrationError as exc:
        if "No recorded batch" not in str(exc):
            raise
        narrowed_offer_items = [oi for oi in final_offer_items if oi.productId in BRIEF_PRODUCT_IDS]
        if not narrowed_offer_items:
            raise
        offer_request = offer_request.model_copy(update={"items": narrowed_offer_items})
        offer = channel.get_offer(offer_request)

    compliant_lines = _compliant_offer_lines(offer, planned_request, products_by_id, mandate)
    deadline = planned_request.requestedDate
    coverage = [_coverage_date(compliant_lines, item) for item in planned_request.items]
    # Keep the existing partial-availability approval flow. When the whole request can
    # be fulfilled but an item has no on-time delivery, let the buyer choose to retry.
    misses_deadline = any(
        not any(line.itemId == item.itemId and line.deliveryDate <= deadline for line in compliant_lines)
        for item in planned_request.items
    )
    if lookup_date is None and all(coverage) and misses_deadline:
        yield no_order(offer, offer_request)
        return
    if lookup_date is not None and (
        not all(coverage) or not any(line.deliveryDate > deadline for line in compliant_lines)
    ):
        yield no_order(offer, offer_request)
        return

    # ------------------------------------------------------------------ Step 4: APPROVAL (`createProposal` -> `getProposal`)
    create_req = CreateProposalRequest(
        customerId=customer_id,
        offerId=offer.offerId,
        offerVersion=offer.offerVersion,
        request=planned_request,
    )
    local_channel = channel if isinstance(channel, GatewayAgentChannel) else None
    # Check and create together so concurrent agent runs cannot both queue a matching proposal.
    try:
        with _proposal_submission_lock if local_channel else nullcontext():
            existing = local_channel.find_pending_proposal(create_req, offer, mandate) if local_channel else None
            if existing is not None:
                enriched_proposal = existing
                offer = local_channel.state.gateway.get_registered_offer(existing.offerId, existing.offerVersion)
                planned_request = existing.request
            else:
                created = channel.create_proposal(create_req)
                proposal = channel.get_proposal(created.proposalId)
                enriched_proposal = _enrich_proposal_for_display(
                    proposal,
                    extra_excluded=all_lookup_excluded,
                    sub_checks_by_product=sub_checks_by_product,
                    extra_reasons=parse_res.reasons,
                )
                # Persist enriched display fields so subsequent proposal polls match.
                if local_channel:
                    local_channel.state.gateway._save_proposal(enriched_proposal)
    except NoOrderError as exc:
        decision = exc.policy_decision
        yield no_order(offer, offer_request, decision.reasons, decision.excluded_products())
        return
    except HttpNoOrderError as exc:
        yield no_order(offer, offer_request, exc.reasons, exc.excluded)
        return

    yield AgentStepEvent(
        step=3,
        name="ORDER",
        title="Order",
        status="done",
        summary=(
            f"Proposal at fixed price · offer v{offer.offerVersion} · "
            f"{format_money(offer.totals.exclVat)} excl. VAT"
        ),
        request=planned_request,
        offer=offer,
        excluded=offer.excluded,
    )

    exp = explain_proposal(
        proposal=enriched_proposal,
        mandate=mandate,
        explainer=explainer,
    )
    step_status, step_summary = _approval_step_summary(enriched_proposal)
    decision_obj = Decision(
        decision=enriched_proposal.decision,
        reasons=list(enriched_proposal.reasons),
        approverRole=enriched_proposal.approverRole,
    )

    yield AgentStepEvent(
        step=4,
        name="APPROVAL",
        title="Approval",
        status=step_status,
        summary=step_summary,
        request=planned_request,
        offer=offer,
        proposal=enriched_proposal,
        decision=decision_obj,
        explanation=exp,
        reasons=list(enriched_proposal.reasons),
        excluded=list(enriched_proposal.excluded),
    )


def run_agent(
    customer_id: str,
    request_text: str,
    *,
    channel: AgentChannel,
    lookup_date: date | None = None,
    now: datetime | None = None,
    interpreter: LlmInterpreter | None = None,
    explainer: LlmExplainer | None = None,
) -> AgentRunResult:
    """Run the purchasing agent through all four steps and return the complete `AgentRunResult`."""
    steps = list(
        iter_agent_steps(
            customer_id,
            request_text,
            channel=channel,
            lookup_date=lookup_date,
            now=now,
            interpreter=interpreter,
            explainer=explainer,
        )
    )
    if not steps:
        raise RuntimeError("Agent run produced no steps")

    last = steps[-1]
    step1 = steps[0]
    step2 = next((s for s in steps if s.step == 2), None)
    step3 = next((s for s in steps if s.step == 3), None)

    mandate = channel.get_mandate(customer_id)
    req = last.request or step1.request
    offer = last.offer or (step3.offer if step3 else None)
    proposal = last.proposal
    mode: Literal["LIVE", "RECORDED"] = (
        proposal.mode
        if proposal is not None
        else (offer.mode if offer is not None else getattr(getattr(channel, "state", None), "mode", "LIVE"))
    )

    plan_obj: AgentPlan | None = None
    if req is not None and step2 is not None and step2.plan is not None:
        all_offer_items: list[OfferItem] = []
        for ip in step2.plan:
            for pid in ip.shortlistIds:
                all_offer_items.append(
                    OfferItem(
                        itemId=ip.item.itemId,
                        productId=pid,
                        quantity=ip.item.quantity,
                        unit=ip.item.unit,
                    )
                )
        if all_offer_items:
            plan_obj = AgentPlan(
                customerId=customer_id,
                mandateVersion=mandate.version,
                request=req,
                items=step2.plan,
                offerRequest=GetOfferRequest(
                    customerId=customer_id,
                    requestedDate=req.requestedDate,
                    items=all_offer_items,
                    lookupDate=lookup_date,
                ),
            )

    explanation = last.explanation or explain_proposal(
        proposal=proposal,
        mandate=mandate,
        reasons=last.reasons,
        clarification_question=last.clarificationQuestion,
        excluded=last.excluded,
        explainer=explainer,
    )

    return AgentRunResult(
        customerId=customer_id,
        mode=mode,
        steps=steps,
        request=req,
        clarificationQuestion=last.clarificationQuestion,
        plan=plan_obj,
        offer=offer,
        proposal=proposal,
        decision=last.decision,
        explanation=explanation,
        reasons=list(last.reasons),
        feasibleWithinHardLimit=feasible_order_for(proposal, mandate, channel),
    )


__all__ = [
    "AgentChannel",
    "AgentRunResult",
    "AgentStepEvent",
    "GatewayAgentChannel",
    "HttpAgentChannel",
    "StepName",
    "StepStatus",
    "feasible_order_for",
    "iter_agent_steps",
    "run_agent",
]
