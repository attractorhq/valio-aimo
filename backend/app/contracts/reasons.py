"""UI copy and allowed effects, not a policy evaluator. Patch P2 overrides the brief."""

from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from .common import Contract


class ReasonCode(StrEnum):
    DATE_LATER = "DATE_LATER"
    CUTOFF_PASSED = "CUTOFF_PASSED"
    SUPPLIER_STOCK_UNKNOWN = "SUPPLIER_STOCK_UNKNOWN"
    PARTIAL_AVAILABILITY = "PARTIAL_AVAILABILITY"
    SPLIT = "SPLIT"
    DATA_CONFLICT = "DATA_CONFLICT"
    AGENT_SUBSTITUTE = "AGENT_SUBSTITUTE"
    VALIO_SUBSTITUTE = "VALIO_SUBSTITUTE"
    SUBSTITUTE_REJECTED = "SUBSTITUTE_REJECTED"
    QUANTITY_BASIS_WEAK = "QUANTITY_BASIS_WEAK"
    QUANTITY_BASIS_UNKNOWN = "QUANTITY_BASIS_UNKNOWN"
    LOW_RELIABILITY = "LOW_RELIABILITY"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    OVER_AUTO_LIMIT = "OVER_AUTO_LIMIT"
    OVER_HARD_LIMIT = "OVER_HARD_LIMIT"
    ALLERGEN_CONTAINS = "ALLERGEN_CONTAINS"
    ALLERGEN_MAY_CONTAIN = "ALLERGEN_MAY_CONTAIN"
    ALLERGEN_IMPLICIT = "ALLERGEN_IMPLICIT"
    ALLERGEN_UNKNOWN = "ALLERGEN_UNKNOWN"
    CLAIM_MISSING = "CLAIM_MISSING"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    SALES_BLOCKED = "SALES_BLOCKED"
    PRICE_UNAVAILABLE = "PRICE_UNAVAILABLE"
    INSTRUCTION_IGNORED = "INSTRUCTION_IGNORED"
    NO_CANDIDATE = "NO_CANDIDATE"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"
    REQUOTED = "REQUOTED"
    DEPOSIT_COST_UNVERIFIED = "DEPOSIT_COST_UNVERIFIED"
    VARIABLE_WEIGHT_UNVERIFIED = "VARIABLE_WEIGHT_UNVERIFIED"
    CATEGORY_NOT_ALLOWED = "CATEGORY_NOT_ALLOWED"
    MANDATE_NOT_VALID = "MANDATE_NOT_VALID"


ReasonEffect = Literal["ASK", "EXCLUDE", "BLOCK", "INFO", "NO_ORDER"]


class ReasonDefinition(Contract):
    code: ReasonCode
    effect: ReasonEffect
    approvable: bool
    chipText: str
    template: str
    allowedEffects: tuple[ReasonEffect, ...]


def _reason(code, effect, chip, template, *additional_effects):
    return ReasonDefinition(
        code=code, effect=effect, approvable=effect == "ASK", chipText=chip,
        template=template, allowedEffects=(effect, *additional_effects),
    )


REASON_REGISTRY = MappingProxyType({r.code: r for r in (
    _reason("DATE_LATER", "ASK", "Later delivery",
            "Earliest delivery {availableDate}. You asked for {requestedDate}."),
    _reason("CUTOFF_PASSED", "EXCLUDE", "Cut-off passed",
            "Order cut-off was {closingTime}, so {availableDate} delivery can't be promised. Re-quoting."),
    _reason("SUPPLIER_STOCK_UNKNOWN", "ASK", "Supplier stock",
            "Ships from the supplier's warehouse. Valio Aimo can't see the quantity."),
    _reason("PARTIAL_AVAILABILITY", "ASK", "Partial stock",
            "Only {available} in stock; {needed} needed. The rest comes from {otherProduct}."),
    _reason("SPLIT", "INFO", "Split order", "Covered by {n} products that all meet your requirements."),
    _reason("DATA_CONFLICT", "EXCLUDE", "Conflicting data", "{detail}"),
    _reason("AGENT_SUBSTITUTE", "ASK", "Agent substitute", "Not exactly what you asked for: {tradeoff}."),
    _reason("VALIO_SUBSTITUTE", "ASK", "Valio Aimo substitute",
            "Listed by Valio Aimo as a substitute for {original}.", "INFO"),
    _reason("SUBSTITUTE_REJECTED", "EXCLUDE", "Substitute rejected",
            "Valio Aimo lists {product} as a substitute, but it fails '{requirement}'."),
    _reason("QUANTITY_BASIS_WEAK", "ASK", "Pack size check",
            "Pack size read from the product name only, or whole packs well above the amount asked."),
    _reason("QUANTITY_BASIS_UNKNOWN", "EXCLUDE", "Pack size unknown",
            "Pack content is unknown, so the requested quantity cannot be sized."),
    _reason("LOW_RELIABILITY", "ASK", "Low reliability",
            "Delivered in full only {pct} of the time historically."),
    _reason("UNIT_MISMATCH", "EXCLUDE", "Unit mismatch", "Product data says {a}; availability says {b}."),
    _reason("OVER_AUTO_LIMIT", "ASK", "Above auto-approve limit",
            "{total} excl. VAT is above your {limit} auto-approve limit."),
    _reason("OVER_HARD_LIMIT", "BLOCK", "Above hard limit",
            "{total} excl. VAT is above your {limit} hard limit. No agent or approver can override this."),
    _reason("ALLERGEN_CONTAINS", "EXCLUDE", "Contains {allergen}",
            "Declared by the supplier: contains {allergen}.", "BLOCK"),
    _reason("ALLERGEN_MAY_CONTAIN", "EXCLUDE", "May contain {allergen}",
            "Declared by the supplier: may contain {allergen}."),
    _reason("ALLERGEN_IMPLICIT", "EXCLUDE", "Not declared free",
            "{allergen} isn't listed, but the supplier hasn't explicitly declared the product free from it."),
    _reason("ALLERGEN_UNKNOWN", "EXCLUDE", "No allergen data",
            "The supplier has provided no allergen information."),
    _reason("CLAIM_MISSING", "EXCLUDE", "Not {claim}", "The supplier doesn't declare this product {claim}."),
    _reason("NOT_AVAILABLE", "EXCLUDE", "Not deliverable", "Valio Aimo can't deliver this product on any date."),
    _reason("SALES_BLOCKED", "EXCLUDE", "Sales blocked", "This product can't be ordered right now."),
    _reason("PRICE_UNAVAILABLE", "EXCLUDE", "No price", "No valid price for this date."),
    _reason("INSTRUCTION_IGNORED", "INFO", "Instruction ignored",
            "The request tried to change rules or claim approval. Sign-off only comes from the approver."),
    _reason("NO_CANDIDATE", "NO_ORDER", "No match",
            "Nothing in the assortment meets all requirements. Closest options: …"),
    _reason("CLARIFICATION_NEEDED", "NO_ORDER", "Needs detail", "{oneQuestion}"),
    _reason("REQUOTED", "INFO", "Re-checked", "Re-checked live before ordering: {changes}."),
    _reason("DEPOSIT_COST_UNVERIFIED", "EXCLUDE", "Deposit not priced",
            "Returnable packaging charges are not priced, so the order total is incomplete."),
    # T68: mandate scope. Neither is a trade-off a human may sign off: the buyer admin changes the mandate instead.
    _reason("CATEGORY_NOT_ALLOWED", "EXCLUDE", "Category not allowed",
            "Category {category} is outside this mandate's allowed categories."),
    _reason("MANDATE_NOT_VALID", "BLOCK", "Mandate not valid",
            "The mandate is not valid on {date}. A buyer admin must renew it before the agent can order."),
    # Retained for migration from 0e74e98, but no longer a rejection or an approval request.
    _reason("VARIABLE_WEIGHT_UNVERIFIED", "INFO", "Variable weight",
            "Variable-weight product: 1 kg per sales unit under the challenge quantity rule."),
)})
