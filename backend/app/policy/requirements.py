"""Hard requirements (brief §6.3 as replaced by patch P2.4, X99 per P4). Static product data only.

Static search (T04) and the order policy share this module, so a requirement means the same thing everywhere.
"""

from collections.abc import Iterable
from typing import Literal

from app.catalog.categories import get_category_profile
from app.contracts import Mandate, Product, Requirement, TrustLabel, TrustSource
from app.trust.labels import (
    CLAIM_DISPLAY_NAMES,
    LABEL_DISPLAY_NAMES,
    SOURCE_CLAIM,
    claim_label,
    packaging_label,
)

from .findings import Finding

ORGANIC_LABELS: tuple[str, ...] = (
    "EU_ORGANIC_FARMING",
    "KRAV_MARK",
    "SOIL_ASSOCIATION_ORGANIC_SYMBOL",
    "OFFICIAL_ECO_LABEL_SUN",
)

FINNISH_ORIGIN_LABELS: tuple[str, ...] = (
    "GOODS_FROM_FINLAND_BLUE_SWAN",
    "MADE_IN_FINLAND_FLAG_WITH_KEY",
    "FINNISH_HEART_SYMBOL",
    "PUHTAASTI_KOTIMAINEN",
)

CERTIFICATION_ALIASES: dict[str, str] = {
    "MSC": "MARINE_STEWARDSHIP_COUNCIL_LABEL",
    "MARINE_STEWARDSHIP_COUNCIL_LABEL": "MARINE_STEWARDSHIP_COUNCIL_LABEL",
    "ASC": "AQUACULTURE_STEWARDSHIP_COUNCIL",
    "AQUACULTURE_STEWARDSHIP_COUNCIL": "AQUACULTURE_STEWARDSHIP_COUNCIL",
    "ORGANIC": "EU_ORGANIC_FARMING",
    "LUOMU": "EU_ORGANIC_FARMING",
    "EU_ORGANIC_FARMING": "EU_ORGANIC_FARMING",
    "FAIRTRADE": "FAIR_TRADE_MARK",
    "FAIR_TRADE": "FAIR_TRADE_MARK",
    "FAIR_TRADE_MARK": "FAIR_TRADE_MARK",
    "FINNISH_ORIGIN": "FINNISH_ORIGIN",
    "ORIGIN_FI": "FINNISH_ORIGIN",
    "FINNISH": "FINNISH_ORIGIN",
    "FI": "FINNISH_ORIGIN",
    "KOTIMAINEN": "FINNISH_ORIGIN",
    "SUOMALAINEN": "FINNISH_ORIGIN",
}

MISSING_LABEL_DETAILS: dict[str, str] = {
    "MARINE_STEWARDSHIP_COUNCIL_LABEL": "No MSC label in product data",
    "AQUACULTURE_STEWARDSHIP_COUNCIL": "No ASC label in product data",
    "EU_ORGANIC_FARMING": "No organic label in product data",
    "FAIR_TRADE_MARK": "No Fairtrade label in product data",
    "FINNISH_ORIGIN": "No Finnish-origin label or countryOfOrigin = FI in product data",
}

SOURCE_PRODUCT_LABELS = TrustSource(
    system="valio-aimo.product-data",
    field="labels",
    retrievedAt=None,
)

SOURCE_PRODUCT_ORIGIN = TrustSource(
    system="valio-aimo.product-data",
    field="countryOfOrigin",
    retrievedAt=None,
)

# P4: X99 proves "free from" only for the EU-14 groups of Regulation (EU) No 1169/2011 Annex II
# and these subcodes. Every other code (e.g. ML lactose, NM corn) stays unproven.
SUBCODES: dict[str, frozenset[str]] = {
    "AN": frozenset({"SA", "SH", "SC", "SP", "SR", "ST", "SW", "SM"}),
    "AW": frozenset({"UW", "GB", "NR", "GO", "GS", "GK"}),
}
PARENT = {child: parent for parent, children in SUBCODES.items() for child in children}
# Lactose (ML) is milk sugar: containing lactose implies containing milk (AM), though X99 never proves ML (P4).
IMPLIED_POSITIVE: dict[str, frozenset[str]] = {
    **SUBCODES,
    "AM": frozenset({"ML"}),
}
EU14 = frozenset({"AC", "AE", "AF", "AM", "AP", "AS", "AU", "AY", "BC", "BM", "NL", "UM", "AN", "AW"})
X99_PROVES = EU14 | frozenset(PARENT)
X99 = "X99"

# K1/K2 (brief §5.3): a free-from claim next to an explicit CONTAINS of the same substance.
CLAIM_CONFLICTS = {"K1": ("FREE_FROM_GLUTEN", "AW"), "K2": ("FREE_FROM_LACTOSE", "ML")}
K6_MESSAGE = "Dairy product declared free from milk: supplier data is inconsistent."

DAIRY_PRODUCT_TYPES: frozenset[str] = frozenset({
    "milk", "milk_drink", "cream", "condensed_milk", "milk_powder", "fermented_milk",
    "yoghurt", "sour_cream", "quark", "dairy_and_plant_dairy", "cheese", "butter",
    "ice_cream", "ice_cream_frozen_desserts",
})
PLANT_ALTERNATIVE_MARKERS: tuple[str, ...] = (
    "alternative", "alternativ", "vaihtoehto", "plant", "plantbased", "plant-based",
    "kasvi", "vegetabil", "oat", "kaura", "havre", "soy", "soya", "soija",
    "almond", "manteli", "mandel", "coconut", "kookos", "kokos", "vegan", "vegaan",
    "dairy-free", "dairy free", "milk-free", "milk free", "maidoton", "mjölkfri",
    "non-dairy", "violife", "sheese", "oddlygood", "sorbet", "sorbetti",
)

Status = Literal["CONTAINS", "MAY_CONTAIN", "UNKNOWN", "NOT_LISTED", "FREE_FROM"]
_POSITIVE = ("CONTAINS", "MAY_CONTAIN")


def _explicit(product: Product) -> dict[str, str]:
    """Declared status per GS1 code; the most severe wins when a code repeats."""
    severity = {"CONTAINS": 0, "MAY_CONTAIN": 1, "UNDECLARED": 2, "UNKNOWN": 2, "NOT_LISTED": 3, "FREE_FROM": 4}
    statuses: dict[str, str] = {}
    for declaration in product.allergens:
        current = statuses.get(declaration.code)
        if current is None or severity[declaration.status] < severity[current]:
            statuses[declaration.code] = declaration.status
    return statuses


def is_inherently_dairy_product(product: Product) -> bool:
    """Return True if the product's category profile is inherently dairy and not a vegan/plant-based alternative."""
    if "VEGAN" in product.claims or "VEGAN" in product.labels:
        return False
    text = " ".join(part for part in (product.name, *product.names.values()) if part).lower()
    if any(marker in text for marker in PLANT_ALTERNATIVE_MARKERS):
        return False
    profile = get_category_profile(product.category)
    if profile.productType in DAIRY_PRODUCT_TYPES:
        return True
    if product.category.startswith("105") and any(w in text for w in ("butter", "voi", "smör")):
        return True
    return False


def allergen_status(product: Product, code: str) -> Status:
    """What the evidence proves about allergen `code`. Anything not proven absent is not FREE_FROM."""
    explicit = _explicit(product)
    own = explicit.get(code)
    # A declared subcode (e.g. UW wheat -> AW) or ML lactose (-> AM milk) makes the target positive.
    for child in sorted(IMPLIED_POSITIVE.get(code, ())):
        if explicit.get(child) in _POSITIVE and own != "CONTAINS":
            own = "CONTAINS" if explicit[child] == "CONTAINS" else "MAY_CONTAIN"
    if own in ("UNDECLARED", "UNKNOWN"):
        return "UNKNOWN"
    if own is not None:
        return own
    parent = PARENT.get(code)
    if parent is not None and parent in explicit:
        # Free from the group proves free from its members; a positive group leaves the member open.
        return "FREE_FROM" if explicit[parent] == "FREE_FROM" else "UNKNOWN"
    if X99 in explicit:
        # P4: X99 proves only the EU-14 codes; for every other code it proves nothing, so UNKNOWN,
        # never NOT_LISTED (which STANDARD mode would accept as proven free).
        return "FREE_FROM" if code in X99_PROVES else "UNKNOWN"
    return "NOT_LISTED" if product.hasAllergenDeclaration else "UNKNOWN"


def hard_requirements(requirements: Iterable[Requirement], mandate: Mandate) -> tuple[Requirement, ...]:
    """The item's hard requirements plus the mandate's forbidden allergens, deduplicated in order."""
    combined = [*requirements, *(Requirement(kind="AVOID_ALLERGEN", code=c) for c in mandate.forbiddenAllergens)]
    return tuple(dict.fromkeys(combined))


def claim_conflicts(product: Product) -> frozenset[str]:
    """K1/K2/K6 found directly in the product data (independent of how the catalogue labels them)."""
    explicit = _explicit(product)
    marks = {
        c
        for c in ("K1", "K2", "K6")
        if any(fact.conflict and fact.conflict.code == c for fact in product.facts)
        or any(decl.evidence.conflict and decl.evidence.conflict.code == c for decl in product.allergens)
    }
    for conflict, (claim, allergen) in CLAIM_CONFLICTS.items():
        if claim in product.claims and explicit.get(allergen) == "CONTAINS":
            marks.add(conflict)
    if is_inherently_dairy_product(product) and allergen_status(product, "AM") in ("FREE_FROM", "NOT_LISTED"):
        marks.add("K6")
    return frozenset(marks)


def conflict_is_decisive(conflict: str, requirements: Iterable[Requirement]) -> bool:
    """K1/K2/K6 matter only when the buyer relies on the conflicting fact (§5.3)."""
    if conflict == "K6":
        return any(r.kind == "AVOID_ALLERGEN" and r.code == "AM" for r in requirements)
    claim, allergen = CLAIM_CONFLICTS[conflict]
    for requirement in requirements:
        if requirement.kind == "CLAIM" and requirement.code == claim:
            return True
        if requirement.kind == "AVOID_ALLERGEN" and allergen in (requirement.code, PARENT.get(requirement.code)):
            return True
    return False


def is_label_or_origin_requirement(code: str) -> bool:
    """Return True if `code` is a certification, packaging label or origin requirement (T75)."""
    norm = code.strip().upper()
    return norm in CERTIFICATION_ALIASES or (norm in LABEL_DISPLAY_NAMES and norm not in CLAIM_DISPLAY_NAMES)


def claim_requirement_fact(product: Product, code: str) -> TrustLabel:
    """Evaluate a CLAIM requirement (`code`) on `product` and return its decisive TrustLabel (T41, T75)."""
    norm = code.strip().upper()
    canonical = CERTIFICATION_ALIASES.get(norm)
    if canonical == "FINNISH_ORIGIN":
        matched_label = next((lbl for lbl in FINNISH_ORIGIN_LABELS if lbl in product.labels), None)
        has_fi_country = (product.countryOfOrigin or "").strip().lower() == "fi"
        if matched_label is not None:
            label_name = LABEL_DISPLAY_NAMES.get(matched_label, matched_label)
            disp = (
                f"Finnish-origin label: {label_name} ({matched_label}; countryOfOrigin = FI)"
                if has_fi_country
                else f"Finnish-origin label: {label_name} ({matched_label})"
            )
            existing = next((f for f in product.facts if f.fact == f"label.{matched_label}"), None)
            return TrustLabel(
                fact=f"claim.{code}",
                value=True,
                display=disp,
                level="VERIFIED",
                source=SOURCE_PRODUCT_LABELS,
                method=f"label:{matched_label}",
                conflict=existing.conflict if existing else None,
            )
        if has_fi_country:
            return TrustLabel(
                fact=f"claim.{code}",
                value=True,
                display="Finnish origin: countryOfOrigin = FI",
                level="VERIFIED",
                source=SOURCE_PRODUCT_ORIGIN,
                method="countryOfOrigin:FI",
                conflict=None,
            )
        return TrustLabel(
            fact=f"claim.{code}",
            value=False,
            display=MISSING_LABEL_DETAILS["FINNISH_ORIGIN"],
            level="UNKNOWN",
            source=SOURCE_PRODUCT_LABELS,
            method=None,
            conflict=None,
        )

    if canonical == "EU_ORGANIC_FARMING":
        matched_label = next((lbl for lbl in ORGANIC_LABELS if lbl in product.labels), None)
        if matched_label is not None:
            label_name = LABEL_DISPLAY_NAMES.get(matched_label, matched_label)
            existing = next((f for f in product.facts if f.fact == f"label.{matched_label}"), None)
            return TrustLabel(
                fact=f"claim.{code}",
                value=True,
                display=f"{label_name} ({matched_label})",
                level="VERIFIED",
                source=SOURCE_PRODUCT_LABELS,
                method=f"label:{matched_label}",
                conflict=existing.conflict if existing else None,
            )
        return TrustLabel(
            fact=f"claim.{code}",
            value=False,
            display=MISSING_LABEL_DETAILS["EU_ORGANIC_FARMING"],
            level="UNKNOWN",
            source=SOURCE_PRODUCT_LABELS,
            method=None,
            conflict=None,
        )

    if canonical is not None or (norm in LABEL_DISPLAY_NAMES and norm not in CLAIM_DISPLAY_NAMES):
        target_label = canonical or norm
        if target_label in product.labels or target_label in product.claims:
            label_name = LABEL_DISPLAY_NAMES.get(target_label, target_label)
            disp = f"{label_name} ({target_label})" if label_name != target_label else target_label
            existing = next((f for f in product.facts if f.fact == f"label.{target_label}"), None)
            return TrustLabel(
                fact=f"claim.{code}",
                value=True,
                display=disp,
                level="VERIFIED",
                source=SOURCE_PRODUCT_LABELS,
                method=f"label:{target_label}",
                conflict=existing.conflict if existing else None,
            )
        missing_detail = MISSING_LABEL_DETAILS.get(
            target_label,
            f"No {LABEL_DISPLAY_NAMES.get(target_label, target_label)} label in product data",
        )
        return TrustLabel(
            fact=f"claim.{code}",
            value=False,
            display=missing_detail,
            level="UNKNOWN",
            source=SOURCE_PRODUCT_LABELS,
            method=None,
            conflict=None,
        )

    if code in product.claims:
        return next((f for f in product.facts if f.fact == f"claim.{code}"), None) or claim_label(code)
    if code in product.labels:  # rule L4 accepts a packaging label for the claim
        label = next((f for f in product.facts if f.fact == f"label.{code}"), None) or packaging_label(code)
        return label.model_copy(update={"fact": f"claim.{code}", "value": True, "method": f"label:{code}"})
    return TrustLabel(
        fact=f"claim.{code}",
        value=False,
        display="Not claimed by the supplier",
        level="UNKNOWN",
        source=SOURCE_CLAIM,
        method=None,
        conflict=None,
    )


def check_requirements(
    product: Product,
    requirements: Iterable[Requirement],
    mandate: Mandate,
    *,
    named: bool = False,
    item_id: str | None = None,
    line_id: str | None = None,
) -> tuple[Finding, ...]:
    """Rule L4 (+ O2 for a named product). Every finding here is EXCLUDE or BLOCK: never approvable (P2.3)."""
    findings: list[Finding] = []
    forbidden = set(mandate.forbiddenAllergens)
    strict = mandate.allergenMode == "STRICT"
    conflicts = claim_conflicts(product)

    def add(code: str, detail: str, rule: str = "L4", effect: str = "EXCLUDE") -> None:
        findings.append(Finding(code, effect, rule, item_id=item_id, line_id=line_id, detail=detail))

    for requirement in hard_requirements(requirements, mandate):
        code = requirement.code
        if requirement.kind == "CLAIM":
            if is_label_or_origin_requirement(code):
                fact = claim_requirement_fact(product, code)
                if fact.value is not True:
                    add("CLAIM_MISSING", fact.display)
                continue
            # Exact code: LOW_LACTOSE never satisfies FREE_FROM_LACTOSE; X99 never proves a claim.
            if code not in product.claims and code not in product.labels:
                add("CLAIM_MISSING", code)
            continue
        status = allergen_status(product, code)
        if status == "CONTAINS":
            # O2: a product the buyer named that contains a mandate-forbidden allergen blocks the order.
            if named and code in forbidden:
                add("ALLERGEN_CONTAINS", code, rule="O2", effect="BLOCK")
            else:
                add("ALLERGEN_CONTAINS", code)
        elif status == "MAY_CONTAIN":
            add("ALLERGEN_MAY_CONTAIN", code)
        elif status == "UNKNOWN":
            add("ALLERGEN_UNKNOWN", code)
        elif code == "AM" and "K6" in conflicts:
            add("DATA_CONFLICT", "K6", rule="L9")
        elif status == "NOT_LISTED" and strict:
            add("ALLERGEN_IMPLICIT", code)

    # §6.3 STRICT: every food line needs an allergen declaration, whatever the request says.
    # Unknown food status counts as food.
    if strict and product.isFood is not False and not product.hasAllergenDeclaration:
        if not any(f.code == "ALLERGEN_UNKNOWN" for f in findings):
            add("ALLERGEN_UNKNOWN", "NO_DECLARATION")
    return tuple(findings)
