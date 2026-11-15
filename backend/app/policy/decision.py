"""Order rules O1–O6 and the decision (brief §6.5–§6.6, patch P2). The only place AUTO/ASK/BLOCK is computed."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from app.contracts import (
    Decision,
    ExcludedProduct,
    Mandate,
    ReasonCode,
    StructuredRequest,
    format_money,
)

from .findings import Finding, ordered_codes
from .instructions import detect_instruction
from .limits import format_days
from .lines import LineInput, LineVerdict, evaluate_line
from .mandates import window_violation

Outcome = Literal["AUTO", "ASK", "BLOCK", "NO_ORDER"]


@dataclass(frozen=True)
class PolicyDecision:
    """`NO_ORDER` (O5/O6) means there is nothing to confirm or approve: no proposal is created."""

    outcome: Outcome
    findings: tuple[Finding, ...]
    lines: tuple[LineVerdict, ...]
    excluded: tuple[LineVerdict, ...]
    total_excl_vat: Decimal
    mandate_version: int
    approver_role: str | None
    # T62: what this customer already has committed (confirmed or awaiting sign-off) for the same delivery date.
    committed_excl_vat: Decimal = Decimal(0)

    @property
    def reasons(self) -> tuple[ReasonCode, ...]:
        """Order-level reasons in rule order: what the proposal shows, never the excluded candidates' codes."""
        return ordered_codes(self.findings)

    @property
    def approvable_reasons(self) -> tuple[ReasonCode, ...]:
        return ordered_codes([f for f in self.findings if f.approvable])

    def to_decision(self) -> Decision:
        if self.outcome == "NO_ORDER":
            raise ValueError("NO_ORDER has no confirmable decision; do not create a proposal")
        return Decision(decision=self.outcome, reasons=list(self.reasons), approverRole=self.approver_role)

    def excluded_products(self) -> list[ExcludedProduct]:
        return [
            ExcludedProduct(
                productId=v.line.product.productId,
                name=v.line.product.name,
                itemId=v.line.item_id,
                reasons=list(v.exclusion_codes),
                facts=list(v.line.facts),
                substituteFor=v.line.substitute_for,
            )
            for v in self.excluded
        ]


def _outcome(findings: Iterable[Finding]) -> Outcome:
    effects = {f.effect for f in findings}
    if "BLOCK" in effects:
        return "BLOCK"
    if "NO_ORDER" in effects:
        return "NO_ORDER"
    if "ASK" in effects:
        return "ASK"
    return "AUTO"


OVERBUY_FACTOR = Decimal("1.25")


def _plain(value: Decimal) -> str:
    return f"{value.normalize():,f}"


def overbuy_detail(covered: Decimal, asked: Decimal, unit: str, lines: Sequence[LineInput]) -> str:
    """'40 kg for 25 kg asked: whole 20 kg packs'."""
    packs = {line.delivered_quantity.value / line.quantity_sales_units for line in lines}
    pack = f": whole {_plain(packs.pop())} {unit} packs" if len(packs) == 1 else ": whole packs"
    return f"{_plain(covered)} {unit} for {_plain(asked)} {unit} asked{pack}"


def decide(
    *,
    mandate: Mandate,
    request: StructuredRequest,
    lines: Sequence[LineInput],
    now: datetime,
    partial_items: frozenset[str] = frozenset(),
    instruction_ignored: bool = False,
    clarification_needed: bool = False,
    committed_excl_vat: Decimal = Decimal(0),
    committed_days: Sequence[date] = (),
) -> PolicyDecision:
    """Decide one order (offer or proposal version) from scratch.

    - `lines` are the order's lines. Any line that now fails a non-approvable rule is dropped into
      `excluded` and no longer counts towards its item, so a re-check can never confirm it.
    - `partial_items` are items this proposal version explicitly offers in part (P2.2/P2.5). A shortfall
      anywhere else is not something a human may sign off: it is NO_ORDER · NO_CANDIDATE.
    - `instruction_ignored` / `clarification_needed` come from the request parser; O4 is also detected here.
    - `committed_excl_vat` (T62) is the total the customer already has committed for delivery on this order's
      delivery days (confirmed or awaiting sign-off), passed in by the gateway: the limits apply to it plus this
      order, so a need split into several small orders can never pass the auto-approve or hard limit unnoticed.
      `committed_days` are the delivery days that total falls on (for the wording; the requested date if empty).
    """
    items = {item.itemId: item for item in request.items}
    for line in lines:
        if line.item_id not in items:
            raise ValueError(f"line {line.line_id} references unknown item {line.item_id}")
        if line.delivered_quantity.unit != items[line.item_id].unit:
            raise ValueError(f"line {line.line_id} is sized in {line.delivered_quantity.unit}, "
                             f"item asks for {items[line.item_id].unit}")
    if unknown := partial_items - items.keys():
        raise ValueError(f"partial_items reference unknown items {sorted(unknown)}")

    verdicts = [
        evaluate_line(line, item=items[line.item_id], mandate=mandate, requested_date=request.requestedDate, now=now)
        for line in lines
    ]
    kept = tuple(v for v in verdicts if v.status != "EXCLUDED")
    excluded = tuple(v for v in verdicts if v.status == "EXCLUDED")
    # A blocked line contributes only its BLOCK reason; its other findings would not change anything.
    findings: list[Finding] = [
        f for v in kept for f in v.findings if v.status == "OK" or f.effect == "BLOCK"
    ]

    # L8 / O5: is every item fully covered by lines that pass every hard requirement?
    for item in request.items:
        item_lines = [v.line for v in kept if v.line.item_id == item.itemId]
        covered = sum((line.covered_quantity() for line in item_lines), Decimal(0))
        if covered <= 0:
            findings.append(Finding("NO_CANDIDATE", "NO_ORDER", "O5", item_id=item.itemId))
        elif covered < item.quantity:
            if item.itemId in partial_items:
                findings.append(Finding("PARTIAL_AVAILABILITY", "ASK", "L8", item_id=item.itemId))
            else:
                findings.append(Finding("NO_CANDIDATE", "NO_ORDER", "O5", item_id=item.itemId,
                                        detail="full quantity not available"))
        elif len(item_lines) > 1:
            findings.append(Finding("SPLIT", "INFO", "L8", item_id=item.itemId))
        # L12 (T100): whole-pack rounding far above the amount asked is a quantity trade-off for a human,
        # e.g. 25 kg of oat flakes delivered as 2 × 20 kg = 40 kg.
        if covered > item.quantity * OVERBUY_FACTOR:
            findings.append(Finding("QUANTITY_BASIS_WEAK", "ASK", "L12", item_id=item.itemId,
                                    detail=overbuy_detail(covered, item.quantity, item.unit, item_lines)))

    # O1 / O3: limits on the total excl. VAT of the lines that remain, plus what is already committed for
    # delivery on the same day (T62: split-order guard).
    total = sum((v.line.line_total_excl_vat for v in kept), Decimal(0))
    exposure = total + committed_excl_vat
    context = (
        f"{format_money(committed_excl_vat)} already committed for delivery on "
        f"{format_days(committed_days or (request.requestedDate,))}; with this order {format_money(exposure)}"
        if committed_excl_vat > 0 else None
    )
    if exposure > mandate.hardLimitEur:
        detail = f"{context} > {format_money(mandate.hardLimitEur)} hard limit" if context else None
        findings.append(Finding("OVER_HARD_LIMIT", "BLOCK", "O1", detail=detail))
    elif exposure > mandate.autoApproveLimitEur:
        detail = f"{context} > {format_money(mandate.autoApproveLimitEur)} auto-approve limit" if context else None
        findings.append(Finding("OVER_AUTO_LIMIT", "ASK", "O3", detail=detail))

    # O0 (T68): outside its validity window the mandate authorises nothing, not even with a human sign-off.
    if (expired := window_violation(mandate, now)) is not None:
        findings.append(Finding("MANDATE_NOT_VALID", "BLOCK", "O0", detail=expired))

    # O4: the request text is data. O6: unclear or contradictory requests are asked about, not assumed.
    if instruction_ignored or detect_instruction(request.requestText):
        findings.append(Finding("INSTRUCTION_IGNORED", "INFO", "O4"))
    if clarification_needed:
        findings.append(Finding("CLARIFICATION_NEEDED", "NO_ORDER", "O6"))

    # P2: Finding refuses any effect the registry does not allow, so an ASK can only stem from a
    # P2.2 trade-off; test_policy_properties checks this exhaustively.
    outcome = _outcome(findings)
    return PolicyDecision(
        outcome=outcome,
        findings=tuple(findings),
        lines=kept,
        excluded=excluded,
        total_excl_vat=total,
        mandate_version=mandate.version,
        approver_role=mandate.approver.role if outcome == "ASK" else None,
        committed_excl_vat=committed_excl_vat,
    )
