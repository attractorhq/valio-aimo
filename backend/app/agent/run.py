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
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import Field

from app.agent.explain import ExplanationResult, LlmExplainer, explain_proposal
from app.agent.llm import LlmInterpreter
from app.agent.parse import ParseResult, parse_request
from app.agent.plan import (
    MAX_SHORTLIST_PER_ITEM,
    AgentPlan,
    CandidateObservation,
    FeasibleOrder,
    ItemPlan,
    ValioSubstituteCheck,
    build_shortlist_offer_items,
    check_valio_substitutes,
    feasible_within_hard_limit,
    normalise_item_for_planning,
    observation_from_offer_line,
    rank_candidates,
    select_curated_static_excluded,
)
from app.catalog.search import SearchProductsRequest, SearchProductsResult
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
    format_money,
)
from app.contracts.common import Contract
from app.gateway.offers import get_offer as compute_offer
from app.gateway.proposals import NoOrderError
from app.valio import IntegrationError
from app.valio.errors import ValioRateLimited
from app.valio.record import BRIEF_PRODUCT_IDS

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
        from app.api import agent_channel

        res = agent_channel.search(request, self.state, customerId=customer_id)
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

        return agent_channel.get_offer(request, self.state)

    def create_proposal(self, request: CreateProposalRequest) -> Proposal:
        from app.api import agent_channel

        return agent_channel.create_proposal(request, self.state)

    def get_proposal(self, proposal_id: str) -> Proposal:
        from app.api import agent_channel

        return agent_channel.get_proposal(proposal_id, self.state)


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
        return "done", "Agent decided → Confirmed (simulated)"
    if proposal.state == "CONFIRMED_SIMULATED":
        return "done", f"Approved by {proposal.approverRole or 'approver'} → Confirmed (simulated)"
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
    requested_date: Any,
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
        probe_offer = channel.probe_offer(
            GetOfferRequest(
                customerId=customer_id,
                requestedDate=requested_date,
                items=narrowed_items,
            )
        )

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


def iter_agent_steps(
    customer_id: str,
    request_text: str,
    *,
    channel: AgentChannel,
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
            now=now,
            interpreter=interpreter,
            explainer=explainer,
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

    # ------------------------------------------------------------------ Static Lookup per item
    search_results: dict[str, SearchProductsResult] = {}
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
        candidates_by_item=candidates_by_item,
        items_by_id=items_by_id,
    )

    item_plans: list[ItemPlan] = []
    all_lookup_excluded: list[ExcludedProduct] = []
    sub_checks_by_product: dict[str, list[ValioSubstituteCheck]] = {}
    final_offer_items: list[OfferItem] = []
    updated_request_items: list[RequestItem] = []

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
            ranked_candidates=ranked,
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

    planned_request = planned_request.model_copy(update={"items": updated_request_items})

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

    if not final_offer_items:
        exp = explain_proposal(
            reasons=[ReasonCode.NO_CANDIDATE, *parse_res.reasons],
            excluded=all_lookup_excluded,
            mandate=mandate,
            explainer=explainer,
        )
        yield AgentStepEvent(
            step=4,
            name="APPROVAL",
            title="Approval",
            status="blocked",
            summary="No compliant product available",
            request=planned_request,
            explanation=exp,
            reasons=[ReasonCode.NO_CANDIDATE, *parse_res.reasons],
            excluded=all_lookup_excluded,
        )
        return

    # ------------------------------------------------------------------ Step 3: ORDER (`getOffer`)
    offer_request = GetOfferRequest(
        customerId=customer_id,
        requestedDate=planned_request.requestedDate,
        items=final_offer_items,
    )
    try:
        offer = channel.get_offer(offer_request)
    except IntegrationError as exc:
        if "No recorded batch" not in str(exc):
            raise
        narrowed_offer_items = [oi for oi in final_offer_items if oi.productId in BRIEF_PRODUCT_IDS]
        offer_request = GetOfferRequest(
            customerId=customer_id,
            requestedDate=planned_request.requestedDate,
            items=narrowed_offer_items,
        )
        offer = channel.get_offer(offer_request)

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

    # ------------------------------------------------------------------ Step 4: APPROVAL (`createProposal` -> `getProposal`)
    create_req = CreateProposalRequest(
        customerId=customer_id,
        offerId=offer.offerId,
        offerVersion=offer.offerVersion,
        request=planned_request,
    )
    try:
        created = channel.create_proposal(create_req)
        proposal = channel.get_proposal(created.proposalId)
    except (NoOrderError, ValueError):
        reasons_no_order = [ReasonCode.NO_CANDIDATE, *parse_res.reasons]
        merged_ex = _merge_excluded_reasons(offer.excluded, all_lookup_excluded)
        exp = explain_proposal(
            reasons=reasons_no_order,
            excluded=merged_ex,
            mandate=mandate,
            explainer=explainer,
        )
        yield AgentStepEvent(
            step=4,
            name="APPROVAL",
            title="Approval",
            status="blocked",
            summary="No confirmable order",
            request=planned_request,
            offer=offer,
            explanation=exp,
            reasons=reasons_no_order,
            excluded=merged_ex,
        )
        return

    enriched_proposal = _enrich_proposal_for_display(
        proposal,
        extra_excluded=all_lookup_excluded,
        sub_checks_by_product=sub_checks_by_product,
        extra_reasons=parse_res.reasons,
    )
    # Persist enriched display fields on in-process gateway so subsequent `getProposal` polls match
    if isinstance(channel, GatewayAgentChannel):
        channel.state.gateway._save_proposal(enriched_proposal)

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
