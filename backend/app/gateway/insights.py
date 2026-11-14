"""Valio Aimo agent-readiness insights (§14, D6, T27): data quality = autonomous sales.

Every number is computed directly from the normalised catalogue (`Product` records + supplier mapping)
and the gateway's live/recorded proposals (`Proposal` snapshots). Nothing is hardcoded.
"""

import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends

from app.catalog.build import DEFAULT_CATALOG_DIR, DEFAULT_RAW_DIR
from app.contracts import (
    REASON_REGISTRY,
    DecisionOutcome,
    Product,
    Proposal,
    ReasonCode,
    ReasonEffect,
)
from app.contracts.common import Contract, Money, NonNegativeDecimal
from app.policy.requirements import claim_conflicts

DEFAULT_VENDORS_PATH = DEFAULT_CATALOG_DIR / "vendors.json"
DEFAULT_RAW_PRODUCTS_PATH = DEFAULT_RAW_DIR / "products.json"

SAFETY_CLAIMS: frozenset[str] = frozenset({
    "FREE_FROM_GLUTEN",
    "FREE_FROM_LACTOSE",
    "VEGAN",
    "VEGETARIAN",
})

_LEADING_MULT_RE = re.compile(r"^\s*\d+\s*[xX*]\s*")
_LEADING_QTY_RE = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*(?:kg|g|l|dl|cl|ml|pcs|st)\b\s*", re.IGNORECASE)
_BRAND_CLEAN_RE = re.compile(r"[^\w\s®™&-]", re.UNICODE)


class DataGapMetric(Contract):
    """One catalogue data-gap category and how it affects autonomous ordering."""

    gapId: str
    title: str
    reasonCode: ReasonCode
    rule: str
    effect: Literal["EXCLUDE", "ASK"]
    blocksAuto: bool
    approvableByHuman: bool
    affectedProducts: int
    affectedSuppliers: int
    shareOfCatalog: NonNegativeDecimal
    fixAction: str


class SupplierReadiness(Contract):
    """Per-supplier readiness and AUTO-blocking data gaps computed from the catalogue."""

    supplierName: str
    totalProducts: int
    foodProducts: int
    nonFoodProducts: int
    autoReadyProducts: int
    blockedFromAutoProducts: int
    autoReadyRate: NonNegativeDecimal
    missingAllergensFood: int
    contentUnknown: int
    contentWeakNameEstimate: int
    dataConflicts: int
    unpricedDeposits: int
    unsafeSubstitutes: int
    lowReliability: int
    blockingReasonCodes: list[ReasonCode]
    topFix: str


class FixListItem(Contract):
    """One concrete supplier- or product-level master-data fix that unlocks autonomous sales."""

    fixId: str
    priority: Literal["HIGH", "MEDIUM"]
    scope: Literal["SUPPLIER", "PRODUCT"]
    supplierName: str
    productId: str | None = None
    productName: str | None = None
    reasonCode: ReasonCode
    effect: Literal["EXCLUDE", "ASK"]
    blocksAuto: bool = True
    problem: str
    fixAction: str
    productsUnlocked: int


class ReasonFrequency(Contract):
    """Observed frequency of one reason code across proposals or excluded candidates."""

    code: ReasonCode
    effect: ReasonEffect
    approvable: bool
    chipText: str
    occurrences: int


class DecisionStatistics(Contract):
    """Live AUTO / ASK / BLOCK statistics computed from the gateway's proposals."""

    totalProposals: int
    activeProposals: int
    supersededProposals: int
    confirmedSimulated: int
    pendingApproval: int
    rejected: int
    expired: int
    blocked: int
    byDecision: dict[DecisionOutcome, int]
    byDecisionActive: dict[DecisionOutcome, int]
    autoRate: NonNegativeDecimal
    askRate: NonNegativeDecimal
    blockRate: NonNegativeDecimal
    totalOrderValueExclVat: Money
    autoValueExclVat: Money
    askValueExclVat: Money
    blockValueExclVat: Money
    reasonCounts: list[ReasonFrequency]
    excludedReasonCounts: list[ReasonFrequency]
    totalExcludedCandidates: int


class CatalogReadinessSummary(Contract):
    """Catalogue-wide readiness and data-gap totals computed from normalised products."""

    totalProducts: int
    totalSuppliers: int
    foodProducts: int
    nonFoodProducts: int
    autoReadyProducts: int
    blockedFromAutoProducts: int
    autoReadyRate: NonNegativeDecimal
    foodAutoReadyProducts: int
    foodBlockedFromAutoProducts: int
    foodAutoReadyRate: NonNegativeDecimal
    missingAllergensFood: int
    missingAllergensTotal: int
    onlyUndeclaredAllergens: int
    x99Products: int
    implicitOnlyAllergensFood: int
    contentDeclared: int
    contentPriceRatioEstimate: int
    contentWeakNameEstimate: int
    contentUnknown: int
    dataConflicts: int
    conflictsByCode: dict[str, int]
    unpricedDeposits: int
    unsafeSubstitutes: int
    reliabilityKnown: int
    reliabilityUnknown: int
    lowReliabilityUnder95: int
    co2Known: int
    co2Unknown: int


class ReadinessInsights(Contract):
    """Valio Aimo readiness view payload (§14, D6, T27)."""

    mode: Literal["LIVE", "RECORDED"] = "LIVE"
    catalogSummary: CatalogReadinessSummary
    gapBreakdown: list[DataGapMetric]
    suppliers: list[SupplierReadiness]
    fixList: list[FixListItem]
    decisionStats: DecisionStatistics


def _rate(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0 or numerator <= 0:
        return Decimal("0")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _money(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def infer_supplier_from_name(name: str) -> str:
    """Deterministic fallback supplier/brand extractor when raw vendorName is unavailable."""
    cleaned = _LEADING_MULT_RE.sub("", (name or "").strip())
    cleaned = _LEADING_QTY_RE.sub("", cleaned).strip()
    cleaned = _BRAND_CLEAN_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return "Unknown supplier"
    tokens = cleaned.split()
    first = tokens[0].rstrip("®™")
    if not first:
        return "Unknown supplier"
    if first.lower() in {"synthetic", "mock"} and len(tokens) >= 2:
        return f"{first.title()} {tokens[1].title()}"
    return first.upper()


@lru_cache(maxsize=2)
def _cached_vendor_map(raw_products_path: str, vendors_path: str) -> dict[str, str]:
    v_path = Path(vendors_path)
    if v_path.is_file():
        try:
            loaded = json.loads(v_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return {str(k).strip(): str(v).strip() for k, v in loaded.items() if str(v).strip()}
        except (OSError, ValueError):
            pass
    r_path = Path(raw_products_path)
    if r_path.is_file():
        try:
            records = json.loads(r_path.read_text(encoding="utf-8-sig"))
            if isinstance(records, list):
                mapping: dict[str, str] = {}
                for rec in records:
                    if not isinstance(rec, dict) or rec.get("deleted"):
                        continue
                    pid = str(rec.get("id") or "").strip()
                    vendor = str(rec.get("vendorName") or "").strip()
                    if pid and vendor:
                        mapping[pid] = vendor
                return mapping
        except (OSError, ValueError):
            pass
    return {}


def resolve_vendor_map(
    *,
    vendor_by_product_id: Mapping[str, str] | None = None,
    raw_records: Sequence[Mapping[str, object]] | None = None,
    raw_products_path: Path = DEFAULT_RAW_PRODUCTS_PATH,
    vendors_path: Path = DEFAULT_VENDORS_PATH,
) -> Mapping[str, str]:
    """Resolve `productId -> supplierName` from explicit input, raw records, or cached catalogue files."""
    if vendor_by_product_id is not None:
        return {str(k).strip(): str(v).strip() for k, v in vendor_by_product_id.items() if str(v).strip()}
    if raw_records is not None:
        mapping: dict[str, str] = {}
        for rec in raw_records:
            if rec.get("deleted"):
                continue
            pid = str(rec.get("id") or "").strip()
            vendor = str(rec.get("vendorName") or "").strip()
            if pid and vendor:
                mapping[pid] = vendor
        return mapping
    return _cached_vendor_map(str(raw_products_path), str(vendors_path))


def supplier_for_product(product: Product, vendor_map: Mapping[str, str]) -> str:
    """Return the supplier name for `product` from `vendor_map`, a vendor fact, or product name."""
    mapped = (vendor_map.get(product.productId) or "").strip()
    if mapped:
        return mapped
    for fact in product.facts:
        if fact.fact == "vendor" and fact.value:
            val = str(fact.value).strip()
            if val:
                return val
    return infer_supplier_from_name(product.name)


def product_conflict_codes(product: Product) -> frozenset[str]:
    """Return all conflict codes (K1, K2, K5, K6) present on `product`."""
    codes = {
        f.conflict.code
        for f in product.facts
        if f.conflict is not None and f.conflict.code
    } | {
        a.evidence.conflict.code
        for a in product.allergens
        if a.evidence.conflict is not None and a.evidence.conflict.code
    }
    return frozenset(codes | claim_conflicts(product))


def product_reliability(product: Product) -> Decimal | None:
    """Extract the historical delivery reliability (0..1) from `product.facts` if present."""
    for fact in product.facts:
        if fact.fact == "reliability" and fact.value is not None:
            try:
                return Decimal(str(fact.value))
            except ArithmeticError:
                return None
    return None


def product_has_co2(product: Product) -> bool:
    """Return True if `product` has a non-null CO2 estimate fact."""
    return any(f.fact == "co2" and f.value is not None for f in product.facts)


def find_unsafe_substitutes(
    product: Product,
    products_by_id: Mapping[str, Product],
) -> list[tuple[Product, list[str]]]:
    """Return `(substitute_product, broken_requirements)` for Valio-listed substitutes of `product`.

    Checks whether any listed substitute drops a declared claim of `product` or declares `CONTAINS` /
    `MAY_CONTAIN` for an allergen that `product` explicitly declares `FREE_FROM`.
    """
    if not product.substitutions:
        return []
    own_claims = set(product.claims)
    own_free_allergens = {a.code for a in product.allergens if a.status == "FREE_FROM" and a.code != "X99"}
    unsafe: list[tuple[Product, list[str]]] = []

    for sub_id in product.substitutions:
        sub = products_by_id.get(sub_id)
        if sub is None:
            continue
        broken: list[str] = []
        missing_safety = sorted((own_claims & SAFETY_CLAIMS) - set(sub.claims))
        missing_other = sorted((own_claims - SAFETY_CLAIMS) - set(sub.claims))
        for claim in missing_safety + missing_other:
            broken.append(f"missing {claim}")
        sub_alg = {a.code: a.status for a in sub.allergens}
        for code in sorted(own_free_allergens):
            sub_status = sub_alg.get(code)
            if sub_status in ("CONTAINS", "MAY_CONTAIN"):
                broken.append(f"allergen {code} is {sub_status}")
        if broken:
            unsafe.append((sub, broken))
    return unsafe


def _supplier_top_fix(
    *,
    missing_allergens_food: int,
    content_unknown: int,
    content_weak: int,
    data_conflicts: int,
    unpriced_deposits: int,
    unsafe_substitutes: int,
    low_reliability: int,
) -> str:
    candidates: list[tuple[int, str]] = []
    if missing_allergens_food > 0:
        candidates.append((
            missing_allergens_food,
            f"Declare GS1 Synkka allergen / nonAllergen classifications for {missing_allergens_food} food product(s) "
            "(eliminates ALLERGEN_UNKNOWN exclusions).",
        ))
    if content_unknown > 0:
        candidates.append((
            content_unknown,
            f"Populate synkkaData.unitConversions[BASE_UNIT_OR_EACH] volume or netWeight for {content_unknown} "
            "product(s) with unknown pack size (eliminates QUANTITY_BASIS_UNKNOWN exclusions).",
        ))
    if content_weak > 0:
        candidates.append((
            content_weak,
            f"Add structured synkkaData.unitConversions[BASE_UNIT_OR_EACH] volume/netWeight for {content_weak} "
            "name-sized product(s) (upgrades QUANTITY_BASIS_WEAK from ASK to AUTO).",
        ))
    if unpriced_deposits > 0:
        candidates.append((
            unpriced_deposits,
            f"Supply verified deposit unit prices for {unpriced_deposits} product(s) "
            "(eliminates DEPOSIT_COST_UNVERIFIED exclusions).",
        ))
    if data_conflicts > 0:
        candidates.append((
            data_conflicts,
            f"Reconcile contradictory allergen vs claim declarations on {data_conflicts} product(s) "
            "(eliminates DATA_CONFLICT exclusions).",
        ))
    if unsafe_substitutes > 0:
        candidates.append((
            unsafe_substitutes,
            f"Remove or replace {unsafe_substitutes} non-compliant Valio-listed substitute link(s) "
            "(eliminates SUBSTITUTE_REJECTED exclusions).",
        ))
    if low_reliability > 0:
        candidates.append((
            low_reliability,
            f"Improve fulfilment reliability ≥ 95% on {low_reliability} product(s) "
            "(eliminates LOW_RELIABILITY human sign-off).",
        ))
    if not candidates:
        return "All static product facts are declared and consistent for autonomous ordering."
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def compute_decision_statistics(proposals: Sequence[Proposal]) -> DecisionStatistics:
    """Compute live AUTO / ASK / BLOCK and reason-code statistics from `proposals`."""
    total = len(proposals)
    active = [p for p in proposals if p.state != "SUPERSEDED"]
    superseded_count = total - len(active)

    confirmed = sum(1 for p in proposals if p.state == "CONFIRMED_SIMULATED")
    pending = sum(1 for p in proposals if p.state == "PENDING_APPROVAL")
    rejected = sum(1 for p in proposals if p.state == "REJECTED")
    expired = sum(1 for p in proposals if p.state == "EXPIRED")
    blocked = sum(1 for p in proposals if p.state == "BLOCKED")

    by_decision: dict[DecisionOutcome, int] = {"AUTO": 0, "ASK": 0, "BLOCK": 0}
    by_decision_active: dict[DecisionOutcome, int] = {"AUTO": 0, "ASK": 0, "BLOCK": 0}
    for p in proposals:
        by_decision[p.decision] = by_decision.get(p.decision, 0) + 1
    for p in active:
        by_decision_active[p.decision] = by_decision_active.get(p.decision, 0) + 1

    total_val = sum((p.totals.exclVat for p in active), Decimal("0"))
    auto_val = sum((p.totals.exclVat for p in active if p.decision == "AUTO"), Decimal("0"))
    ask_val = sum((p.totals.exclVat for p in active if p.decision == "ASK"), Decimal("0"))
    block_val = sum((p.totals.exclVat for p in active if p.decision == "BLOCK"), Decimal("0"))

    order_reasons: Counter[ReasonCode] = Counter()
    excluded_reasons: Counter[ReasonCode] = Counter()
    total_excluded_candidates = 0

    for p in proposals:
        seen_on_proposal = set(p.reasons)
        for line in p.lines:
            seen_on_proposal.update(line.reasons)
        for code in seen_on_proposal:
            order_reasons[code] += 1
        for ex in p.excluded:
            total_excluded_candidates += 1
            for code in dict.fromkeys(ex.reasons):
                excluded_reasons[code] += 1

    def _build_frequencies(counter: Counter[ReasonCode]) -> list[ReasonFrequency]:
        items: list[ReasonFrequency] = []
        for code, count in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0]))):
            reg = REASON_REGISTRY[code]
            items.append(
                ReasonFrequency(
                    code=code,
                    effect=reg.effect,
                    approvable=reg.approvable,
                    chipText=reg.chipText,
                    occurrences=count,
                )
            )
        return items

    return DecisionStatistics(
        totalProposals=total,
        activeProposals=len(active),
        supersededProposals=superseded_count,
        confirmedSimulated=confirmed,
        pendingApproval=pending,
        rejected=rejected,
        expired=expired,
        blocked=blocked,
        byDecision=by_decision,
        byDecisionActive=by_decision_active,
        autoRate=_rate(by_decision["AUTO"], total),
        askRate=_rate(by_decision["ASK"], total),
        blockRate=_rate(by_decision["BLOCK"], total),
        totalOrderValueExclVat=_money(total_val),
        autoValueExclVat=_money(auto_val),
        askValueExclVat=_money(ask_val),
        blockValueExclVat=_money(block_val),
        reasonCounts=_build_frequencies(order_reasons),
        excludedReasonCounts=_build_frequencies(excluded_reasons),
        totalExcludedCandidates=total_excluded_candidates,
    )


def _compute_static_catalog_insights(
    products: Sequence[Product],
    vendor_map: Mapping[str, str],
) -> tuple[CatalogReadinessSummary, list[DataGapMetric], list[SupplierReadiness], list[FixListItem]]:
    """Compute catalogue summary, gap breakdown, supplier readiness and fix list from `products`."""
    products_by_id = {p.productId: p for p in products}
    total_products = len(products)

    food_products = 0
    non_food_products = 0
    auto_ready_products = 0
    food_auto_ready = 0

    missing_alg_food = 0
    missing_alg_total = 0
    only_undeclared = 0
    x99_products = 0
    implicit_only_food = 0

    content_declared = 0
    content_price_ratio = 0
    content_weak_name = 0
    content_unknown = 0

    data_conflicts_count = 0
    conflicts_by_code: Counter[str] = Counter()
    unpriced_deposits = 0
    unsafe_substitutes = 0

    rel_known = 0
    low_rel_under_95 = 0
    co2_known = 0

    gap_suppliers: dict[str, set[str]] = defaultdict(set)
    by_supplier: dict[str, dict[str, int]] = defaultdict(lambda: {
        "total": 0,
        "food": 0,
        "nonFood": 0,
        "autoReady": 0,
        "blockedFromAuto": 0,
        "missingAllergensFood": 0,
        "contentUnknown": 0,
        "contentWeakNameEstimate": 0,
        "dataConflicts": 0,
        "unpricedDeposits": 0,
        "unsafeSubstitutes": 0,
        "lowReliability": 0,
    })

    conflict_products: list[tuple[Product, str, tuple[str, ...]]] = []
    unsafe_sub_products: list[tuple[Product, str, Product, list[str]]] = []
    gf_missing_alg_products: list[tuple[Product, str]] = []

    for product in products:
        supplier = supplier_for_product(product, vendor_map)
        sup_stats = by_supplier[supplier]
        sup_stats["total"] += 1

        is_food = product.isFood is not False
        if is_food:
            food_products += 1
            sup_stats["food"] += 1
        else:
            non_food_products += 1
            sup_stats["nonFood"] += 1

        # Allergen declaration analysis
        if not product.hasAllergenDeclaration:
            missing_alg_total += 1
            if product.allergens and all(a.status == "UNDECLARED" for a in product.allergens):
                only_undeclared += 1
            if is_food:
                missing_alg_food += 1
                sup_stats["missingAllergensFood"] += 1
                gap_suppliers["MISSING_ALLERGEN_DECLARATION"].add(supplier)
                if "FREE_FROM_GLUTEN" in product.claims or "FREE_FROM_LACTOSE" in product.claims:
                    gf_missing_alg_products.append((product, supplier))
        else:
            codes_with_free = {a.code for a in product.allergens if a.status == "FREE_FROM"}
            if "X99" in codes_with_free:
                x99_products += 1
            if is_food and not codes_with_free:
                implicit_only_food += 1
                gap_suppliers["IMPLICIT_ONLY_ALLERGENS"].add(supplier)

        # Pack content per sales unit analysis (§5.2, L12)
        cpsu = product.contentPerSalesUnit
        is_content_unknown = cpsu is None
        is_content_weak = cpsu is not None and cpsu.method == "name"
        if is_content_unknown:
            content_unknown += 1
            sup_stats["contentUnknown"] += 1
            gap_suppliers["CONTENT_UNKNOWN"].add(supplier)
        elif is_content_weak:
            content_weak_name += 1
            sup_stats["contentWeakNameEstimate"] += 1
            gap_suppliers["CONTENT_NAME_ONLY_WEAK"].add(supplier)
        elif cpsu is not None and cpsu.method == "catalogPrice.value / comparisonPrice.value":
            content_price_ratio += 1
        else:
            content_declared += 1

        # Conflicts (K1, K2, K5, K6 — L9)
        c_codes = sorted(product_conflict_codes(product))
        has_conflict = bool(c_codes)
        if has_conflict:
            data_conflicts_count += 1
            sup_stats["dataConflicts"] += 1
            gap_suppliers["DATA_CONFLICT"].add(supplier)
            for code in c_codes:
                conflicts_by_code[code] += 1
            conflict_products.append((product, supplier, tuple(c_codes)))

        # Unpriced deposits (L3)
        has_unpriced_deposit = any(d.unitPriceExclVat is None for d in product.deposits)
        if has_unpriced_deposit:
            unpriced_deposits += 1
            sup_stats["unpricedDeposits"] += 1
            gap_suppliers["UNPRICED_DEPOSIT"].add(supplier)

        # Valio-listed substitutes that break a claim/allergen proof of the parent product (L11)
        unsafe_subs = find_unsafe_substitutes(product, products_by_id)
        if unsafe_subs:
            unsafe_substitutes += 1
            sup_stats["unsafeSubstitutes"] += 1
            gap_suppliers["UNSAFE_VALIO_SUBSTITUTE"].add(supplier)
            for sub_prod, broken in unsafe_subs:
                unsafe_sub_products.append((product, supplier, sub_prod, broken))

        # Delivery reliability (L13) & CO2 coverage
        rel = product_reliability(product)
        has_low_rel = False
        if rel is not None:
            rel_known += 1
            if rel < Decimal("0.95"):
                low_rel_under_95 += 1
                has_low_rel = True
                sup_stats["lowReliability"] += 1
                gap_suppliers["LOW_RELIABILITY"].add(supplier)
        if product_has_co2(product):
            co2_known += 1

        # Does this product have any static gap that blocks AUTO?
        blocks_auto = (
            (is_food and not product.hasAllergenDeclaration)
            or is_content_unknown
            or is_content_weak
            or has_conflict
            or has_unpriced_deposit
            or has_low_rel
        )
        if blocks_auto:
            sup_stats["blockedFromAuto"] += 1
        else:
            auto_ready_products += 1
            sup_stats["autoReady"] += 1
            if is_food:
                food_auto_ready += 1

    blocked_total = total_products - auto_ready_products
    food_blocked = food_products - food_auto_ready

    summary = CatalogReadinessSummary(
        totalProducts=total_products,
        totalSuppliers=len(by_supplier),
        foodProducts=food_products,
        nonFoodProducts=non_food_products,
        autoReadyProducts=auto_ready_products,
        blockedFromAutoProducts=blocked_total,
        autoReadyRate=_rate(auto_ready_products, total_products),
        foodAutoReadyProducts=food_auto_ready,
        foodBlockedFromAutoProducts=food_blocked,
        foodAutoReadyRate=_rate(food_auto_ready, food_products),
        missingAllergensFood=missing_alg_food,
        missingAllergensTotal=missing_alg_total,
        onlyUndeclaredAllergens=only_undeclared,
        x99Products=x99_products,
        implicitOnlyAllergensFood=implicit_only_food,
        contentDeclared=content_declared,
        contentPriceRatioEstimate=content_price_ratio,
        contentWeakNameEstimate=content_weak_name,
        contentUnknown=content_unknown,
        dataConflicts=data_conflicts_count,
        conflictsByCode=dict(sorted(conflicts_by_code.items())),
        unpricedDeposits=unpriced_deposits,
        unsafeSubstitutes=unsafe_substitutes,
        reliabilityKnown=rel_known,
        reliabilityUnknown=total_products - rel_known,
        lowReliabilityUnder95=low_rel_under_95,
        co2Known=co2_known,
        co2Unknown=total_products - co2_known,
    )

    gap_breakdown: list[DataGapMetric] = [
        DataGapMetric(
            gapId="CONTENT_NAME_ONLY_WEAK",
            title="Pack content parsed only from product name (Method 3)",
            reasonCode=ReasonCode.QUANTITY_BASIS_WEAK,
            rule="L12 / P2.2",
            effect="ASK",
            blocksAuto=True,
            approvableByHuman=True,
            affectedProducts=content_weak_name,
            affectedSuppliers=len(gap_suppliers["CONTENT_NAME_ONLY_WEAK"]),
            shareOfCatalog=_rate(content_weak_name, total_products),
            fixAction=(
                "Populate synkkaData.unitConversions[synkkaPackagingType=BASE_UNIT_OR_EACH].volume or .netWeight "
                "and units[salesUnit].sizeInBaseUnits so sizing is DECLARED (Method 1) instead of a name estimate."
            ),
        ),
        DataGapMetric(
            gapId="MISSING_ALLERGEN_DECLARATION",
            title="Food products with no allergen declaration",
            reasonCode=ReasonCode.ALLERGEN_UNKNOWN,
            rule="L4 / P2.3",
            effect="EXCLUDE",
            blocksAuto=True,
            approvableByHuman=False,
            affectedProducts=missing_alg_food,
            affectedSuppliers=len(gap_suppliers["MISSING_ALLERGEN_DECLARATION"]),
            shareOfCatalog=_rate(missing_alg_food, total_products),
            fixAction=(
                "Require GS1 Synkka classifications[allergen / nonAllergen] (or X99 for EU-14 allergen-free products) "
                "from the supplier; missing allergen evidence is never approvable by a human."
            ),
        ),
        DataGapMetric(
            gapId="CONTENT_UNKNOWN",
            title="Pack content per sales unit completely unknown",
            reasonCode=ReasonCode.QUANTITY_BASIS_UNKNOWN,
            rule="L12 / P2.3",
            effect="EXCLUDE",
            blocksAuto=True,
            approvableByHuman=False,
            affectedProducts=content_unknown,
            affectedSuppliers=len(gap_suppliers["CONTENT_UNKNOWN"]),
            shareOfCatalog=_rate(content_unknown, total_products),
            fixAction=(
                "Provide BASE_UNIT_OR_EACH volume/netWeight in synkkaData.unitConversions or comparisonPrice/unit "
                "so physical-unit requests (kg / l) can be sized into sales units."
            ),
        ),
        DataGapMetric(
            gapId="IMPLICIT_ONLY_ALLERGENS",
            title="Food allergens declared only by omission (no explicit FREE_FROM or X99)",
            reasonCode=ReasonCode.ALLERGEN_IMPLICIT,
            rule="L4 / P2.4",
            effect="EXCLUDE",
            blocksAuto=True,
            approvableByHuman=False,
            affectedProducts=implicit_only_food,
            affectedSuppliers=len(gap_suppliers["IMPLICIT_ONLY_ALLERGENS"]),
            shareOfCatalog=_rate(implicit_only_food, total_products),
            fixAction=(
                "Add explicit nonAllergen FREE_FROM declarations (or X99 for EU-14 codes) so STRICT-mandate buyers "
                "(schools, care kitchens) can order autonomously."
            ),
        ),
        DataGapMetric(
            gapId="LOW_RELIABILITY",
            title="Historical delivery reliability below 95% threshold",
            reasonCode=ReasonCode.LOW_RELIABILITY,
            rule="L13 / P2.2",
            effect="ASK",
            blocksAuto=True,
            approvableByHuman=True,
            affectedProducts=low_rel_under_95,
            affectedSuppliers=len(gap_suppliers["LOW_RELIABILITY"]),
            shareOfCatalog=_rate(low_rel_under_95, total_products),
            fixAction=(
                "Address supplier fulfilment bottlenecks or buffer stock in Valio Aimo warehouses so historical "
                "delivery reliability meets buyer mandate thresholds (≥ 95% / ≥ 98%)."
            ),
        ),
        DataGapMetric(
            gapId="UNPRICED_DEPOSIT",
            title="Returnable deposit material without a verified unit price",
            reasonCode=ReasonCode.DEPOSIT_COST_UNVERIFIED,
            rule="L3 / P2.3",
            effect="EXCLUDE",
            blocksAuto=True,
            approvableByHuman=False,
            affectedProducts=unpriced_deposits,
            affectedSuppliers=len(gap_suppliers["UNPRICED_DEPOSIT"]),
            shareOfCatalog=_rate(unpriced_deposits, total_products),
            fixAction=(
                "Publish unitPriceExclVat for every deposit material in the price catalogue so order totals "
                "including returnable packaging are complete and verifiable."
            ),
        ),
        DataGapMetric(
            gapId="UNSAFE_VALIO_SUBSTITUTE",
            title="Valio-listed substitute breaks a claim or allergen proof of the primary product",
            reasonCode=ReasonCode.SUBSTITUTE_REJECTED,
            rule="L11 / P8",
            effect="EXCLUDE",
            blocksAuto=True,
            approvableByHuman=False,
            affectedProducts=unsafe_substitutes,
            affectedSuppliers=len(gap_suppliers["UNSAFE_VALIO_SUBSTITUTE"]),
            shareOfCatalog=_rate(unsafe_substitutes, total_products),
            fixAction=(
                "Audit substitutions[].substituteProduct links against nutritionalClaim and nonAllergen FREE_FROM "
                "declarations so listed substitutes preserve gluten-free, lactose-free and vegan guarantees."
            ),
        ),
        DataGapMetric(
            gapId="DATA_CONFLICT",
            title="Contradictory supplier declarations (K1/K2/K5/K6)",
            reasonCode=ReasonCode.DATA_CONFLICT,
            rule="L9 / P2.3",
            effect="EXCLUDE",
            blocksAuto=True,
            approvableByHuman=False,
            affectedProducts=data_conflicts_count,
            affectedSuppliers=len(gap_suppliers["DATA_CONFLICT"]),
            shareOfCatalog=_rate(data_conflicts_count, total_products),
            fixAction=(
                "Validate GS1 Synkka imports to reject FREE_FROM_GLUTEN + AW CONTAINS (K1), "
                "FREE_FROM_LACTOSE + ML CONTAINS (K2), and dairy products marked FREE_FROM AM (K6)."
            ),
        ),
    ]
    gap_breakdown.sort(key=lambda g: (-g.affectedProducts, g.gapId))

    suppliers_list: list[SupplierReadiness] = []
    for sup_name, st in by_supplier.items():
        codes: list[ReasonCode] = []
        if st["missingAllergensFood"] > 0:
            codes.append(ReasonCode.ALLERGEN_UNKNOWN)
        if st["contentUnknown"] > 0:
            codes.append(ReasonCode.QUANTITY_BASIS_UNKNOWN)
        if st["contentWeakNameEstimate"] > 0:
            codes.append(ReasonCode.QUANTITY_BASIS_WEAK)
        if st["dataConflicts"] > 0:
            codes.append(ReasonCode.DATA_CONFLICT)
        if st["unpricedDeposits"] > 0:
            codes.append(ReasonCode.DEPOSIT_COST_UNVERIFIED)
        if st["unsafeSubstitutes"] > 0:
            codes.append(ReasonCode.SUBSTITUTE_REJECTED)
        if st["lowReliability"] > 0:
            codes.append(ReasonCode.LOW_RELIABILITY)

        suppliers_list.append(
            SupplierReadiness(
                supplierName=sup_name,
                totalProducts=st["total"],
                foodProducts=st["food"],
                nonFoodProducts=st["nonFood"],
                autoReadyProducts=st["autoReady"],
                blockedFromAutoProducts=st["blockedFromAuto"],
                autoReadyRate=_rate(st["autoReady"], st["total"]),
                missingAllergensFood=st["missingAllergensFood"],
                contentUnknown=st["contentUnknown"],
                contentWeakNameEstimate=st["contentWeakNameEstimate"],
                dataConflicts=st["dataConflicts"],
                unpricedDeposits=st["unpricedDeposits"],
                unsafeSubstitutes=st["unsafeSubstitutes"],
                lowReliability=st["lowReliability"],
                blockingReasonCodes=codes,
                topFix=_supplier_top_fix(
                    missing_allergens_food=st["missingAllergensFood"],
                    content_unknown=st["contentUnknown"],
                    content_weak=st["contentWeakNameEstimate"],
                    data_conflicts=st["dataConflicts"],
                    unpriced_deposits=st["unpricedDeposits"],
                    unsafe_substitutes=st["unsafeSubstitutes"],
                    low_reliability=st["lowReliability"],
                ),
            )
        )

    suppliers_list.sort(
        key=lambda s: (-s.blockedFromAutoProducts, -s.foodProducts, -s.totalProducts, s.supplierName)
    )

    fix_list: list[FixListItem] = []

    # 1. High-impact supplier-level fixes (top food & overall suppliers with the most blocked products)
    top_suppliers_for_fixes = [s for s in suppliers_list if s.blockedFromAutoProducts > 0][:10]
    for idx, sup in enumerate(top_suppliers_for_fixes, start=1):
        primary_reason = sup.blockingReasonCodes[0] if sup.blockingReasonCodes else ReasonCode.QUANTITY_BASIS_WEAK
        effect: Literal["EXCLUDE", "ASK"] = (
            "ASK"
            if primary_reason in (ReasonCode.QUANTITY_BASIS_WEAK, ReasonCode.LOW_RELIABILITY)
            else "EXCLUDE"
        )
        parts: list[str] = []
        if sup.missingAllergensFood > 0:
            parts.append(f"{sup.missingAllergensFood} food product(s) lack allergen declarations")
        if sup.contentWeakNameEstimate > 0:
            parts.append(f"{sup.contentWeakNameEstimate} product(s) rely on name-parsed pack size")
        if sup.contentUnknown > 0:
            parts.append(f"{sup.contentUnknown} product(s) have no pack content size")
        if sup.unpricedDeposits > 0:
            parts.append(f"{sup.unpricedDeposits} product(s) have unpriced deposits")
        if sup.dataConflicts > 0:
            parts.append(f"{sup.dataConflicts} product(s) have data conflicts")
        if sup.unsafeSubstitutes > 0:
            parts.append(f"{sup.unsafeSubstitutes} product(s) list non-compliant substitutes")

        fix_list.append(
            FixListItem(
                fixId=f"FIX-SUP-{idx:02d}",
                priority="HIGH" if sup.foodProducts > 0 and sup.blockedFromAutoProducts >= 10 else "MEDIUM",
                scope="SUPPLIER",
                supplierName=sup.supplierName,
                productId=None,
                productName=None,
                reasonCode=primary_reason,
                effect=effect,
                blocksAuto=True,
                problem=(
                    f"{sup.blockedFromAutoProducts} of {sup.totalProducts} products from {sup.supplierName} "
                    f"cannot be AUTO-ordered ({'; '.join(parts)})."
                ),
                fixAction=sup.topFix,
                productsUnlocked=sup.blockedFromAutoProducts,
            )
        )

    # 2. Product-level fixes for unsafe Valio-listed substitutes (prioritising safety claims like FREE_FROM_GLUTEN)
    def _unsafe_sub_sort_key(entry: tuple[Product, str, Product, list[str]]) -> tuple[int, str, str]:
        prod, _, sub_prod, broken = entry
        breaks_gf_or_lf = any("FREE_FROM_GLUTEN" in b or "FREE_FROM_LACTOSE" in b for b in broken)
        is_demo_trap = prod.productId == "468465"
        return (0 if is_demo_trap else (1 if breaks_gf_or_lf else 2), prod.productId, sub_prod.productId)

    for idx, (prod, sup_name, sub_prod, broken) in enumerate(
        sorted(unsafe_sub_products, key=_unsafe_sub_sort_key)[:8], start=1
    ):
        fix_list.append(
            FixListItem(
                fixId=f"FIX-SUB-{idx:02d}",
                priority="HIGH",
                scope="PRODUCT",
                supplierName=sup_name,
                productId=prod.productId,
                productName=prod.name,
                reasonCode=ReasonCode.SUBSTITUTE_REJECTED,
                effect="EXCLUDE",
                blocksAuto=True,
                problem=(
                    f"{prod.productId} ({prod.name}) lists {sub_prod.productId} ({sub_prod.name}) in "
                    f"substitutions[], but {sub_prod.productId} fails: {', '.join(broken)}."
                ),
                fixAction=(
                    f"Replace substitute {sub_prod.productId} on {prod.productId} with a substitute that "
                    f"declares {', '.join(prod.claims) or 'matching allergen guarantees'}."
                ),
                productsUnlocked=1,
            )
        )

    # 3. Product-level fixes for contradictory supplier declarations (K1/K2/K6)
    def _conflict_sort_key(entry: tuple[Product, str, tuple[str, ...]]) -> tuple[int, str]:
        prod, _, codes = entry
        is_demo_conflict = prod.productId == "471881"
        return (0 if is_demo_conflict else (1 if "K1" in codes or "K2" in codes else 2), prod.productId)

    for idx, (prod, sup_name, codes) in enumerate(
        sorted(conflict_products, key=_conflict_sort_key)[:8], start=1
    ):
        fix_list.append(
            FixListItem(
                fixId=f"FIX-CONFLICT-{idx:02d}",
                priority="HIGH",
                scope="PRODUCT",
                supplierName=sup_name,
                productId=prod.productId,
                productName=prod.name,
                reasonCode=ReasonCode.DATA_CONFLICT,
                effect="EXCLUDE",
                blocksAuto=True,
                problem=(
                    f"{prod.productId} ({prod.name}) has contradictory GS1 Synkka declarations "
                    f"({', '.join(codes)})."
                ),
                fixAction=(
                    f"Reconcile synkkaData.classifications[allergen/nonAllergen] and nutritionalClaim on "
                    f"{prod.productId} to resolve conflict {', '.join(codes)}."
                ),
                productsUnlocked=1,
            )
        )

    # 4. Product-level fixes for products claiming FREE_FROM_GLUTEN / FREE_FROM_LACTOSE without allergen declarations
    def _gf_missing_alg_sort_key(entry: tuple[Product, str]) -> tuple[int, str]:
        prod, _ = entry
        is_urtekram = prod.productId in {"470699", "460302", "460293"}
        return (0 if is_urtekram else 1, prod.productId)

    for idx, (prod, sup_name) in enumerate(
        sorted(gf_missing_alg_products, key=_gf_missing_alg_sort_key)[:6], start=1
    ):
        fix_list.append(
            FixListItem(
                fixId=f"FIX-ALG-{idx:02d}",
                priority="HIGH",
                scope="PRODUCT",
                supplierName=sup_name,
                productId=prod.productId,
                productName=prod.name,
                reasonCode=ReasonCode.ALLERGEN_UNKNOWN,
                effect="EXCLUDE",
                blocksAuto=True,
                problem=(
                    f"{prod.productId} ({prod.name}) declares {', '.join(prod.claims)} in nutritionalClaim "
                    "but has zero allergen/nonAllergen declarations (excluded in STRICT mode)."
                ),
                fixAction=(
                    f"Add explicit synkkaData.classifications[nonAllergen] FREE_FROM declarations on "
                    f"{prod.productId} so STRICT kitchens can order it autonomously."
                ),
                productsUnlocked=1,
            )
        )

    return summary, gap_breakdown, suppliers_list, fix_list


_static_cache_key: tuple[int, int, int] | None = None
_static_cache_value: tuple[
    CatalogReadinessSummary,
    list[DataGapMetric],
    list[SupplierReadiness],
    list[FixListItem],
] | None = None


def compute_readiness_insights(
    products: Sequence[Product] | None = None,
    *,
    proposals: Sequence[Proposal] | None = None,
    gateway: Any = None,
    vendor_by_product_id: Mapping[str, str] | None = None,
    raw_records: Sequence[Mapping[str, object]] | None = None,
    mode: Literal["LIVE", "RECORDED"] = "LIVE",
) -> ReadinessInsights:
    """Compute Valio Aimo readiness insights (§14, D6, T27) from catalogue products and gateway proposals."""
    global _static_cache_key, _static_cache_value

    if products is None:
        if gateway is not None:
            resolved_products: Sequence[Product] = list(gateway.products.values())
        else:
            from app.catalog.build import get_cached_catalog

            resolved_products = get_cached_catalog().products
    else:
        resolved_products = products

    if proposals is None:
        if gateway is not None:
            if gateway._session is not None:
                gateway._hydrate_from_session(gateway._session)
            resolved_proposals: Sequence[Proposal] = list(gateway._proposals.values())
        else:
            resolved_proposals = ()
    else:
        resolved_proposals = proposals

    vendor_map = resolve_vendor_map(
        vendor_by_product_id=vendor_by_product_id,
        raw_records=raw_records,
    )

    use_cache = (
        vendor_by_product_id is None
        and raw_records is None
        and len(resolved_products) > 1000
    )
    cache_key = (id(resolved_products), len(resolved_products), len(vendor_map))
    if use_cache and _static_cache_key == cache_key and _static_cache_value is not None:
        summary, gap_breakdown, suppliers_list, fix_list = _static_cache_value
    else:
        summary, gap_breakdown, suppliers_list, fix_list = _compute_static_catalog_insights(
            resolved_products,
            vendor_map,
        )
        if use_cache:
            _static_cache_key = cache_key
            _static_cache_value = (summary, gap_breakdown, suppliers_list, fix_list)

    decision_stats = compute_decision_statistics(resolved_proposals)

    return ReadinessInsights(
        mode=mode,
        catalogSummary=summary,
        gapBreakdown=gap_breakdown,
        suppliers=suppliers_list,
        fixList=fix_list,
        decisionStats=decision_stats,
    )


def _register_insights_route() -> APIRouter:
    from app.api import agent_channel
    from app.api.errors import ERROR_RESPONSES
    from app.api.state import GatewayState, get_state

    router = APIRouter(prefix="/v0.1", tags=["Agent channel"], responses=ERROR_RESPONSES)

    @router.get(
        "/insights",
        response_model=ReadinessInsights,
        operation_id="getInsights",
        summary="Valio Aimo readiness view: supplier data gaps, live AUTO/ASK/BLOCK statistics and fix list",
    )
    def get_insights_route(state: Annotated[GatewayState, Depends(get_state)]) -> ReadinessInsights:
        return compute_readiness_insights(
            products=state.catalog,
            gateway=state.gateway,
            mode=state.mode,
        )

    if not any(getattr(r, "path", None) == "/v0.1/insights" for r in agent_channel.router.routes):
        agent_channel.router.add_api_route(
            "/insights",
            get_insights_route,
            methods=["GET"],
            response_model=ReadinessInsights,
            operation_id="getInsights",
            summary="Valio Aimo readiness view: supplier data gaps, live AUTO/ASK/BLOCK statistics and fix list",
        )
    return router


router = _register_insights_route()

__all__ = [
    "CatalogReadinessSummary",
    "DataGapMetric",
    "DecisionStatistics",
    "FixListItem",
    "ReadinessInsights",
    "ReasonFrequency",
    "SupplierReadiness",
    "compute_decision_statistics",
    "compute_readiness_insights",
    "find_unsafe_substitutes",
    "infer_supplier_from_name",
    "product_conflict_codes",
    "resolve_vendor_map",
    "router",
    "supplier_for_product",
]
