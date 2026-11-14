"""Agent planner: shortlist (<= 20 per item), §7.6 ranking, Valio-substitute check, and agent substitutes (T10).

 Pure domain module:
- No network calls and no `datetime.now()`; `now` is passed in when needed.
- Uses `Decimal` for all prices and quantities.
- Never computes the gateway's AUTO / ASK / BLOCK decision.
- Re-checks every Valio-listed substitute against every hard requirement (§7.8, patch P8 B); any substitute
  that fails a requirement is marked `SUBSTITUTE_REJECTED` for the line's trust card and `Excluded`.
- Proposes agent substitutes (`AGENT_SUBSTITUTE`) only when every hard requirement is proven (patch P2.2/P2.3).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import Field

from app.catalog.categories import (
    DAIRY_CONCEPTS,
    VARIANT_CHOICE_LABELS,
    TypeMatch,
    missing_family_nouns,
    product_type_match,
    product_variant,
    same_product_family,
    stated_groups,
)
from app.catalog.normalise import apply_price_ratio_content
from app.catalog.search import (
    SearchProductsResult,
    evaluate_product_static_requirements,
    narrow_candidates_by_fat_preference,
    parse_fat_percent,
    product_fat_percents,
    requested_fat_percent,
)
from app.catalog.vocab import (
    CONCEPTS_BY_ID,
    ProductConcept,
    extract_inline_requirements,
    extract_requested_form,
    match_concept_in_text,
    product_form_difference,
    resolve_query_concepts,
    split_query_alternatives,
)
from app.contracts import (
    ExcludedProduct,
    GetOfferRequest,
    Mandate,
    OfferItem,
    OfferLine,
    Preference,
    Product,
    Proposal,
    Quantity,
    ReasonCode,
    RequestItem,
    Requirement,
    StructuredRequest,
    TrustLabel,
    format_date,
    format_money,
)
from app.contracts.common import Contract
from app.gateway.offers import (
    PIECE_UNITS,
    VOLUME_UNITS,
    WEIGHT_UNITS,
     Sizing,
    _floor_to_lot,
    _partial_stock_pack_explanation,
    size,
)
from app.policy.requirements import check_requirements, hard_requirements
from app.trust.labels import SOURCE_SUBSTITUTION, allergen_name
from app.valio.facts import SENTINEL_METHOD, is_stock_sentinel
from app.valio.record import BRIEF_PRODUCT_IDS

MAX_SHORTLIST_PER_ITEM = 20
ORGANIC_LABELS = frozenset({"EU_ORGANIC_FARMING", "KRAV_MARK", "OFFICIAL_ECO_LABEL_SUN"})
_WORD_RE = re.compile(r"[\wåäöÅÄÖ%-]+", re.UNICODE)
_PACK_VALUE_RE = re.compile(r"^(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|g|l|ml|dl|pcs|cases)$", re.IGNORECASE)
_FAT_IN_NAME_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)

CandidateTier = Literal["RECOMMENDED", "BEST", "BETTER", "GOOD", "ALTERNATIVE"]
SubstitutionKind = Literal["VALIO", "AGENT"]

_REQUIREMENT_DISPLAY: dict[str, str] = {
    "FREE_FROM_GLUTEN": "gluten-free",
    "FREE_FROM_LACTOSE": "lactose-free",
    "VEGAN": "vegan",
    "VEGETARIAN": "vegetarian",
    "LOW_LACTOSE": "low-lactose",
    "FREE_FROM_EGGS": "egg-free",
    "FREE_FROM_SOY": "soy-free",
    "NO_ADDED_SUGARS": "no added sugars",
}

_NON_ORDERABLE_CODES: frozenset[ReasonCode] = frozenset({
    ReasonCode.SALES_BLOCKED,
    ReasonCode.NOT_AVAILABLE,
    ReasonCode.PRICE_UNAVAILABLE,
    ReasonCode.DEPOSIT_COST_UNVERIFIED,
    ReasonCode.CLAIM_MISSING,
    ReasonCode.ALLERGEN_CONTAINS,
    ReasonCode.ALLERGEN_MAY_CONTAIN,
    ReasonCode.ALLERGEN_IMPLICIT,
    ReasonCode.ALLERGEN_UNKNOWN,
    ReasonCode.CUTOFF_PASSED,
    ReasonCode.DATA_CONFLICT,
    ReasonCode.SUBSTITUTE_REJECTED,
    ReasonCode.QUANTITY_BASIS_UNKNOWN,
    ReasonCode.UNIT_MISMATCH,
})


@dataclass(frozen=True)
class CandidateObservation:
    """Live (or recorded) price and availability observation for one shortlisted product."""

    product_id: str
    unit_price_excl_vat: Decimal | None = None
    comparison_price: Decimal | None = None
    comparison_unit: str | None = None
    vat_rate: Decimal | None = None
    stock_tier: Literal["PLENTY", "EXACT", "SUPPLIER", "UNAVAILABLE", "UNKNOWN"] = "PLENTY"
    available_quantity: Decimal | None = None
    delivery_date: date | None = None
    closing_time: datetime | None = None
    sales_blocked: bool = False
    reasons: tuple[ReasonCode, ...] = ()
    facts: tuple[TrustLabel, ...] = ()


class ValioSubstituteCheck(Contract):
    """Result of re-checking a Valio-listed substitute against the buyer's hard requirements (§7.8, P8 B)."""

    productId: str
    substituteProductId: str
    substituteName: str
    itemId: str | None = None
    passed: bool
    reasons: list[ReasonCode] = Field(default_factory=list)
    failedRequirements: list[str] = Field(default_factory=list)
    summary: str
    trustLabel: TrustLabel
    excludedEntry: ExcludedProduct | None = None


class RankedCandidate(Contract):
    """One compliant candidate ranked per brief §7.6 with a one-line trade-off."""

    productId: str
    name: str
    itemId: str
    rank: int = Field(ge=1)
    tier: CandidateTier
    allRequirementsDeclared: bool
    deliverableOnTimeVerified: bool
    comparisonPriceExclVat: Decimal | None = None
    comparisonUnit: str | None = None
    unitPriceExclVat: Decimal | None = None
    quantitySalesUnits: Decimal | None = None
    deliveredQuantity: Quantity | None = None
    stockTier: str = "UNKNOWN"
    availableQuantity: Decimal | None = None
    deliveryDate: date | None = None
    reliability: Decimal | None = None
    matchedPreferences: int = 0
    unknownFactCount: int = 0
    substitutionSource: SubstitutionKind | None = None
    substituteFor: str | None = None
    exactType: bool = True  # exactly the product type the buyer asked for (T34/T39)
    tradeoff: str
    reasons: list[ReasonCode] = Field(default_factory=list)


class ItemPlan(Contract):
    """Planned shortlist, §7.6 ranking, excluded list and Valio-substitute checks for one request item."""

    item: RequestItem
    resolvedProductType: str | None = None
    shortlistIds: list[str] = Field(default_factory=list)
    rankedCandidates: list[RankedCandidate] = Field(default_factory=list)
    excluded: list[ExcludedProduct] = Field(default_factory=list)
    substituteChecks: list[ValioSubstituteCheck] = Field(default_factory=list)
    partialCoverage: bool = False


class AgentPlan(Contract):
    """Complete purchasing plan for a StructuredRequest before offer commitment and gateway policy decision."""

    customerId: str
    mandateVersion: int = Field(ge=1)
    request: StructuredRequest
    items: list[ItemPlan]
    offerRequest: GetOfferRequest


class ClosestMatch(Contract):
    productId: str
    name: str


class NoOrderItem(Contract):
    itemId: str
    searched: str
    why: str
    closestMatches: list[ClosestMatch] = Field(default_factory=list)


class NoOrderDetails(Contract):
    items: list[NoOrderItem]
    earliestDelivery: date | None = None


def relaxed_name_queries(name: str) -> list[str]:
    """Drop one name token from the original query each time, keeping a meaningful search term."""
    cleaned, _ = extract_inline_requirements(name)
    words = _WORD_RE.findall(cleaned)
    queries = (" ".join(words[:i] + words[i + 1:]) for i in range(len(words)))
    return list(dict.fromkeys(query for query in queries if any(char.isalpha() for char in query)))


def requirement_display_name(req: Requirement) -> str:
    """Human-readable label for a hard requirement (e.g. 'gluten-free', 'milk-free')."""
    code = req.code.strip().upper()
    if req.kind == "CLAIM":
        return _REQUIREMENT_DISPLAY.get(code, code.lower().replace("_", " "))
    if code == "AM":
        return "milk-free"
    return f"{allergen_name(code)}-free"


def check_valio_substitutes(
    product: Product,
    *,
    requirements: Sequence[Requirement],
    mandate: Mandate,
    products_by_id: Mapping[str, Product],
    item_id: str | None = None,
) -> list[ValioSubstituteCheck]:
    """Re-check every Valio-listed substitute on `product` against every hard requirement (§7.8, P8 B).

    A substitute that fails any hard requirement is marked `SUBSTITUTE_REJECTED`, producing both:
    - a `TrustLabel` (`fact="substitute.valio"`) for the line's trust card, and
    - an `ExcludedProduct` entry (`substituteFor=product.productId`, `reasons=[..., SUBSTITUTE_REJECTED]`).
    """
    checks: list[ValioSubstituteCheck] = []
    combined_reqs = hard_requirements(requirements, mandate)
    for sub_id in product.substitutions:
        sub_product = products_by_id.get(sub_id)
        if sub_product is None:
            continue
        reasons, facts = evaluate_product_static_requirements(
            sub_product,
            requirements,
            mandate,
            named=False,
            item_id=item_id,
        )
        if reasons:
            merged_reasons = list(dict.fromkeys([*reasons, ReasonCode.SUBSTITUTE_REJECTED]))
            failed_labels: list[str] = []
            for req in combined_reqs:
                single_findings = check_requirements(
                    sub_product, [req], mandate, named=False, item_id=item_id
                )
                if single_findings:
                    lbl = requirement_display_name(req)
                    if lbl not in failed_labels:
                        failed_labels.append(lbl)
            if not failed_labels:
                if ReasonCode.ALLERGEN_UNKNOWN in reasons:
                    failed_labels.append("allergen declaration")
                else:
                    failed_labels.append(
                        ", ".join(requirement_display_name(r) for r in combined_reqs) or "hard requirements"
                    )
            req_str = ", ".join(failed_labels)
            summary = (
                f"Valio Aimo lists {sub_product.productId} {sub_product.name} as the substitute for "
                f"{product.productId}. It fails '{req_str}' for this buyer, so the agent will never swap to it."
            )
            trust_label = TrustLabel(
                fact="substitute.valio",
                value=sub_product.productId,
                display=summary,
                level="VERIFIED",
                source=SOURCE_SUBSTITUTION,
                method=None,
                conflict=None,
            )
            excluded_entry = ExcludedProduct(
                productId=sub_product.productId,
                name=sub_product.name,
                itemId=item_id,
                reasons=merged_reasons,
                facts=facts or list(sub_product.facts),
                substituteFor=product.productId,
            )
            checks.append(
                ValioSubstituteCheck(
                    productId=product.productId,
                    substituteProductId=sub_product.productId,
                    substituteName=sub_product.name,
                    itemId=item_id,
                    passed=False,
                    reasons=merged_reasons,
                    failedRequirements=failed_labels,
                    summary=summary,
                    trustLabel=trust_label,
                    excludedEntry=excluded_entry,
                )
            )
        else:
            req_str = ", ".join(requirement_display_name(r) for r in combined_reqs) or "all hard requirements"
            summary = (
                f"Valio Aimo lists {sub_product.productId} {sub_product.name} as the substitute for "
                f"{product.productId}; it satisfies {req_str}."
            )
            trust_label = TrustLabel(
                fact="substitute.valio",
                value=sub_product.productId,
                display=summary,
                level="VERIFIED",
                source=SOURCE_SUBSTITUTION,
                method=None,
                conflict=None,
            )
            checks.append(
                ValioSubstituteCheck(
                    productId=product.productId,
                    substituteProductId=sub_product.productId,
                    substituteName=sub_product.name,
                    itemId=item_id,
                    passed=True,
                    reasons=[ReasonCode.VALIO_SUBSTITUTE],
                    failedRequirements=[],
                    summary=summary,
                    trustLabel=trust_label,
                    excludedEntry=None,
                )
            )
    return checks


def is_compliant_substitute(
    product: Product,
    *,
    requirements: Sequence[Requirement],
    mandate: Mandate,
    item_id: str | None = None,
) -> tuple[bool, list[ReasonCode], list[TrustLabel]]:
    """Verify whether a candidate proves every hard requirement so it may be proposed as a substitute (P2.2/P2.3)."""
    reasons, facts = evaluate_product_static_requirements(
        product,
        requirements,
        mandate,
        named=False,
        item_id=item_id,
    )
    return len(reasons) == 0, reasons, facts


def _product_reliability(product: Product) -> Decimal | None:
    for label in product.facts:
        if label.fact == "reliability" and label.value is not None:
            try:
                return Decimal(str(label.value))
            except ArithmeticError:
                return None
    return None


def _unknown_fact_count(product: Product, live_facts: Sequence[TrustLabel] = ()) -> int:
    seen_facts: dict[str, TrustLabel] = {}
    for f in product.facts:
        seen_facts[f.fact] = f
    for f in live_facts:
        seen_facts[f.fact] = f
    count = sum(1 for f in seen_facts.values() if f.level == "UNKNOWN")
    if product.contentPerSalesUnit is None:
        count += 1
    return count


def _all_requirements_declared(
    product: Product,
    requirements: Sequence[Requirement],
    mandate: Mandate,
) -> bool:
    """§7.6 #1: all hard requirements and pack content met with >= DECLARED (not ESTIMATE or UNKNOWN)."""
    reasons, _ = evaluate_product_static_requirements(product, requirements, mandate, named=False)
    if reasons:
        return False
    cpsu = product.contentPerSalesUnit
    if cpsu is not None and cpsu.level not in ("VERIFIED", "DECLARED"):
        return False
    return True


def _matched_preferences_count(product: Product, preferences: Sequence[Preference]) -> int:
    if not preferences:
        return 0
    text_lower = " ".join(p for p in (product.name, *product.names.values()) if p).lower()
    matched = 0
    for pref in preferences:
        val = pref.value.strip().lower()
        if pref.kind == "ORGANIC":
            if is_organic(product):
                matched += 1
        elif pref.kind == "ORIGIN":
            if (product.countryOfOrigin or "").lower() == val or (
                val == "fi"
                and any(
                    lbl in product.labels
                    for lbl in ("GOODS_FROM_FINLAND_BLUE_SWAN", "MADE_IN_FINLAND_FLAG_WITH_KEY", "PUHTAASTI_KOTIMAINEN")
                )
            ):
                matched += 1
        elif pref.kind == "FAT_PERCENT":
            target_fat = parse_fat_percent(pref.value)
            if target_fat is not None and target_fat in product_fat_percents(product):
                matched += 1
        elif pref.kind == "PACK_SIZE":
            compact = val.replace(" ", "")
            if val in text_lower or compact in text_lower.replace(" ", ""):
                matched += 1
        elif pref.kind == "LABEL":
            if pref.value.upper() in product.labels or val in text_lower:
                matched += 1
        elif pref.kind in ("BRAND", "OTHER"):
            if val and val in text_lower:
                matched += 1
    return matched


def _comparison_price(
    product: Product,
    obs: CandidateObservation | None,
    target_unit: str,
) -> tuple[Decimal | None, str | None]:
    """Compute the comparison price (€/l, €/kg, or per sales unit) in Decimal (§7.6 #3)."""
    if obs is None:
        return None, None
    if obs.comparison_price is not None and obs.comparison_price > 0:
        comp_unit = (obs.comparison_unit or target_unit).lower()
        return obs.comparison_price, comp_unit
    if obs.unit_price_excl_vat is None:
        return None, None
    cpsu = product.contentPerSalesUnit
    if cpsu is not None and cpsu.value > 0:
        per_unit = (obs.unit_price_excl_vat / cpsu.value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        return per_unit, cpsu.unit
    return obs.unit_price_excl_vat, product.salesUnit.lower()


def observation_from_offer_line(line: OfferLine) -> CandidateObservation:
    """Extract a `CandidateObservation` from an `OfferLine` returned by `getOffer`."""
    comp_price: Decimal | None = None
    comp_unit: str | None = None
    stock_tier: Literal["PLENTY", "EXACT", "SUPPLIER", "UNAVAILABLE", "UNKNOWN"] = "PLENTY"
    avail_qty: Decimal | None = None
    sales_blocked = ReasonCode.SALES_BLOCKED in line.reasons

    for f in line.facts:
        if f.fact == "price.comparison" and f.value is not None:
            try:
                comp_price = Decimal(str(f.value))
                if "/" in (f.display or ""):
                    comp_unit = f.display.rsplit("/", 1)[-1].strip().split()[0].lower()
            except ArithmeticError:
                comp_price = None
        elif f.fact == "stock.quantity":
            disp = (f.display or "").lower()
            if "supplier" in disp or ReasonCode.SUPPLIER_STOCK_UNKNOWN in line.reasons:
                stock_tier = "SUPPLIER"
                avail_qty = None
            elif "plenty" in disp or f.method == SENTINEL_METHOD:
                stock_tier = "PLENTY"
                if f.value is not None:
                    try:
                        avail_qty = Decimal(str(f.value))
                    except ArithmeticError:
                        avail_qty = None
            elif f.value is not None:
                try:
                    qty = Decimal(str(f.value))
                    avail_qty = qty
                    if qty <= 0:
                        stock_tier = "UNAVAILABLE"
                    elif is_stock_sentinel(qty):
                        stock_tier = "PLENTY"
                    else:
                        stock_tier = "EXACT"
                except ArithmeticError:
                    stock_tier = "UNKNOWN"
        elif f.fact == "order.blocked" and f.value is True:
            sales_blocked = True

    if comp_price is None and line.contentPerSalesUnit.value > 0:
        comp_price = (line.unitPriceExclVat / line.contentPerSalesUnit.value).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        comp_unit = line.contentPerSalesUnit.unit

    return CandidateObservation(
        product_id=line.productId,
        unit_price_excl_vat=line.unitPriceExclVat,
        comparison_price=comp_price,
        comparison_unit=comp_unit,
        vat_rate=line.vatRate,
        stock_tier=stock_tier,
        available_quantity=avail_qty,
        delivery_date=line.deliveryDate,
        closing_time=line.closingTime,
        sales_blocked=sales_blocked,
        reasons=tuple(line.reasons),
        facts=tuple(line.facts),
    )


_SALES_UNIT_DISPLAY: dict[str, str] = {
    "ST": "pc",
    "PCS": "pc",
    "PC": "pc",
    "H87": "pc",
    "PR": "pc",
    "PTN": "pc",
    "EA": "pc",
    "BAG": "bag",
    "BG": "bag",
    "PS": "bag",
    "SK": "bag",
    "PAK": "pack",
    "PK": "pack",
    "KI": "case",
    "LTK": "case",
    "BOX": "case",
    "CS": "case",
    "CT": "case",
    "KG": "kg",
    "KGM": "kg",
    "L": "l",
    "LTR": "l",
}

_KNOWN_BRANDS: tuple[tuple[str, str], ...] = (
    ("fazer aito", "Fazer Aito"),
    ("explore cuisine", "Explore Cuisine"),
    ("myllyn paras", "Myllyn Paras"),
    ("arla pro", "Arla Pro"),
    ("valio", "Valio"),
    ("oddlygood", "Oddlygood"),
    ("elovena", "Elovena"),
    ("sammills", "SamMills"),
    ("semper", "Semper"),
    ("rummo", "Rummo"),
    ("arla", "Arla"),
    ("oatly", "Oatly"),
    ("urtekram", "Urtekram"),
    ("barilla", "Barilla"),
    ("fazer", "Fazer"),
    ("meira", "Meira"),
    ("raisio", "Raisio"),
    ("paulig", "Paulig"),
    ("atria", "Atria"),
    ("snellman", "Snellman"),
    ("saarioinen", "Saarioinen"),
    ("apetit", "Apetit"),
    ("vaasan", "Vaasan"),
    ("pirkka", "Pirkka"),
    ("menu", "Menu"),
    ("eila", "Valio Eila"),
    ("arki", "Valio Arki"),
)

_GENERIC_NAME_LEADS: frozenset[str] = frozenset({
    "synthetic",
    "milk",
    "skimmed",
    "semi",
    "semi-skimmed",
    "whole",
    "oat",
    "gluten-free",
    "glutenfree",
    "gluten",
    "lactose-free",
    "lactose",
    "organic",
    "luomu",
    "cooking",
    "whipping",
    "fresh",
    "frozen",
    "smoked",
    "lightly",
    "buffet",
    "light",
    "cream",
    "pasta",
    "macaroni",
    "penne",
    "fusilli",
    "spaghetti",
    "lasagne",
    "fettuccine",
    "fettucine",
    "salmon",
    "rainbow",
    "trout",
    "flounder",
    "flounderfillet",
    "herring",
    "cod",
    "fish",
    "butter",
    "cheese",
    "bread",
    "msc",
    "asc",
})

_PASTA_SHAPES: tuple[tuple[str, str], ...] = (
    ("macaroni", "macaroni"),
    ("maccheroni", "macaroni"),
    ("makaroni", "macaroni"),
    ("penne", "penne"),
    ("fusilli", "fusilli"),
    ("kierrepasta", "fusilli"),
    ("fettuccine", "fettuccine"),
    ("fettucine", "fettuccine"),
    ("spaghetti", "spaghetti"),
    ("spagetti", "spaghetti"),
    ("lasagne", "lasagne"),
    ("lasagna", "lasagne"),
    ("rigatoni", "rigatoni"),
    ("tagliatelle", "tagliatelle"),
)

_SOUR_MILK_TERMS: tuple[str, ...] = (
    "sour milk",
    "piimä",
    "surmjölk",
    "filmjölk",
    "buttermilk",
)


def _sales_unit_label(sales_unit: str) -> str:
    clean = (sales_unit or "").strip().upper()
    return _SALES_UNIT_DISPLAY.get(clean, clean.lower() or "unit")


def _extract_brand(product: Product) -> str | None:
    """Extract a readable brand name from the product's multilingual names (§7.6 trade-off)."""
    combined = " ".join(p for p in (product.name, *product.names.values()) if p)
    lower = combined.lower()
    for needle, label in _KNOWN_BRANDS:
        if re.search(rf"\b{re.escape(needle)}\b", lower):
            return label

    cleaned = re.sub(r"^\s*\d+\s*[x×]\s*", "", product.name or "", flags=re.IGNORECASE).strip()
    if cleaned.lower().startswith("synthetic "):
        cleaned = cleaned[len("synthetic ") :].strip()
        cleaned = re.sub(r"^\s*\d+\s*[x×]\s*", "", cleaned, flags=re.IGNORECASE).strip()
    tokens = re.findall(r"[A-Za-zÅÄÖåäö][\w'’-]*", cleaned)
    if tokens:
        first = tokens[0]
        if first[0].isupper() and first.lower() not in _GENERIC_NAME_LEADS:
            return first
    return None


def _extract_product_traits(product: Product) -> list[str]:
    """Extract fat %, dairy variant, pasta shape, or heat-treatment style for the §7.6 one-line trade-off."""
    text_lower = " ".join(p for p in (product.name, *product.names.values()) if p).lower()
    traits: list[str] = []

    if "semi skimmed" in text_lower or "semi-skimmed" in text_lower or "kevytmaito" in text_lower:
        traits.append("semi-skimmed")
    elif "skimmed" in text_lower or "rasvaton" in text_lower:
        traits.append("skimmed (0 % fat)")
    elif "whole milk" in text_lower or "täysmaito" in text_lower:
        traits.append("whole milk")

    fat_match = _FAT_IN_NAME_RE.search(product.name or "")
    if fat_match:
        pct = fat_match.group(1).replace(",", ".")
        fat_label = f"{pct} % fat"
        if fat_label not in traits:
            traits.append(fat_label)

    for needle, shape in _PASTA_SHAPES:
        if re.search(rf"\b{re.escape(needle)}\b", text_lower):
            if shape not in traits:
                traits.append(shape)
            break

    if "barista" in text_lower:
        traits.append("Barista")
    if re.search(r"\buht\b", text_lower):
        traits.append("UHT")
    elif re.search(r"\besl\b", text_lower):
        traits.append("ESL")
    if (
        "organic" in text_lower
        or "luomu" in text_lower
        or "EU_ORGANIC_FARMING" in product.labels
    ):
        traits.append("organic")

    return traits


def _format_tradeoff(
    product: Product,
    *,
    is_recommended: bool,
    comp_price: Decimal | None,
    comp_unit: str | None,
    best_comp_price: Decimal | None,
    stock_tier: str,
    available_qty: Decimal | None,
    reliability: Decimal | None,
    substitution_source: SubstitutionKind | None,
    substitute_for: str | None,
    type_differences: tuple[str, ...] = (),
    pack_explanation: str | None = None,
    shortfall_text: str | None = None,
) -> str:
    parts: list[str] = []
    if substitution_source == "VALIO" and substitute_for:
        parts.append(f"Valio-listed substitute for {substitute_for}")
    elif substitution_source == "AGENT" and substitute_for:
        parts.append(f"Agent substitute for {substitute_for}")
    elif substitution_source == "AGENT":
        parts.append("Agent substitute (closest compliant product type)")
    if type_differences:
        parts.append("different product type: " + "; ".join(type_differences))

    if comp_price is not None and comp_unit:
        price_str = f"{format_money(comp_price)}/{comp_unit}"
        if not is_recommended and best_comp_price is not None and comp_price != best_comp_price:
            diff = comp_price - best_comp_price
            sign = "+" if diff > 0 else "-"
            parts.append(f"{price_str} ({sign}{format_money(abs(diff))}/{comp_unit} vs recommended)")
        else:
            parts.append(price_str)

    cpsu = product.contentPerSalesUnit
    if cpsu is not None:
        val_str = format(cpsu.value.normalize(), "f")
        parts.append(f"{val_str} {cpsu.unit} / {_sales_unit_label(product.salesUnit)}")

    brand = _extract_brand(product)
    if brand:
        parts.append(brand)

    traits = _extract_product_traits(product)
    if traits:
        parts.append(", ".join(traits))

    if stock_tier == "PLENTY":
        # Valio's sentinel (>= 999999): any quantity may be ordered, but no count is proven (T60).
        parts.append("no stock limit (Valio allows any quantity)")
    elif stock_tier == "EXACT" and available_qty is not None:
        if pack_explanation is not None:
            parts.append(f"{pack_explanation} (verified)")
        else:
            parts.append(f"{format(available_qty.normalize(), 'f')} in stock (verified)")
        if shortfall_text is not None:
            parts.append(shortfall_text)
    elif stock_tier == "SUPPLIER":
        parts.append("supplier warehouse stock (quantity unknown)")
    elif stock_tier == "UNKNOWN":
        parts.append("static catalogue match (stock unprobed)")

    if reliability is not None:
        pct = (reliability * Decimal(100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        pct_str = str(int(pct)) if pct == pct.to_integral() else format(pct, "f")
        parts.append(f"{pct_str} % reliability")

    return " · ".join(parts) if parts else product.name


def _concept_for_query(query: str) -> tuple[ProductConcept | None, ProductConcept | None]:
    cleaned_query, _ = extract_inline_requirements(query)
    concepts = resolve_query_concepts(cleaned_query)
    primary = concepts[0] if concepts else None
    parent = CONCEPTS_BY_ID.get(primary.parent_concept) if primary and primary.parent_concept else None
    return primary, parent


def _concept_for_item(item: RequestItem) -> tuple[ProductConcept | None, ProductConcept | None]:
    return _concept_for_query(item.what)


def _item_alternatives(item: RequestItem) -> tuple[str, ...]:
    cleaned, _ = extract_inline_requirements(item.what)
    alts = split_query_alternatives(cleaned)
    return alts if len(alts) > 1 else (item.what,)


def _item_same_product_family(
    product_names: tuple[str, ...],
    item: RequestItem,
    *,
    category: str = "",
) -> bool:
    req_codes = tuple(r.code for r in item.requirements)
    for alt_what in _item_alternatives(item):
        primary_c, parent_c = _concept_for_query(alt_what)
        if same_product_family(
            alt_what,
            product_names,
            concept_id=primary_c.concept_id if primary_c else None,
            parent_concept_id=parent_c.concept_id if parent_c else None,
            category=category,
            requirement_codes=req_codes,
        ):
            return True
    return False


def _requested_brand(item: RequestItem, query: str | None = None) -> tuple[str, str] | None:
    """Return `(needle_lower, display_label)` when the buyer explicitly asked for a known brand."""
    if item.productId is not None:
        return None
    for pref in item.preferences:
        if pref.kind == "BRAND" and pref.value.strip():
            low = pref.value.strip().lower()
            for needle, label in _KNOWN_BRANDS:
                if needle == low or re.search(rf"\b{re.escape(needle)}\b", low):
                    return needle, label
            return low, pref.value.strip()
    q_lower = (query if query is not None else item.what).lower()
    for needle, label in _KNOWN_BRANDS:
        if re.search(rf"\b{re.escape(needle)}\b", q_lower):
            return needle, label
    return None


def _single_requested_type_match(product: Product, item: RequestItem, query_what: str) -> TypeMatch:
    primary, _ = _concept_for_query(query_what)
    codes = tuple(r.code for r in item.requirements)
    names = (product.name, *product.names.values())
    base = product_type_match(
        query_what, names, concept_id=primary.concept_id if primary else None, requirement_codes=codes
    )
    differences = list(base.differences)
    text_lower = " ".join(n for n in names if n).lower()
    q_lower = query_what.lower()

    if primary is not None and primary.concept_id == "milk":
        asked_sour = any(t in q_lower for t in ("sour", "piimä", "surmjölk", "filmjölk", "buttermilk", "gefilus"))
        if not asked_sour and any(t in text_lower for t in _SOUR_MILK_TERMS):
            diff = "sour milk, not milk"
            if diff not in differences:
                differences.append(diff)
    elif primary is not None and primary.parent_concept == "pasta":
        words = frozenset(_WORD_RE.findall(text_lower))
        if not match_concept_in_text(primary, text_lower, words):
            for needle, shape in _PASTA_SHAPES:
                if shape != primary.concept_id and re.search(rf"\b{re.escape(needle)}\b", text_lower):
                    diff = f"{shape}, not {primary.concept_id}"
                    if diff not in differences:
                        differences.append(diff)
                    break
    elif primary is None:
        # No known product concept (e.g. fish): every product noun the buyer used must be in the name,
        # so "salmon fillet" never matches a flounder fillet (T39).
        missing = _missing_query_nouns(query_what, text_lower)
        if missing:
            differences.append(f"not {' '.join(missing)}")

    # T87: product form (grated, sliced, block, shredded, cubed, whole, powder) is a hard requirement when stated.
    req_form = extract_requested_form(query_what, item.preferences)
    if req_form is not None:
        form_diff = product_form_difference(names, product.category, req_form)
        if form_diff is not None and form_diff not in differences:
            differences.append(form_diff)

    # T52: stated attributes are hard. A product that does not prove one is only an agent substitute, with
    # the difference named; it is never the recommended exact match.
    if any(p.kind == "ORGANIC" for p in item.preferences) and not is_organic(product):
        differences.append("not organic (no organic label)")
    target_fat = requested_fat_percent(item.preferences, query_what)
    if target_fat is not None:
        fats = product_fat_percents(product)
        if not fats:
            differences.append(f"fat % not stated, {_pct(target_fat)} % asked")
        elif target_fat not in fats:
            shown = " / ".join(_pct(f) for f in sorted(fats))
            differences.append(f"{shown} % fat, not {_pct(target_fat)} %")

    req_brand = _requested_brand(item, query_what)
    if req_brand is not None:
        brand_needle, brand_label = req_brand
        prod_brand = _extract_brand(product)
        matches_brand = bool(re.search(rf"\b{re.escape(brand_needle)}\b", text_lower)) or (
            prod_brand is not None and brand_needle in prod_brand.lower()
        )
        if not matches_brand:
            diff = f"{prod_brand}, not {brand_label}" if prod_brand else f"not {brand_label}"
            if diff not in differences:
                differences.append(diff)

    return TypeMatch(exact=not differences, differences=tuple(differences))


def requested_type_match(product: Product, item: RequestItem) -> TypeMatch:
    """Is `product` exactly the product type the buyer asked for (e.g. whipping, not cooking cream)? (T34, T22, T87)"""
    alts = _item_alternatives(item)
    if len(alts) <= 1:
        return _single_requested_type_match(product, item, item.what)
    names = (product.name, *product.names.values())
    req_codes = tuple(r.code for r in item.requirements)
    family_matches = [
        _single_requested_type_match(product, item, alt)
        for alt in alts
        if same_product_family(
            alt,
            names,
            concept_id=_concept_for_query(alt)[0].concept_id if _concept_for_query(alt)[0] else None,
            parent_concept_id=_concept_for_query(alt)[1].concept_id if _concept_for_query(alt)[1] else None,
            category=product.category,
            requirement_codes=req_codes,
        )
    ]
    candidates = family_matches or [_single_requested_type_match(product, item, alt) for alt in alts]
    return min(candidates, key=lambda tm: (0 if tm.exact else 1, len(tm.differences)))


def _pct(value: Decimal) -> str:
    return format(value.normalize(), "f")


def is_organic(product: Product) -> bool:
    """Organic is proven by an organic label, or the producer's own word in the product name."""
    text_lower = " ".join(p for p in (product.name, *product.names.values()) if p).lower()
    return (
        any(label in product.labels for label in ORGANIC_LABELS)
        or any(word in text_lower for word in ("organic", "luomu", "ekologisk"))
    )


def _missing_query_nouns(what: str, text_lower: str) -> list[str]:
    """The buyer's product words (qualifiers such as 'fresh' removed) that the product name does not contain."""
    return missing_family_nouns(what, text_lower)


def _matches_primary_concept(product: Product, primary: ProductConcept | None, query_lower: str) -> tuple[bool, bool]:
    """Return `(matches_primary_concept, is_unrequested_specialty)`."""
    if primary is None:
        return True, False
    text_lower = " ".join(p for p in (product.name, *product.names.values()) if p).lower()
    words = frozenset(_WORD_RE.findall(text_lower))
    matches = match_concept_in_text(primary, text_lower, words, product.category)
    is_specialty = False
    if primary.prefer_plain_over:
        for term in primary.prefer_plain_over:
            if term not in query_lower and term in text_lower:
                is_specialty = True
                break
    return matches, is_specialty


def _item_matches_primary_concept(product: Product, item: RequestItem) -> tuple[bool, bool]:
    alts = _item_alternatives(item)
    if len(alts) <= 1:
        primary, _ = _concept_for_item(item)
        return _matches_primary_concept(product, primary, item.what.lower())
    results = [
        _matches_primary_concept(product, _concept_for_query(alt)[0], alt.lower())
        for alt in alts
    ]
    matched_results = [r for r in results if r[0]]
    if matched_results:
        return True, all(r[1] for r in matched_results)
    return False, any(r[1] for r in results)


def rank_candidates(
    candidates: Sequence[Product],
    *,
    item: RequestItem,
    mandate: Mandate,
    requested_date: date,
    observations: Mapping[str, CandidateObservation] | None = None,
    named_product: Product | None = None,
) -> list[RankedCandidate]:
    """Rank compliant candidates for `item` in exact §7.6 order:

    1. all requirements met with >= DECLARED;
    2. deliverable by the date with a VERIFIED quantity;
    3. lowest comparison price (€/l, €/kg);
    4. higher reliability;
    5. preferences and labels;
    6. fewer UNKNOWN facts.

    Non-compliant or non-deliverable candidates are excluded and never returned here.
    """
    obs_map = observations or {}
    target_id = item.productId or (named_product.productId if named_product else None)
    valio_subs_of_named: frozenset[str] = (
        frozenset(named_product.substitutions) if named_product is not None else frozenset()
    )
    target_fat = requested_fat_percent(item.preferences, item.what)

    scored: list[
        tuple[tuple, Product, CandidateObservation | None, SubstitutionKind | None, str | None, tuple[str, ...], bool]
    ] = []

    for prod in candidates:
        named = target_id is not None and prod.productId == target_id
        # T51 product family guard: a candidate may be offered (even as AGENT_SUBSTITUTE) only if it is
        # in the same product family as the item (or any of its 'X or Y' / 'X tai Y' alternatives, T87).
        if not named and not _item_same_product_family(
            (prod.name, *prod.names.values()),
            item,
            category=prod.category,
        ):
            continue
        static_reasons, _ = evaluate_product_static_requirements(
            prod, item.requirements, mandate, named=named, item_id=item.itemId
        )
        if static_reasons:
            continue

        # A proven different fat % is out (T24); an unstated one ranks after proven matches as an agent
        # substitute that names it ("fat % not stated, 1.5 % asked"), never silently as the exact type (T52).
        prod_fats = product_fat_percents(prod)
        if target_fat is not None and not named and prod_fats and target_fat not in prod_fats:
            continue
        fat_unknown_rank = 1 if (target_fat is not None and not prod_fats) else 0

        obs = obs_map.get(prod.productId)
        if obs is not None:
            if obs.sales_blocked or obs.stock_tier in ("UNAVAILABLE", "UNKNOWN"):
                continue
            if any(code in _NON_ORDERABLE_CODES for code in obs.reasons):
                continue
            if obs.unit_price_excl_vat is None:
                continue
            prod = apply_price_ratio_content(
                prod,
                obs.unit_price_excl_vat,
                obs.comparison_price,
                obs.comparison_unit,
            )

        sizing = size(prod, item.quantity, item.unit)
        if sizing.unit_mismatch or sizing.content is None:
            continue
        if (
            obs is not None
            and obs.stock_tier == "EXACT"
            and obs.available_quantity is not None
            and obs.available_quantity < sizing.quantity_sales_units
        ):
            if _floor_to_lot(obs.available_quantity, prod.allowedLotSize) <= 0:
                continue

        matches_primary, is_specialty = _item_matches_primary_concept(prod, item)
        # A product of a different type (whipping vs cooking cream, dairy vs alternative) is never an
        # exact match: it can only be offered as an agent substitute, after every exact match (T34).
        type_match = requested_type_match(prod, item)
        exact_type = matches_primary and type_match.exact
        sub_source: SubstitutionKind | None = None
        sub_for: str | None = None

        if target_id is not None:
            if prod.productId == target_id:
                sub_rank = 0
            elif prod.productId in valio_subs_of_named:
                sub_rank = 1
                sub_source = "VALIO"
                sub_for = target_id
            else:
                sub_rank = 2 if exact_type else 3
                sub_source = "AGENT"
                sub_for = target_id
        else:
            if exact_type and not is_specialty:
                sub_rank = 0
            elif exact_type and is_specialty:
                sub_rank = 1
            else:
                sub_rank = 2
                sub_source = "AGENT"

        type_diff_count = 0 if exact_type else len(type_match.differences)

        # When live/recorded observations exist for this item, any candidate with a verified observation
        # ranks ahead of static-only candidates that were not in a partial recorded fixture batch.
        has_observation = 0 if (obs is not None or not obs_map) else 1

        # 1. All requirements met with >= DECLARED (§7.6 #1)
        reqs_declared = _all_requirements_declared(prod, item.requirements, mandate)
        crit1_declared = 0 if reqs_declared else 1

        # 2. Deliverable by the date with a VERIFIED quantity (§7.6 #2)
        if obs is not None:
            on_time = obs.delivery_date is not None and obs.delivery_date <= requested_date
            verified_qty = obs.stock_tier in ("PLENTY", "EXACT") and (
                obs.stock_tier == "PLENTY" or (obs.available_quantity or Decimal(0)) > 0
            )
            if on_time and verified_qty:
                crit2_deliverable = 0
            elif on_time and obs.stock_tier == "SUPPLIER":
                crit2_deliverable = 1
            elif verified_qty:
                crit2_deliverable = 2
            else:
                crit2_deliverable = 3
        else:
            crit2_deliverable = 0 if not obs_map else 4

        # 3. Lowest comparison price (€/l, €/kg) (§7.6 #3)
        comp_price, _ = _comparison_price(prod, obs, item.unit)
        crit3_price = comp_price if comp_price is not None else Decimal("999999999")

        # 4. Higher reliability (§7.6 #4)
        rel = _product_reliability(prod)
        crit4_reliability = -(rel if rel is not None else Decimal("-1"))

        # 5. Preferences and labels (§7.6 #5)
        pref_matches = _matched_preferences_count(prod, item.preferences)
        label_count = len(prod.labels)
        crit5_prefs = (-pref_matches, -label_count)

        # 6. Fewer UNKNOWN facts (§7.6 #6)
        unk_count = _unknown_fact_count(prod, obs.facts if obs else ())
        crit6_unknowns = unk_count

        # Tie-breakers for agent substitutes: preserve same salesUnit as named product, then brief IDs, then ID
        same_sales_unit = (
            0 if (named_product is None or prod.salesUnit == named_product.salesUnit) else 1
        )
        in_brief = 0 if prod.productId in BRIEF_PRODUCT_IDS else 1

        sort_key = (
            has_observation,
            sub_rank,
            type_diff_count,
            fat_unknown_rank,
            crit1_declared,
            crit2_deliverable,
            crit3_price,
            crit4_reliability,
            crit5_prefs,
            crit6_unknowns,
            same_sales_unit,
            in_brief,
            prod.productId,
        )
        is_exact_type = exact_type or (target_id is not None and prod.productId == target_id)
        scored.append((sort_key, prod, obs, sub_source, sub_for, type_match.differences, is_exact_type))

    scored.sort(key=lambda entry: entry[0])

    tier_names: tuple[CandidateTier, ...] = ("RECOMMENDED", "BEST", "BETTER", "GOOD")
    ranked: list[RankedCandidate] = []
    best_comp_price: Decimal | None = None

    for idx, (_key, prod, obs, sub_source, sub_for, type_differences, is_exact_type) in enumerate(scored):
        sizing = size(prod, item.quantity, item.unit)
        pack_explanation: str | None = None
        shortfall_text: str | None = None
        if (
            obs is not None
            and obs.stock_tier == "EXACT"
            and obs.available_quantity is not None
            and sizing.content is not None
            and obs.available_quantity < sizing.quantity_sales_units
        ):
            capped_units = _floor_to_lot(obs.available_quantity, prod.allowedLotSize)
            if capped_units > 0:
                sizing = Sizing(
                    capped_units,
                    Quantity(value=capped_units * sizing.content.value, unit=item.unit),
                    sizing.content,
                    sizing.basis,
                    sizing.unit_mismatch,
                )
                pack_explanation = _partial_stock_pack_explanation(
                    prod, sizing, capped_units, obs.available_quantity
                )
                if sizing.delivered.value < item.quantity:
                    shortfall = item.quantity - sizing.delivered.value
                    day_str = format_date(obs.delivery_date or requested_date)
                    shortfall_text = (
                        f"{_qty_text(sizing.delivered.value, item.unit)} now; "
                        f"{_qty_text(shortfall, item.unit)} cannot be met by {day_str}"
                    )
        comp_price, comp_unit = _comparison_price(prod, obs, item.unit)
        if idx == 0:
            best_comp_price = comp_price
        tier: CandidateTier = tier_names[idx] if idx < len(tier_names) else "ALTERNATIVE"
        rel = _product_reliability(prod)
        pref_matches = _matched_preferences_count(prod, item.preferences)
        unk_count = _unknown_fact_count(prod, obs.facts if obs else ())
        reqs_declared = _all_requirements_declared(prod, item.requirements, mandate)
        if obs is not None:
            on_time = obs.delivery_date is not None and obs.delivery_date <= requested_date
            verified_qty = obs.stock_tier in ("PLENTY", "EXACT") and (
                obs.stock_tier == "PLENTY" or (obs.available_quantity or Decimal(0)) > 0
            )
            deliverable_verified = on_time and verified_qty
            stock_tier = obs.stock_tier
            avail_qty = obs.available_quantity
            deliv_date = obs.delivery_date
            unit_price = obs.unit_price_excl_vat
            line_reasons = list(obs.reasons)
        else:
            deliverable_verified = not obs_map
            stock_tier = "UNKNOWN"
            avail_qty = None
            deliv_date = None
            unit_price = None
            line_reasons = []

        if sub_source == "AGENT" and ReasonCode.AGENT_SUBSTITUTE not in line_reasons:
            line_reasons.append(ReasonCode.AGENT_SUBSTITUTE)
        elif sub_source == "VALIO" and ReasonCode.VALIO_SUBSTITUTE not in line_reasons:
            line_reasons.append(ReasonCode.VALIO_SUBSTITUTE)

        tradeoff = _format_tradeoff(
            prod,
            is_recommended=idx == 0,
            comp_price=comp_price,
            comp_unit=comp_unit,
            best_comp_price=best_comp_price,
            stock_tier=stock_tier,
            available_qty=avail_qty,
            reliability=rel,
            substitution_source=sub_source,
            substitute_for=sub_for,
            type_differences=type_differences if sub_source == "AGENT" else (),
            pack_explanation=pack_explanation,
            shortfall_text=shortfall_text,
        )

        ranked.append(
            RankedCandidate(
                productId=prod.productId,
                name=prod.name,
                itemId=item.itemId,
                rank=idx + 1,
                tier=tier,
                allRequirementsDeclared=reqs_declared,
                deliverableOnTimeVerified=deliverable_verified,
                comparisonPriceExclVat=comp_price,
                comparisonUnit=comp_unit,
                unitPriceExclVat=unit_price,
                quantitySalesUnits=sizing.quantity_sales_units,
                deliveredQuantity=sizing.delivered,
                stockTier=stock_tier,
                availableQuantity=avail_qty,
                deliveryDate=deliv_date,
                reliability=rel,
                matchedPreferences=pref_matches,
                unknownFactCount=unk_count,
                substitutionSource=sub_source,
                substituteFor=sub_for,
                exactType=is_exact_type,
                tradeoff=tradeoff,
                reasons=line_reasons,
            )
        )
    return ranked


def _parse_pack_size_preference(preferences: Sequence[Preference]) -> tuple[Decimal, Literal["l", "kg"]] | None:
    for pref in preferences:
        if pref.kind != "PACK_SIZE":
            continue
        m = _PACK_VALUE_RE.match(pref.value.strip())
        if not m:
            continue
        val = Decimal(m.group("num").replace(",", "."))
        u = m.group("unit").lower()
        if u == "g":
            return val / Decimal(1000), "kg"
        if u == "kg":
            return val, "kg"
        if u == "ml":
            return val / Decimal(1000), "l"
        if u == "dl":
            return val / Decimal(10), "l"
        if u == "l":
            return val, "l"
    return None


def _concept_terms(concept: ProductConcept | None) -> set[str]:
    if concept is None:
        return set()
    words: set[str] = set()
    for phrase in (*concept.en_terms, *concept.fi_stems, *concept.sv_stems):
        for w in _WORD_RE.findall(phrase.lower()):
            words.add(w)
    return words


def _find_named_product_in_results(
    item: RequestItem,
    search_result: SearchProductsResult,
    products_by_id: Mapping[str, Product],
) -> Product | None:
    if item.productId and item.productId in products_by_id:
        return products_by_id[item.productId]
    # Check if the buyer wrote a specific brand/product name that matched an excluded product (e.g. "URTEKRAM fusilli" -> 470699)
    tokens = [t.lower() for t in _WORD_RE.findall(item.what) if len(t) >= 4]
    if not tokens:
        return None
    primary_concept, parent_concept = _concept_for_item(item)
    concept_terms = _concept_terms(primary_concept) | _concept_terms(parent_concept)
    brand_tokens = [t for t in tokens if not any(t.startswith(ct) or ct.startswith(t) for ct in concept_terms)]
    if not brand_tokens:
        return None
    for ex in search_result.excluded:
        prod = products_by_id.get(ex.productId)
        if prod is None:
            continue
        name_lower = " ".join(p for p in (prod.name, *prod.names.values()) if p).lower()
        if all(bt in name_lower for bt in brand_tokens):
            if primary_concept is None or match_concept_in_text(
                primary_concept, name_lower, frozenset(_WORD_RE.findall(name_lower))
            ):
                return prod
    return None


def normalise_item_for_planning(
    item: RequestItem,
    *,
    search_result: SearchProductsResult,
    products_by_id: Mapping[str, Product],
) -> tuple[RequestItem, Product | None, list[str]]:
    """Normalise an item's unit/quantity when pack sizes or case units convert to kg/l, and resolve named products.

    Examples:
    - Row 8: `10 cases` of `465039` (`contentPerSalesUnit = 5 l` per `KI` case) -> `50 l`, `productId="465039"`.
    - Row 24: `40 pcs` of `URTEKRAM gluten-free fusilli 250 g` (`470699`, `0.25 kg` per pack) -> `10 kg`, `productId="470699"`.
    """
    notes: list[str] = []
    if requested_fat_percent(item.preferences, item.what) is not None:
        narrowed_candidates = narrow_candidates_by_fat_preference(
            search_result.candidates,
            item.preferences,
            query=item.what,
            target_product_id=item.productId,
        )
        search_result.candidates[:] = narrowed_candidates
    named_prod = _find_named_product_in_results(item, search_result, products_by_id)
    updated_item = item
    if named_prod is not None and updated_item.productId is None:
        # Only bind productId when no compliant candidate matches those brand tokens
        tokens = [t.lower() for t in _WORD_RE.findall(item.what) if len(t) >= 4]
        primary_concept, parent_concept = _concept_for_item(item)
        concept_terms = _concept_terms(primary_concept) | _concept_terms(parent_concept)
        brand_tokens = [t for t in tokens if not any(t.startswith(ct) or ct.startswith(t) for ct in concept_terms)]
        has_compliant_brand = any(
            all(bt in " ".join(p for p in (c.name, *c.names.values()) if p).lower() for bt in brand_tokens)
            for c in search_result.candidates
        )
        if not has_compliant_brand:
            updated_item = updated_item.model_copy(update={"productId": named_prod.productId})
        else:
            named_prod = None

    if updated_item.unit in ("cases", "pcs"):
        candidate_units = {
            c.contentPerSalesUnit.unit
            for c in search_result.candidates
            if c.contentPerSalesUnit is not None
        }
        # 1. Named product has declared/estimated content in kg or l, and substitute candidates use kg/l
        if (
            named_prod is not None
            and named_prod.contentPerSalesUnit is not None
            and named_prod.contentPerSalesUnit.unit in ("l", "kg")
            and (
                updated_item.unit == "cases"
                or named_prod.salesUnit.upper() not in PIECE_UNITS
                or named_prod.contentPerSalesUnit.unit in candidate_units
            )
        ):
            cpsu = named_prod.contentPerSalesUnit
            if not (updated_item.unit == "pcs" and cpsu.value == Decimal(1) and "pcs" in candidate_units):
                converted = (updated_item.quantity * cpsu.value).normalize()
                target_unit = cpsu.unit
                notes.append(
                    f"{format(updated_item.quantity.normalize(), 'f')} {updated_item.unit} "
                    f"({format(cpsu.value.normalize(), 'f')} {target_unit} each) = "
                    f"{format(converted, 'f')} {target_unit}"
                )
                updated_item = updated_item.model_copy(update={"quantity": converted, "unit": target_unit})
                return updated_item, named_prod, notes

        # 2. Pack size preference ("250 g", "500 g", "1 l") when candidates are sized in kg or l
        pack_conv = _parse_pack_size_preference(updated_item.preferences)
        if pack_conv is not None:
            pack_val, pack_unit = pack_conv
            if pack_unit in candidate_units and updated_item.unit not in candidate_units:
                converted = (updated_item.quantity * pack_val).normalize()
                notes.append(
                    f"{format(updated_item.quantity.normalize(), 'f')} {updated_item.unit} × "
                    f"{format(pack_val.normalize(), 'f')} {pack_unit} = "
                    f"{format(converted, 'f')} {pack_unit}"
                )
                updated_item = updated_item.model_copy(update={"quantity": converted, "unit": pack_unit})

    return updated_item, named_prod, notes


def select_curated_static_excluded(
    item: RequestItem,
    search_result: SearchProductsResult,
    products_by_id: Mapping[str, Product],
    *,
    chosen_product_ids: Iterable[str] = (),
    max_claim_missing: int = 4,
) -> list[ExcludedProduct]:
    """Select the high-signal static exclusions for the proposal's Excluded list (P8 B).

    - Keeps `SUBSTITUTE_REJECTED` entries for the chosen proposal lines (`substituteFor in chosen_ids`).
    - Keeps all `DATA_CONFLICT` (`K1`, `K2`, `K5`, `K6`), `ALLERGEN_UNKNOWN`, `ALLERGEN_IMPLICIT`, and
      `ALLERGEN_MAY_CONTAIN` entries on products that claimed the requested dietary property or were named.
    - Keeps up to `max_claim_missing` `CLAIM_MISSING` / `ALLERGEN_CONTAINS` entries (prioritising brief IDs).
    """
    chosen_set = set(chosen_product_ids)
    req_claims = {r.code for r in item.requirements if r.kind == "CLAIM"}
    curated: list[ExcludedProduct] = []
    claim_missing_bucket: list[ExcludedProduct] = []

    for ex in search_result.excluded:
        if ReasonCode.DATA_CONFLICT in ex.reasons:
            if ex.substituteFor and ex.substituteFor not in chosen_set:
                cleaned_reasons = [ReasonCode(r) for r in ex.reasons if r != ReasonCode.SUBSTITUTE_REJECTED]
                curated.append(ex.model_copy(update={"reasons": cleaned_reasons, "substituteFor": None}))
            else:
                curated.append(ex)
            continue
        if ReasonCode.SUBSTITUTE_REJECTED in ex.reasons:
            if not chosen_set or ex.substituteFor in chosen_set:
                curated.append(ex)
            continue
        if any(
            c in ex.reasons
            for c in (ReasonCode.ALLERGEN_UNKNOWN, ReasonCode.ALLERGEN_IMPLICIT, ReasonCode.ALLERGEN_MAY_CONTAIN)
        ):
            prod = products_by_id.get(ex.productId)
            # In STRICT mode, highlight products that actually carry the requested claim (e.g. URTEKRAM GF pasta
            # 470699 and 460302) or were explicitly named, rather than every unrelated product in the category.
            if (
                item.productId == ex.productId
                or ex.productId in BRIEF_PRODUCT_IDS
                or (prod is not None and req_claims and req_claims.issubset(set(prod.claims)))
                or not req_claims
            ):
                curated.append(ex)
            continue
        claim_missing_bucket.append(ex)

    claim_missing_bucket.sort(key=lambda e: (0 if e.productId in BRIEF_PRODUCT_IDS else 1, e.productId))
    curated.extend(claim_missing_bucket[:max_claim_missing])
    return curated


def build_shortlist_offer_items(
    item: RequestItem,
    *,
    ranked_candidates: Sequence[RankedCandidate],
    live_excluded_ids: Sequence[str] = (),
    static_strict_excluded_ids: Sequence[str] = (),
    limit: int = MAX_SHORTLIST_PER_ITEM,
) -> list[OfferItem]:
    """Assemble `<= 20` `OfferItem`s for `getOffer` so `offers.py`:
    1) records live-excluded shortlist candidates in `offer.excluded`, and
    2) allocates orderable candidates in exact §7.6 ranked order (`same product type first`, §7.7).
    """
    ordered_ids: list[str] = []

    def add_id(pid: str) -> None:
        if pid not in ordered_ids and len(ordered_ids) < limit:
            ordered_ids.append(pid)

    # First: candidates on the shortlist that fail live rules (CUTOFF_PASSED, NOT_AVAILABLE, etc.) or STRICT
    # missing-declaration rules, so `offers.build_offer` records them in `offer.excluded` before `remaining <= 0`.
    for pid in live_excluded_ids:
        add_id(pid)
    for pid in static_strict_excluded_ids:
        add_id(pid)

    # Next: compliant deliverable candidates in §7.6 ranked order.
    # Per §7.7 ("same product type first"), if non-substitute candidates cover the full need, do not mix
    # different-product-type agent substitutes into the same item allocation.
    # T69: when exact-type stock covers only part of the line (`0 < shortfall < item.quantity`), include
    # ONE compliant top-up candidate from the shortlist that has stock for the shortfall.
    has_direct_candidates = any(rc.substitutionSource is None for rc in ranked_candidates)
    has_exact_type = any(rc.exactType for rc in ranked_candidates)

    exact_covered = Decimal(0)
    for rc in ranked_candidates:
        if rc.exactType and (rc.substitutionSource is None or item.productId is not None):
            if rc.stockTier in ("PLENTY", "SUPPLIER", "UNKNOWN"):
                exact_covered = max(exact_covered, item.quantity)
            elif rc.deliveredQuantity is not None:
                exact_covered += rc.deliveredQuantity.value

    shortfall = max(item.quantity - exact_covered, Decimal(0))
    need_top_up = has_exact_type and Decimal(0) < shortfall < item.quantity

    agent_sub_added = False
    for rc in ranked_candidates:
        is_sub = (rc.substitutionSource == "AGENT" and item.productId is None) or not rc.exactType
        if is_sub and has_exact_type:
            if not need_top_up or agent_sub_added:
                continue
            if any(
                bad in rc.tradeoff
                for bad in ("does not whip", "cooking cream, not whipping cream", "margarine or fat spread")
            ):
                continue
            covers_shortfall = rc.stockTier in ("PLENTY", "SUPPLIER", "UNKNOWN") or (
                rc.deliveredQuantity is not None and rc.deliveredQuantity.value >= shortfall
            )
            if not covers_shortfall:
                continue
            agent_sub_added = True
            add_id(rc.productId)
            continue

        if has_direct_candidates and rc.substitutionSource == "AGENT" and item.productId is None:
            continue
        if has_exact_type and not rc.exactType:
            continue
        if rc.substitutionSource == "AGENT" or not rc.exactType:
            if agent_sub_added:
                continue
            agent_sub_added = True
        add_id(rc.productId)

    return [
        OfferItem(
            itemId=item.itemId,
            productId=pid,
            quantity=item.quantity,
            unit=item.unit,
        )
        for pid in ordered_ids
    ]


def good_better_best_alternatives(ranked_candidates: Sequence[RankedCandidate]) -> list[RankedCandidate]:
    """Return the Recommended + Best / Better / Good options (up to 4 proven-compliant candidates, §7.6 / T22)."""
    return [
        rc
        for rc in ranked_candidates
        if rc.tier in ("RECOMMENDED", "BEST", "BETTER", "GOOD")
    ][:4]


# ---- T77: material variant choices the buyer did not state -------------------------------------------


class VariantChoice(Contract):
    """One material attribute the compliant shortlist differs in, and the value the agent chose."""

    label: str  # "Cream type", "Fresh or frozen", "Salt", "Lactose", "Organic", "Pack size"
    value: str  # the recommended product's value, or "unknown" when its data does not say
    others: list[str] = Field(default_factory=list)
    restricts: bool = False  # the item is kept to this value (never a silent fresh + frozen mix)

    @property
    def chip(self) -> str:
        return f"{self.label}: {self.value}"


def _names(product: Product) -> tuple[str, ...]:
    return (product.name, *product.names.values())


def _pack_text(value: Decimal, unit: str) -> str:
    if unit in ("kg", "l") and value < 1:
        return f"{_pct(value * 1000)} {'g' if unit == 'kg' else 'ml'}"
    return f"{_pct(value)} {unit}"


def variant_choices(
    item: RequestItem, ranked: Sequence[RankedCandidate], products_by_id: Mapping[str, Product]
) -> list[VariantChoice]:
    """Material attributes in which the compliant, exact-type candidates differ and the buyer said nothing.

    Values come only from product data (names in every language, claims, labels, declared pack content).
    Cream type, fresh/frozen and salt are material use choices: the item is kept to the recommended value.
    Lactose, organic and a ≥5× pack-size spread are stated so the buyer can see and change them.
    """
    exact = [products_by_id[rc.productId] for rc in ranked if rc.exactType and rc.productId in products_by_id]
    if len(exact) < 2:
        return []
    primary, _ = _concept_for_item(item)
    concept_id = primary.concept_id if primary else None
    stated = stated_groups(item.what, concept_id)
    choices: list[VariantChoice] = []

    for group_id, label in VARIANT_CHOICE_LABELS.items():
        if group_id in stated:
            continue
        values = [product_variant(group_id, _names(p)) for p in exact]
        distinct = {v for v in values if v}
        if len(distinct) < 2:
            continue
        chosen = values[0] or "unknown"
        choices.append(VariantChoice(label=label, value=chosen, others=sorted(distinct - {chosen}), restricts=True))

    codes = {r.code for r in item.requirements}
    if concept_id in DAIRY_CONCEPTS and "FREE_FROM_LACTOSE" not in codes:
        lactose = ["lactose-free" if "FREE_FROM_LACTOSE" in p.claims else "regular" for p in exact]
        if len(set(lactose)) > 1:
            choices.append(VariantChoice(label="Lactose", value=lactose[0],
                                         others=sorted(set(lactose) - {lactose[0]})))
    if not any(p.kind == "ORGANIC" for p in item.preferences):
        organic = ["organic" if is_organic(p) else "not organic" for p in exact]
        if len(set(organic)) > 1:
            choices.append(VariantChoice(label="Organic", value="yes" if organic[0] == "organic" else "no",
                                         others=["yes" if organic[0] != "organic" else "no"]))
    if not any(p.kind == "PACK_SIZE" for p in item.preferences):
        packs = [p.contentPerSalesUnit for p in exact]
        known = [c for c in packs if c is not None and c.unit == item.unit]
        if packs[0] is not None and packs[0].unit == item.unit and len(known) > 1:
            smallest, largest = min(c.value for c in known), max(c.value for c in known)
            if smallest > 0 and largest / smallest >= 5:
                chosen_pack = _pack_text(packs[0].value, packs[0].unit)
                others = sorted({_pack_text(c.value, c.unit) for c in known} - {chosen_pack})
                choices.append(VariantChoice(label="Pack size", value=chosen_pack, others=others))
    return choices


def keep_chosen_variants(
    ranked: Sequence[RankedCandidate], choices: Sequence[VariantChoice], products_by_id: Mapping[str, Product]
) -> list[RankedCandidate]:
    """Drop exact-type candidates whose value differs from the chosen one in a restricting group, so an
    item is never filled half fresh, half frozen (or half whipping, half cooking cream)."""
    restricting = [(gid, c) for c in choices if c.restricts
                   for gid, label in VARIANT_CHOICE_LABELS.items() if label == c.label]
    if not restricting:
        return list(ranked)
    kept: list[RankedCandidate] = []
    for rc in ranked:
        product = products_by_id.get(rc.productId)
        if rc.exactType and product is not None and any(
            (product_variant(gid, _names(product)) or "unknown") != choice.value for gid, choice in restricting
        ):
            continue
        kept.append(rc)
    return kept


# ---- T44: a feasible order within the hard limit (information only) -------------------------------------


class FeasibleLine(Contract):
    productId: str
    name: str
    quantitySalesUnits: Decimal
    salesUnitLabel: str
    deliveredQuantity: Quantity
    lineTotalExclVat: Decimal


class FeasibleOrder(Contract):
    """What fits under the hard limit when an order is BLOCKed by OVER_HARD_LIMIT. Never created or ordered."""

    hardLimitEur: Decimal
    totalExclVat: Decimal
    lines: list[FeasibleLine]
    text: str


def _floor_units(units: Decimal, lot: Decimal) -> Decimal:
    if lot <= 0:
        return units
    return (units / lot).to_integral_value(rounding=ROUND_FLOOR) * lot


def _qty_text(value: Decimal, unit: str) -> str:
    return f"{format(value.normalize(), ',f')} {unit}"


def _is_valid_feasible_line(ln: OfferLine, item: RequestItem | None, mandate: Mandate) -> bool:
    """T51: feasible-within-limit hints may use only valid, compliant, same-family lines."""
    if any(r in _NON_ORDERABLE_CODES for r in ln.reasons):
        return False
    if ln.unitPriceExclVat <= 0 or ln.contentPerSalesUnit.value <= 0 or ln.quantitySalesUnits <= 0:
        return False
    if item is not None:
        facts_by_key = {f.fact: f for f in ln.facts}
        for req in hard_requirements(item.requirements, mandate):
            key = f"claim.{req.code}" if req.kind == "CLAIM" else f"allergen.{req.code}"
            fact = facts_by_key.get(key)
            if fact is not None:
                if fact.level == "UNKNOWN" or fact.conflict is not None:
                    return False
                if req.kind == "CLAIM" and fact.value is False:
                    return False
                if req.kind == "AVOID_ALLERGEN" and fact.value in ("CONTAINS", "MAY_CONTAIN", None):
                    return False
        if item.productId != ln.productId:
            is_syn = ln.productId.startswith("SYN-") or ln.name.lower().startswith("synthetic syn-")
            if not _item_same_product_family(
                (ln.name, *([ln.nameFi] if ln.nameFi else [])),
                item,
                category="synthetic" if is_syn else "",
            ):
                return False
    return True


def feasible_within_hard_limit(
    proposal: Proposal, mandate: Mandate, lot_sizes: Mapping[str, Decimal] | None = None
) -> FeasibleOrder | None:
    """Scale a hard-limit BLOCK down to the largest order (same lines, same proportions, whole lots) whose
    total excl. VAT stays within the hard limit. Information for the buyer only: nothing is proposed or
    ordered, and no other BLOCK reason may be present (e.g. a forbidden allergen)."""
    if proposal.decision != "BLOCK" or ReasonCode.OVER_HARD_LIMIT not in proposal.reasons:
        return None
    if any(r in (ReasonCode.ALLERGEN_CONTAINS,) for r in proposal.reasons) or not proposal.lines:
        return None
    items_by_id = {it.itemId: it for it in proposal.request.items}
    valid_lines = [ln for ln in proposal.lines if _is_valid_feasible_line(ln, items_by_id.get(ln.itemId), mandate)]
    if not valid_lines:
        return None
    total = sum((ln.lineTotalExclVat for ln in valid_lines), Decimal(0))
    limit = mandate.hardLimitEur
    if total <= 0 or (len(valid_lines) == len(proposal.lines) and total <= limit):
        return None
    given = lot_sizes or {}

    def lot(ln: OfferLine) -> Decimal:
        # Countable sales units (bottles, bags, cases) are only sold whole, whatever a fractional lot says.
        size_ = given.get(ln.productId, Decimal(1))
        if ln.salesUnit.upper() not in WEIGHT_UNITS | VOLUME_UNITS and size_ < 1:
            return Decimal(1)
        return size_

    factor = min(Decimal(1), limit / total)
    lots = {ln.lineId: lot(ln) for ln in valid_lines}
    units = {ln.lineId: _floor_units(ln.quantitySalesUnits * factor, lots[ln.lineId]) for ln in valid_lines}

    def line_total(ln: OfferLine) -> Decimal:
        return (units[ln.lineId] * ln.unitPriceExclVat).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Rounding can never push the sum over the limit, but stay safe: drop whole lots from the largest line.
    while sum((line_total(ln) for ln in valid_lines), Decimal(0)) > limit:
        largest = max(valid_lines, key=line_total)
        units[largest.lineId] -= lots[largest.lineId]
    feasible = [
        FeasibleLine(
            productId=ln.productId,
            name=ln.name,
            quantitySalesUnits=units[ln.lineId],
            salesUnitLabel=ln.salesUnitLabel,
            deliveredQuantity=Quantity(value=units[ln.lineId] * ln.contentPerSalesUnit.value,
                                       unit=ln.deliveredQuantity.unit),
            lineTotalExclVat=line_total(ln),
        )
        for ln in valid_lines
        if units[ln.lineId] > 0
    ]
    if not feasible:
        return None
    feasible_total = sum((f.lineTotalExclVat for f in feasible), Decimal(0))
    what = " + ".join(_qty_text(f.deliveredQuantity.value, f.deliveredQuantity.unit) for f in feasible)
    text = (
        f"Feasible within your {format_money(limit)} hard limit: {what} for {format_money(feasible_total)} "
        "excl. VAT. Information only; nothing has been proposed or ordered."
    )
    return FeasibleOrder(hardLimitEur=limit, totalExclVat=feasible_total, lines=feasible, text=text)


__all__ = [
    "MAX_SHORTLIST_PER_ITEM",
    "VariantChoice",
    "keep_chosen_variants",
    "variant_choices",
    "FeasibleLine",
    "FeasibleOrder",
    "feasible_within_hard_limit",
    "AgentPlan",
    "CandidateObservation",
    "CandidateTier",
    "ItemPlan",
    "RankedCandidate",
    "SubstitutionKind",
    "ValioSubstituteCheck",
    "build_shortlist_offer_items",
    "check_valio_substitutes",
    "good_better_best_alternatives",
    "is_compliant_substitute",
    "normalise_item_for_planning",
    "observation_from_offer_line",
    "rank_candidates",
    "requested_type_match",
    "requirement_display_name",
    "select_curated_static_excluded",
]
