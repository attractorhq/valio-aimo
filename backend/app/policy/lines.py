"""Line rules L1–L14 (brief §6.4, effects per patch P2) for one candidate or offer line."""

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Literal

from app.catalog.categories import TYPE_QUALIFIER_GROUPS, product_variant, proposal_type_match
from app.contracts import (
    Mandate,
    Product,
    Quantity,
    RequestItem,
    Requirement,
    TrustLabel,
    TrustSource,
    format_date,
    format_money,
)
from app.trust.labels import CLAIM_DISPLAY_NAMES, EU14_ALLERGEN_CODES, LABEL_DISPLAY_NAMES, allergen_name

from .findings import Finding, ordered_codes
from .mandates import category_allowed
from .requirements import check_requirements, claim_conflicts, conflict_is_decisive, hard_requirements

SPLIT_PRICE_GUARD_THRESHOLD_PCT = Decimal("10")


def early_delivery_detail(available_date: date, requested_date: date) -> str | None:
    """T104: info note when a line arrives before the requested date ('Arrives Mon 16 Nov, 4 days before your Fri 20 Nov')."""
    days = (requested_date - available_date).days
    if days <= 0:
        return None
    unit = "day" if days == 1 else "days"
    return f"Arrives {format_date(available_date)}, {days} {unit} before your {format_date(requested_date)}"


def split_price_rise_pct(
    primary_price_per_unit: Decimal | None,
    top_up_price_per_unit: Decimal | None,
) -> Decimal | None:
    """Percentage by which a split top-up line's price per unit exceeds the primary line's (T69)."""
    if (
        primary_price_per_unit is None
        or top_up_price_per_unit is None
        or primary_price_per_unit <= 0
    ):
        return None
    diff = top_up_price_per_unit - primary_price_per_unit
    if diff <= 0:
        return Decimal(0)
    return ((diff / primary_price_per_unit) * Decimal(100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def split_price_guard_detail(
    primary_price_per_unit: Decimal | None,
    top_up_price_per_unit: Decimal | None,
    unit: str,
) -> str | None:
    """T69 price guard: detail line when the second product's price per kg/l is > 10 % above the original's."""
    pct = split_price_rise_pct(primary_price_per_unit, top_up_price_per_unit)
    if pct is None or pct <= SPLIT_PRICE_GUARD_THRESHOLD_PCT:
        return None
    pct_str = str(int(pct)) if pct == pct.to_integral() else format(pct.normalize(), "f")
    diff = (top_up_price_per_unit - primary_price_per_unit).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return (
        f"Price guard: +{pct_str} % per {unit} above primary line "
        f"({format_money(top_up_price_per_unit)}/{unit} vs {format_money(primary_price_per_unit)}/{unit}, "
        f"+{format_money(diff)}/{unit})"
    )


def _requirement_text(requirement: Requirement) -> str:
    if requirement.kind == "AVOID_ALLERGEN":
        return "milk-free" if requirement.code == "AM" else f"{allergen_name(requirement.code).lower()}-free"
    if requirement.code in CLAIM_DISPLAY_NAMES:  # "Lactose-free" reads "lactose-free" inside a sentence
        display = CLAIM_DISPLAY_NAMES[requirement.code]
        return display if display[:2].isupper() else display[:1].lower() + display[1:]
    return LABEL_DISPLAY_NAMES.get(requirement.code, requirement.code.replace("_", " ").capitalize())


def _fat_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def substitute_detail(
    product: Product,
    item: RequestItem,
    *,
    type_differences: tuple[str, ...] = (),
    guard_detail: str | None = None,
    allergens: tuple[str, ...] = (),
    requirements_proven: bool = True,
) -> str:
    """T115: one sentence for AGENT_SUBSTITUTE that names how the product differs from what was asked and what
    it proves ("Valio whipping cream 38 %: 38 % fat instead of 35 %; proves lactose-free; contains milk").

    Detail text only: it never changes a decision. A declared allergen is never the whole reason."""
    from app.catalog.search import product_fat_percents, requested_fat_percent  # lazy: catalog imports policy

    differences = list(type_differences)
    target_fat = requested_fat_percent(item.preferences, item.what)
    if target_fat is not None:
        fats = product_fat_percents(product)
        if not fats:
            differences.append(f"fat % not stated ({_fat_text(target_fat)} % asked)")
        elif target_fat not in fats:
            shown = " / ".join(_fat_text(fat) for fat in sorted(fats))
            differences.append(f"{shown} % fat instead of {_fat_text(target_fat)} %")
    if item.productId is not None and item.productId != product.productId:
        differences.append(f"instead of the named product {item.productId}")
    if guard_detail:
        differences.append(guard_detail)
    if not differences:
        differences.append(f"closest compliant product, not an exact match for the {item.what} asked")
    parts = ["; ".join(differences)]
    proven = list(dict.fromkeys(_requirement_text(r) for r in item.requirements))
    if proven and requirements_proven:
        parts.append("proves " + ", ".join(proven))
    if allergens:
        parts.append("contains " + ", ".join(allergens))
    sentence = "; ".join(parts)
    name = (product.name or "").strip()
    return f"{name}: {sentence}" if name else sentence[:1].upper() + sentence[1:]


class StockKind(StrEnum):
    """Stock tiers of §5.2. SUPPLIER = logisticModel Terminal: the quantity is UNKNOWN, not 999,999."""

    EXACT = "EXACT"
    PLENTY = "PLENTY"
    SUPPLIER = "SUPPLIER"
    OUT = "OUT"
    NOT_DELIVERABLE = "NOT_DELIVERABLE"


class ContentBasis(StrEnum):
    """How the content per sales unit is known (§5.2 methods 1–3)."""

    DECLARED = "DECLARED"  # (1) unit conversions
    PRICE_RATIO = "PRICE_RATIO"  # (2) catalogue ÷ comparison price; ESTIMATE but not weak
    PER_KG = "PER_KG"  # sold by KG or variable unit: 1 kg per sales unit
    NAME = "NAME"  # (3) parsed from the product name only → QUANTITY_BASIS_WEAK
    UNKNOWN = "UNKNOWN"  # cannot be sized → QUANTITY_BASIS_UNKNOWN


@dataclass(frozen=True)
class LineInput:
    """Facts the gateway (T06) assembles for one line. Live facts come from the API; `now` is its timeNow."""

    line_id: str
    item_id: str
    product: Product
    quantity_sales_units: Decimal
    delivered_quantity: Quantity
    line_total_excl_vat: Decimal
    price_valid: bool
    sales_blocked: bool
    stock: StockKind
    stock_amount: Decimal | None
    available_date: date | None
    closing_time: datetime | None
    content_basis: ContentBasis
    unit_mismatch: bool = False
    reliability: Decimal | None = None
    substitution: Literal["VALIO", "AGENT"] | None = None
    substitute_for: str | None = None
    product_type_differs: bool = False
    check_requested_type: bool = False  # Offers have no buyer type; proposals and confirmation re-checks do.
    split_top_up: bool = False
    # T100: the gateway found this top-up to be the primary line's product type, form and variant at a guarded
    # price: a second supplier of the same thing, not a substitute.
    same_as_primary: bool = False
    primary_price_per_unit: Decimal | None = None
    facts: tuple[TrustLabel, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.stock is StockKind.EXACT and self.stock_amount is None:
            raise ValueError("EXACT stock needs stock_amount")
        if self.quantity_sales_units <= 0:
            raise ValueError("quantity_sales_units must be positive")

    def covered_quantity(self) -> Decimal:
        """Physical quantity this line can deliver. Supplier stock counts in full; L7 flags it instead."""
        if self.stock in (StockKind.OUT, StockKind.NOT_DELIVERABLE):
            return Decimal(0)
        if self.stock is StockKind.EXACT and self.stock_amount < self.quantity_sales_units:
            return self.delivered_quantity.value * max(self.stock_amount, Decimal(0)) / self.quantity_sales_units
        return self.delivered_quantity.value

    def price_per_delivered_unit(self) -> Decimal | None:
        """Price excl. VAT per 1 unit of `delivered_quantity.unit` (e.g. €/kg or €/l)."""
        if not self.price_valid or self.delivered_quantity.value <= 0:
            return None
        return self.line_total_excl_vat / self.delivered_quantity.value


@dataclass(frozen=True)
class LineVerdict:
    line: LineInput
    findings: tuple[Finding, ...]

    @property
    def status(self) -> Literal["OK", "EXCLUDED", "BLOCKED"]:
        effects = {f.effect for f in self.findings}
        if "BLOCK" in effects:
            return "BLOCKED"
        if "EXCLUDE" in effects:
            return "EXCLUDED"
        return "OK"

    @property
    def reasons(self):
        """All reason codes on this line in rule order, e.g. for OfferLine.reasons."""
        return ordered_codes(self.findings)

    @property
    def exclusion_codes(self):
        """The reasons a line is excluded (what the Excluded list shows; never ASK/INFO noise)."""
        return tuple(dict.fromkeys(f.code for f in self.findings if f.effect in ("EXCLUDE", "BLOCK")))


def _conflict_codes(line: LineInput) -> frozenset[str]:
    labels = (*line.product.facts, *line.facts)
    return frozenset(label.conflict.code for label in labels if label.conflict) | claim_conflicts(line.product)


def is_exact_top_up(top_up: LineInput, primary: LineInput, item: RequestItem) -> bool:
    """T100: a top-up of exactly the requested type, form and variant at a guarded price is the same product from
    a second supplier ("Valio Emmental grated" topping up "Jokilaakson Emmental grated"), not a substitute.

    Never when the buyer named a product id: then any other product is a swap (L10)."""
    if item.productId is not None:
        return False
    names = (top_up.product.name, *top_up.product.names.values())
    primary_names = (primary.product.name, *primary.product.names.values())
    match = proposal_type_match(
        item.what, names, category=top_up.product.category,
        requirement_codes=tuple(requirement.code for requirement in item.requirements),
    )
    if not match.exact:
        return False
    if any(product_variant(group.group_id, names) != product_variant(group.group_id, primary_names)
           for group in TYPE_QUALIFIER_GROUPS):
        return False
    guard = split_price_guard_detail(
        primary.price_per_delivered_unit(), top_up.price_per_delivered_unit(), item.unit)
    return guard is None


def evaluate_line(
    line: LineInput, *, item: RequestItem, mandate: Mandate, requested_date: date, now: datetime
) -> LineVerdict:
    """Apply L1–L15 to one line. Pure: `now` is the API's timeNow, never the wall clock."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if line.item_id != item.itemId:
        raise ValueError(f"line {line.line_id} belongs to item {line.item_id}, not {item.itemId}")
    found: list[Finding] = []

    def add(code: str, effect: str, rule: str, detail: str | None = None) -> None:
        found.append(Finding(code, effect, rule, item_id=item.itemId, line_id=line.line_id, detail=detail))

    product = line.product
    type_match = proposal_type_match(
        item.what, (product.name, *product.names.values()), category=product.category,
        requirement_codes=tuple(requirement.code for requirement in item.requirements),
    ) if line.check_requested_type else None
    type_differences = type_match.differences if type_match else ()
    type_detail = "; ".join(type_differences) or None
    unrelated = type_match is not None and not type_match.related
    type_differs = line.product_type_differs or (type_match is not None and not type_match.exact)
    named = item.productId is not None and item.productId == product.productId
    exact_top_up = line.split_top_up and line.same_as_primary
    substitution = line.substitution
    if type_differs and not unrelated:
        substitution = "AGENT"  # A type-changing Valio alternative also needs a human.
    elif substitution is None and not exact_top_up and (
        line.split_top_up or (item.productId is not None and not named)
    ):
        substitution = "AGENT"  # never swap silently (L10/O2/T69)
    added_facts: list[TrustLabel] = []
    if type_differences:
        added_facts.append(TrustLabel(
            fact="product.typeGuard", value=list(type_differences), display=type_detail,
            level="DECLARED",
            source=TrustSource(system="valio-aimo.product-data", field="category, synkkaData.names", retrievedAt=None),
            method="deterministic-product-type-guard",
        ))
    allergen_details: list[str] = []
    if substitution == "AGENT":
        # Highlight declared risks, never infer that the requested type was allergen-free.
        for declaration in product.allergens:
            if declaration.code in EU14_ALLERGEN_CODES and declaration.status == "CONTAINS":
                detail = f"Contains {allergen_name(declaration.code)}"
                allergen_details.append(detail)
                added_facts.append(declaration.evidence.model_copy(update={
                    "fact": f"substitute.allergen.{declaration.code}", "display": detail, "value": "CONTAINS",
                }))
    if added_facts or substitution != line.substitution:
        replaced_keys = {fact.fact for fact in added_facts}
        line = replace(line, substitution=substitution, product_type_differs=type_differs,
                       facts=tuple(fact for fact in line.facts if fact.fact not in replaced_keys) + tuple(added_facts))
    conflicts = _conflict_codes(line)
    requirements = hard_requirements(item.requirements, mandate)
    cutoff_passed = line.closing_time is not None and line.closing_time < now
    guard_detail = (
        split_price_guard_detail(line.primary_price_per_unit, line.price_per_delivered_unit(), item.unit)
        if line.split_top_up
        else None
    )

    # L1–L3: orderable at a known, complete price.
    if line.sales_blocked:
        add("SALES_BLOCKED", "EXCLUDE", "L1")
    if line.stock in (StockKind.OUT, StockKind.NOT_DELIVERABLE) or line.available_date is None or (
        line.stock is StockKind.EXACT and line.stock_amount <= 0
    ):
        add("NOT_AVAILABLE", "EXCLUDE", "L2")
    if not line.price_valid:
        add("PRICE_UNAVAILABLE", "EXCLUDE", "L3")
    if any(deposit.unitPriceExclVat is None for deposit in product.deposits):
        add("DEPOSIT_COST_UNVERIFIED", "EXCLUDE", "L3")  # an unpriced deposit makes the total incomplete

    # L15 (T68): the mandate may limit what the agent buys to some catalogue categories. Never approvable.
    if not category_allowed(mandate, product.category):
        add("CATEGORY_NOT_ALLOWED", "EXCLUDE", "L15",
            f"Category {product.category or 'unknown'} is not in the allowed list "
            f"({', '.join(mandate.allowedCategories)})")

    # L4 (+ O2): hard requirements are proven or the line is out. Never approvable.
    requirement_findings = check_requirements(
        product, item.requirements, mandate, named=named, item_id=item.itemId, line_id=line.line_id
    )
    found.extend(requirement_findings)

    # L5–L7: delivery.
    if line.available_date is not None and line.available_date > requested_date:
        add("DATE_LATER", "ASK", "L5")
    if mandate.enforceCutoff and (cutoff_passed or "K3" in conflicts):
        add("CUTOFF_PASSED", "EXCLUDE", "L6")  # P2.3: cannot be promised, so not approvable
    if line.stock is StockKind.SUPPLIER and not mandate.autoAcceptSupplierStock:
        add("SUPPLIER_STOCK_UNKNOWN", "ASK", "L7")

    # L8 is item-level (coverage across lines) and lives in decide(); a split top-up line also carries SPLIT (T69).
    if line.split_top_up:
        add("SPLIT", "INFO", "L8", guard_detail)

    # L9: a CONFLICT on a decisive fact. K5 (pack content) always sizes the order.
    for conflict in sorted(conflicts & {"K1", "K2", "K5"}):
        if conflict == "K5" or conflict_is_decisive(conflict, requirements):
            add("DATA_CONFLICT", "EXCLUDE", "L9", conflict)

    # L10–L11: substitutes must prove every hard requirement.
    if substitution == "AGENT":
        detail_differences = type_differences
        if type_match is None:  # detail only: the decision never depends on this re-check
            detail_differences = proposal_type_match(
                item.what, (product.name, *product.names.values()), category=product.category,
                requirement_codes=tuple(requirement.code for requirement in item.requirements),
            ).differences
        add("AGENT_SUBSTITUTE", "ASK", "L10", substitute_detail(
            product, item, type_differences=detail_differences, guard_detail=guard_detail,
            allergens=tuple(detail.removeprefix("Contains ").lower() for detail in allergen_details),
            requirements_proven=not requirement_findings,
        ))
    if substitution == "VALIO":
        if requirement_findings:
            add("SUBSTITUTE_REJECTED", "EXCLUDE", "L11", requirement_findings[0].detail)
        elif mandate.autoAcceptValioSubstitutes:
            add("VALIO_SUBSTITUTE", "INFO", "L11")
        else:
            add("VALIO_SUBSTITUTE", "ASK", "L11")

    # L12: can the requested quantity be sized?
    if line.content_basis is ContentBasis.NAME:
        add("QUANTITY_BASIS_WEAK", "ASK", "L12")
    elif line.content_basis is ContentBasis.UNKNOWN:
        add("QUANTITY_BASIS_UNKNOWN", "EXCLUDE", "L12")

    # L13: unknown reliability is information only.
    if line.reliability is not None and line.reliability < mandate.minReliability:
        add("LOW_RELIABILITY", "ASK", "L13")

    # L14 (K4): units that cannot be converted exactly.
    if line.unit_mismatch or "K4" in conflicts or unrelated:
        add("UNIT_MISMATCH", "EXCLUDE", "L14", type_detail if unrelated else None)

    return LineVerdict(line=line, findings=tuple(found))
