"""A policy finding: one reason code, its effect in context, and the rule that produced it."""

from dataclasses import dataclass

from app.contracts import REASON_REGISTRY, ReasonCode, ReasonEffect

# Brief §6.4/§6.5 rule order. Order-level reasons are listed in this order (e.g. B2:
# DATE_LATER (L5), PARTIAL_AVAILABILITY (L8), OVER_AUTO_LIMIT (O3)).
RULE_ORDER = (
    "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10", "L11", "L12", "L13", "L14",
    "O1", "O2", "O3", "O4", "O5", "O6",
)
RULE_RANK = {rule: rank for rank, rule in enumerate(RULE_ORDER)}


@dataclass(frozen=True)
class Finding:
    code: ReasonCode
    effect: ReasonEffect
    rule: str
    item_id: str | None = None
    line_id: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        # The registry's allowedEffects is the P2 boundary: e.g. CLAIM_MISSING can never be ASK.
        definition = REASON_REGISTRY[ReasonCode(self.code)]
        if self.effect not in definition.allowedEffects:
            raise ValueError(f"{self.code} cannot have effect {self.effect} (P2)")
        if self.rule not in RULE_RANK:
            raise ValueError(f"unknown policy rule {self.rule!r}")

    @property
    def approvable(self) -> bool:
        """True only for a P2.2 trade-off that a human may sign off."""
        return self.effect == "ASK" and REASON_REGISTRY[self.code].approvable


def ordered_codes(findings: "tuple[Finding, ...] | list[Finding]") -> tuple[ReasonCode, ...]:
    """Unique reason codes in rule order (stable within a rule)."""
    seen: dict[ReasonCode, None] = {}
    for finding in sorted(findings, key=lambda f: RULE_RANK[f.rule]):
        seen.setdefault(finding.code, None)
    return tuple(seen)
