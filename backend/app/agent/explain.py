"""Decision and proposal explanation: <= 2 sentences (LLM with deterministic template fallback, §7.9, T10).

Rules:
- The agent never computes the decision; it only explains the gateway's authoritative decision.
- Every explanation is at most 2 sentences (`sentenceCount <= 2`).
- Agent-facing explanations never contain an approval URL, token or QR payload (patch P3, AC15).
- Any slow, invalid or failing LLM call falls back immediately to the deterministic template.
"""

import json
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from decimal import Decimal
from typing import Literal

from openai import OpenAI
from pydantic import Field

from app.agent.llm import OPENROUTER_BASE_URL
from app.agent.plan import requirement_display_name
from app.contracts import (
    REASON_REGISTRY,
    ExcludedProduct,
    Mandate,
    Proposal,
    ReasonCode,
    ReasonOccurrence,
    Requirement,
    TrustLabel,
    format_date,
    format_money,
    format_time,
)
from app.contracts.common import Contract
from app.policy.limits import limit_sentence
from app.policy.requirements import hard_requirements
from app.valio.vendor import HELSINKI

ExplainMode = Literal["LLM", "TEMPLATE"]
LlmExplainer = Callable[[dict], str]

EXPLAIN_DEADLINE_S = 1.5

# Split sentences on `.`, `!`, `?` followed by whitespace, without splitting decimals (`506.16`),
# `excl. VAT`, `incl. VAT`, or `no. 465039`.
_ABBREV_PLACEHOLDER = "\x00"
_ABBREV_RE = re.compile(r"\b(excl|incl|no|nr|approx|vs|e\.g|i\.e)\.", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÅ€\"'“])")
_FORBIDDEN_AGENT_PATTERNS = re.compile(
    r"https?://|/approver|/approve/|approvaltoken|approvalurl|qrpayload|\batk-[a-z0-9_-]+",
    re.IGNORECASE,
)

EXPLAIN_SYSTEM_PROMPT = """You write a concise 1–2 sentence plain-English explanation of the Trusted Trade
Gateway's authoritative decision for a professional kitchen buyer.

Rules:
- Maximum 2 sentences total. Never write more than 2 sentences.
- Never invent facts, prices, quantities, dates, or product IDs; use only the provided JSON payload.
- Never compute or change the decision: explain the gateway's decision (AUTO, ASK, or BLOCK) and reasons.
- Never include any URL, link, token, QR code, or path (such as /approver).
- If the decision is ASK, state the reason and that the proposal version waits for the named approver role.
  If a Valio-listed substitute was rejected (SUBSTITUTE_REJECTED), mention that the agent rejected it.
- If the decision is AUTO, mention that all requirements and stock are verified/declared within the mandate.
- Say that the hard requirements are proven only if every entry in "requirements" has "proven": true.
- If the decision is BLOCK, state which hard mandate limit or rule blocked the order (and if instructions in
  the request text were ignored, say so).
"""


class ExplanationResult(Contract):
    """Structured explanation output for an agent run (<= 2 sentences + rendered reason chips)."""

    text: str = Field(min_length=1)
    sentenceCount: int = Field(ge=1, le=2)
    mode: ExplainMode
    fallbackReason: str | None = None
    reasonChips: list[ReasonOccurrence] = Field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    """Split `text` into sentences while keeping `excl. VAT`, `incl. VAT`, and `no. 465039` intact."""
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return []
    protected = _ABBREV_RE.sub(lambda m: m.group(1) + _ABBREV_PLACEHOLDER, cleaned)
    raw_parts = _SENTENCE_SPLIT_RE.split(protected)
    sentences = [part.replace(_ABBREV_PLACEHOLDER, ".").strip() for part in raw_parts if part.strip()]
    return sentences


def count_sentences(text: str) -> int:
    return len(split_sentences(text))


def clamp_to_two_sentences(text: str) -> str:
    """Guarantee at most 2 sentences and terminal punctuation."""
    sentences = split_sentences(text)
    if not sentences:
        return ""
    kept = sentences[:2]
    joined = " ".join(
        s if s[-1] in ".!?" else f"{s}."
        for s in kept
    )
    return joined


def contains_forbidden_agent_content(text: str) -> bool:
    """Return True if `text` contains any URL, approver path, token or QR marker (patch P3, AC15)."""
    return bool(_FORBIDDEN_AGENT_PATTERNS.search(text))


def render_reason_occurrence(
    code: ReasonCode,
    *,
    proposal: Proposal | None = None,
    mandate: Mandate | None = None,
    clarification_question: str | None = None,
    excluded: Sequence[ExcludedProduct] = (),
) -> ReasonOccurrence:
    """Render a `ReasonCode` using `REASON_REGISTRY` and the actual proposal/mandate values."""
    defn = REASON_REGISTRY[code]
    params: dict[str, str] = {
        "total": format_money(proposal.totals.exclVat) if proposal else "the order total",
        "limit": (
            format_money(mandate.hardLimitEur if code == ReasonCode.OVER_HARD_LIMIT else mandate.autoApproveLimitEur)
            if mandate is not None
            else ("the hard limit" if code == ReasonCode.OVER_HARD_LIMIT else "the auto-approve limit")
        ),
        "requestedDate": format_date(proposal.requestedDate) if proposal else "the requested date",
        "availableDate": (
            format_date(max(ln.deliveryDate for ln in proposal.lines))
            if proposal and proposal.lines
            else "a later date"
        ),
        "closingTime": (
            format_time(proposal.lines[0].closingTime.astimezone(HELSINKI))
            if proposal and proposal.lines and proposal.lines[0].closingTime
            else "the cut-off time"
        ),
        "available": (
            f"{format(proposal.lines[0].deliveredQuantity.value.normalize(), 'f')} {proposal.lines[0].deliveredQuantity.unit}"
            if proposal and proposal.lines
            else "partial stock"
        ),
        "needed": (
            f"{format(proposal.request.items[0].quantity.normalize(), 'f')} {proposal.request.items[0].unit}"
            if proposal and proposal.request.items
            else "the requested quantity"
        ),
        "otherProduct": "a second compliant product" if proposal and len(proposal.lines) > 1 else "partial delivery",
        "n": str(len(proposal.lines)) if proposal and proposal.lines else "multiple",
        "detail": "Conflicting supplier data",
        "tradeoff": (
            f"{proposal.lines[0].name} ({proposal.lines[0].productId})"
            if proposal and proposal.lines
            else "compliant alternative"
        ),
        "original": (
            next((i.productId for i in proposal.request.items if i.productId), "the requested product")
            if proposal
            else "the requested product"
        ),
        "product": "listed substitute",
        "requirement": "hard requirements",
        "pct": "below threshold",
        "a": "product unit",
        "b": "availability unit",
        "allergen": "forbidden allergen",
        "claim": "required claim",
        "oneQuestion": clarification_question or "Please clarify the product, quantity and delivery date.",
        "changes": "mandate, prices, quantities and delivery unchanged",
    }

    ex_list = list(proposal.excluded) if proposal else list(excluded)
    rejected_sub = next(
        (e for e in ex_list if ReasonCode.SUBSTITUTE_REJECTED in e.reasons),
        None,
    )
    if rejected_sub is not None:
        params["product"] = f"{rejected_sub.productId} ({rejected_sub.name})"
        if proposal:
            item = next((i for i in proposal.request.items if i.itemId == rejected_sub.itemId), None)
            if item and item.requirements:
                params["requirement"] = ", ".join(requirement_display_name(r) for r in item.requirements)

    try:
        msg = defn.template.format(**params)
    except KeyError:
        msg = defn.chipText
    if code in (ReasonCode.OVER_AUTO_LIMIT, ReasonCode.OVER_HARD_LIMIT) and proposal and mandate:
        # T62: true also when the day, not this order alone, is over the limit.
        limit = mandate.hardLimitEur if code == ReasonCode.OVER_HARD_LIMIT else mandate.autoApproveLimitEur
        msg = limit_sentence(code, proposal.totals.exclVat, limit, proposal.requestedDate) + "."
        if code == ReasonCode.OVER_HARD_LIMIT:
            msg += " No agent or approver can override this."
    return ReasonOccurrence(code=code, effect=defn.effect, message=msg)


_PROVEN_ALLERGEN_VALUES = ("FREE_FROM", "NOT_LISTED")


def _is_proven(requirement: Requirement, fact: TrustLabel | None) -> bool:
    if fact is None or fact.level == "UNKNOWN" or fact.conflict is not None:
        return False
    if requirement.kind == "CLAIM":
        return fact.value is True or fact.value == requirement.code
    return fact.value in _PROVEN_ALLERGEN_VALUES


def requirement_evidence(proposal: Proposal, mandate: Mandate | None = None) -> list[dict]:
    """Per line and hard requirement: the evidence the gateway attached to the proposal line (T41).

    These are the requirement facts the policy decided on and the trust card shows, so the explanation
    calls the requirements proven only when this evidence proves every one of them.
    """
    items = {item.itemId: item for item in proposal.request.items}
    evidence: list[dict] = []
    for line in proposal.lines:
        item = items.get(line.itemId)
        own = item.requirements if item else []
        requirements = hard_requirements(own, mandate) if mandate is not None else tuple(dict.fromkeys(own))
        facts = {fact.fact: fact for fact in line.facts}
        for requirement in requirements:
            key = f"claim.{requirement.code}" if requirement.kind == "CLAIM" else f"allergen.{requirement.code}"
            fact = facts.get(key)
            evidence.append({
                "productId": line.productId,
                "requirement": requirement_display_name(requirement),
                "evidence": fact.display if fact else "Unknown",
                "level": fact.level if fact else "UNKNOWN",
                "proven": _is_proven(requirement, fact),
            })
    return evidence


def _requirement_names(evidence: Sequence[dict], *, proven: bool) -> list[str]:
    return list(dict.fromkeys(e["requirement"] for e in evidence if e["proven"] is proven))


def template_explanation(
    *,
    proposal: Proposal | None = None,
    mandate: Mandate | None = None,
    reasons: Sequence[ReasonCode] = (),
    clarification_question: str | None = None,
    excluded: Sequence[ExcludedProduct] = (),
) -> str:
    """Deterministic <= 2 sentence explanation of the gateway's decision."""
    active_reasons = list(proposal.reasons) if proposal is not None else list(reasons)

    # 1. No confirmable proposal (O6 clarification needed or O5 no candidate)
    if proposal is None:
        if clarification_question:
            q = clarification_question.strip()
            if not q.endswith("?"):
                q += "?"
            if ReasonCode.INSTRUCTION_IGNORED in active_reasons:
                return clamp_to_two_sentences(
                    f"Instructions in the request text were ignored. {q}"
                )
            return clamp_to_two_sentences(q)
        ex_names = ", ".join(f"{e.productId} ({e.name})" for e in list(excluded)[:2])
        if ex_names:
            return clamp_to_two_sentences(
                f"No product in the assortment meets all hard requirements and delivery terms for this request. "
                f"Closest excluded candidates: {ex_names}."
            )
        return "No product in the assortment meets all hard requirements and delivery terms for this request."

    total_str = format_money(proposal.totals.exclVat)
    date_str = format_date(proposal.requestedDate)
    approver = proposal.approverRole or (mandate.approver.role if mandate else "Head Chef (demo)")
    auto_limit_str = format_money(mandate.autoApproveLimitEur) if mandate else None
    hard_limit_str = format_money(mandate.hardLimitEur) if mandate else None

    evidence = requirement_evidence(proposal, mandate)
    unproven = _requirement_names(evidence, proven=False)
    proven_names = _requirement_names(evidence, proven=True)

    # 2. AUTO
    if proposal.decision == "AUTO":
        line_summary = ", ".join(
            f"{ln.productId} × {format(ln.quantitySalesUnits.normalize(), 'f')}"
            for ln in proposal.lines[:2]
        )
        within_str = f" within the {auto_limit_str} mandate limit" if auto_limit_str else " within the buyer mandate"
        if unproven:
            s1 = (
                f"Stock and {date_str} delivery are verified or declared for {line_summary} at {total_str} excl. "
                f"VAT{within_str}, but {', '.join(unproven)} is not proven by the product data."
            )
        else:
            named = f" ({', '.join(proven_names)})" if proven_names else ""
            s1 = (
                f"All hard requirements{named}, stock and {date_str} delivery are verified or declared for "
                f"{line_summary} at {total_str} excl. VAT{within_str}."
            )
        if ReasonCode.INSTRUCTION_IGNORED in active_reasons:
            s2 = (
                "Instructions inside the request text were ignored, and the gateway re-checked and confirmed "
                "the simulated order automatically."
            )
        else:
            s2 = "The gateway re-checked live price and availability and confirmed the simulated order automatically."
        return clamp_to_two_sentences(f"{s1} {s2}")

    # 3. BLOCK
    if proposal.decision == "BLOCK":
        if ReasonCode.OVER_HARD_LIMIT in active_reasons and mandate is not None:
            sentence = limit_sentence(
                ReasonCode.OVER_HARD_LIMIT, proposal.totals.exclVat, mandate.hardLimitEur, proposal.requestedDate
            )
            s1 = f"The gateway blocked this proposal because {sentence}."
        elif ReasonCode.OVER_HARD_LIMIT in active_reasons:
            limit_part = f"the {hard_limit_str} hard limit" if hard_limit_str else "the mandate hard limit"
            s1 = f"The gateway blocked this proposal because {total_str} excl. VAT exceeds {limit_part}."
        elif ReasonCode.ALLERGEN_CONTAINS in active_reasons:
            s1 = "The gateway blocked this proposal because the named product contains a mandate-forbidden allergen."
        else:
            s1 = f"The gateway blocked this proposal at {total_str} excl. VAT due to a non-overridable mandate rule."

        if ReasonCode.INSTRUCTION_IGNORED in active_reasons:
            s2 = (
                "Instructions in the request text to bypass rules or claim prior sign-off were ignored, "
                "and no agent or approver can override a hard limit."
            )
        else:
            s2 = "No agent or approver can override a hard mandate limit."
        return clamp_to_two_sentences(f"{s1} {s2}")

    # 4. ASK
    rejected_sub = next(
        (e for e in proposal.excluded if ReasonCode.SUBSTITUTE_REJECTED in e.reasons),
        None,
    )

    ask_parts: list[str] = []
    if ReasonCode.OVER_AUTO_LIMIT in active_reasons:
        if mandate is not None:
            diff = proposal.totals.exclVat - mandate.autoApproveLimitEur
            if diff > Decimal(0):
                ask_parts.append(
                    f"{total_str} excl. VAT is {format_money(diff)} above the {auto_limit_str} auto-approve limit"
                )
            else:
                # T62: this order alone is within the limit; the day's committed orders take it over.
                ask_parts.append(limit_sentence(
                    ReasonCode.OVER_AUTO_LIMIT, proposal.totals.exclVat, mandate.autoApproveLimitEur,
                    proposal.requestedDate,
                ))
        else:
            ask_parts.append(f"{total_str} excl. VAT is above the auto-approve limit")

    if ReasonCode.DATE_LATER in active_reasons and proposal.lines:
        earliest = max(ln.deliveryDate for ln in proposal.lines)
        ask_parts.append(f"earliest verified delivery is {format_date(earliest)} instead of {date_str}")

    if ReasonCode.PARTIAL_AVAILABILITY in active_reasons and proposal.lines:
        ln0 = proposal.lines[0]
        req0 = next((i for i in proposal.request.items if i.itemId == ln0.itemId), proposal.request.items[0])
        ask_parts.append(
            f"only {format(ln0.deliveredQuantity.value.normalize(), 'f')} {ln0.deliveredQuantity.unit} "
            f"of {format(req0.quantity.normalize(), 'f')} {req0.unit} is available in verified stock"
        )

    if ReasonCode.AGENT_SUBSTITUTE in active_reasons and proposal.lines:
        ln0 = proposal.lines[0]
        req0 = next((i for i in proposal.request.items if i.itemId == ln0.itemId), proposal.request.items[0])
        orig = req0.productId or req0.what
        ask_parts.append(
            f"{ln0.productId} ({ln0.name}) is proposed as a compliant substitute for {orig}"
        )

    if ReasonCode.VALIO_SUBSTITUTE in active_reasons and proposal.lines:
        ln0 = proposal.lines[0]
        ask_parts.append(f"{ln0.productId} ({ln0.name}) is a compliant Valio-listed substitute")

    if ReasonCode.SUPPLIER_STOCK_UNKNOWN in active_reasons:
        ask_parts.append("stock ships from the supplier's warehouse with an unverified quantity")

    if ReasonCode.LOW_RELIABILITY in active_reasons:
        ask_parts.append("historical delivery reliability is below the mandate threshold")

    if ReasonCode.QUANTITY_BASIS_WEAK in active_reasons:
        ask_parts.append("pack content was estimated from the product name")

    reason_clause = "; ".join(ask_parts) if ask_parts else f"total is {total_str} excl. VAT"
    if unproven:
        s1 = (
            f"Not every hard requirement is proven ({', '.join(unproven)}), and human sign-off is needed because "
            f"{reason_clause}."
        )
    else:
        named = f" ({', '.join(proven_names)})" if proven_names else ""
        s1 = f"Every hard requirement{named} is proven, and human sign-off is needed because {reason_clause}."

    if rejected_sub is not None:
        item = next((i for i in proposal.request.items if i.itemId == rejected_sub.itemId), None)
        req_label = (
            ", ".join(requirement_display_name(r) for r in item.requirements)
            if item and item.requirements
            else "hard requirements"
        )
        s2 = (
            f"Proposal version {proposal.offerVersion} waits for {approver}, while Valio Aimo's listed substitute "
            f"{rejected_sub.productId} ({rejected_sub.name}) was rejected for failing '{req_label}'."
        )
    else:
        s2 = f"Proposal version {proposal.offerVersion} is waiting for {approver} to sign off."

    return clamp_to_two_sentences(f"{s1} {s2}")


def make_openrouter_explainer(api_key: str, model: str, timeout_s: float = EXPLAIN_DEADLINE_S) -> LlmExplainer | None:
    """Return an OpenRouter-backed explainer callable, or None when no key is configured."""
    if not api_key:
        return None

    def explain(payload: dict) -> str:
        with OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=timeout_s, max_retries=0) as client:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0,
                max_tokens=180,
                extra_body={"thinking": {"type": "disabled"}},
            )
        content = (completion.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("empty LLM explanation")
        return content

    return explain


_explain_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm-explain")


def _call_explainer_with_deadline(explainer: LlmExplainer, payload: dict, deadline_s: float) -> str:
    future = _explain_executor.submit(explainer, payload)
    try:
        return future.result(timeout=deadline_s)
    except FutureTimeout as exc:
        future.cancel()
        raise TimeoutError(f"LLM explainer did not answer within {deadline_s:.1f} s") from exc


def explain_proposal(
    *,
    proposal: Proposal | None = None,
    mandate: Mandate | None = None,
    reasons: Sequence[ReasonCode] = (),
    clarification_question: str | None = None,
    excluded: Sequence[ExcludedProduct] = (),
    explainer: LlmExplainer | None = None,
    deadline_s: float = EXPLAIN_DEADLINE_S,
) -> ExplanationResult:
    """Produce a <= 2 sentence explanation and rendered reason chips for a gateway decision."""
    active_reasons = list(proposal.reasons) if proposal is not None else list(reasons)
    chips = [
        render_reason_occurrence(
            code,
            proposal=proposal,
            mandate=mandate,
            clarification_question=clarification_question,
            excluded=excluded,
        )
        for code in active_reasons
    ]

    fallback_text = template_explanation(
        proposal=proposal,
        mandate=mandate,
        reasons=active_reasons,
        clarification_question=clarification_question,
        excluded=excluded,
    )

    mode: ExplainMode = "TEMPLATE"
    fallback_reason: str | None = "no_llm"
    chosen_text = fallback_text

    if explainer is not None:
        payload = {
            "decision": proposal.decision if proposal else "NO_ORDER",
            "state": proposal.state if proposal else None,
            "reasons": [str(r) for r in active_reasons],
            "approverRole": proposal.approverRole if proposal else (mandate.approver.role if mandate else None),
            "totalExclVat": format_money(proposal.totals.exclVat) if proposal else None,
            "autoApproveLimit": format_money(mandate.autoApproveLimitEur) if mandate else None,
            "hardLimit": format_money(mandate.hardLimitEur) if mandate else None,
            "requestedDate": format_date(proposal.requestedDate) if proposal else None,
            "lines": [
                {
                    "productId": ln.productId,
                    "name": ln.name,
                    "quantitySalesUnits": str(ln.quantitySalesUnits),
                    "deliveredQuantity": f"{ln.deliveredQuantity.value} {ln.deliveredQuantity.unit}",
                    "lineTotalExclVat": format_money(ln.lineTotalExclVat),
                }
                for ln in (proposal.lines if proposal else ())
            ],
            "requirements": requirement_evidence(proposal, mandate) if proposal else [],
            "rejectedSubstitutes": [
                {"productId": e.productId, "name": e.name, "substituteFor": e.substituteFor}
                for e in (proposal.excluded if proposal else excluded)
                if ReasonCode.SUBSTITUTE_REJECTED in e.reasons
            ],
            "clarificationQuestion": clarification_question,
        }
        try:
            raw_text = _call_explainer_with_deadline(explainer, payload, deadline_s)
            if not isinstance(raw_text, str) or not raw_text.strip():
                raise ValueError("LLM explanation is not a non-empty string")
            clamped = clamp_to_two_sentences(raw_text)
            if not clamped or contains_forbidden_agent_content(clamped):
                raise ValueError("LLM explanation contained forbidden or empty content")
            chosen_text = clamped
            mode, fallback_reason = "LLM", None
        except TimeoutError:
            fallback_reason = "timeout"
        except (ValueError, TypeError, KeyError):
            fallback_reason = "invalid_output"
        except Exception:
            fallback_reason = "llm_error"

    sentence_count = max(1, min(2, count_sentences(chosen_text)))
    return ExplanationResult(
        text=chosen_text,
        sentenceCount=sentence_count,
        mode=mode,
        fallbackReason=fallback_reason,
        reasonChips=chips,
    )


__all__ = [
    "EXPLAIN_DEADLINE_S",
    "ExplainMode",
    "ExplanationResult",
    "LlmExplainer",
    "clamp_to_two_sentences",
    "contains_forbidden_agent_content",
    "count_sentences",
    "explain_proposal",
    "make_openrouter_explainer",
    "render_reason_occurrence",
    "split_sentences",
    "template_explanation",
]
