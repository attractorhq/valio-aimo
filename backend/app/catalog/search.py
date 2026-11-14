"""Static-only product lookup (§7.3, P2.4, P4): FI/EN/SV vocabulary, category profiles, hard requirements, and excluded[]."""

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import Field

from app.catalog.build import CatalogArtifact, get_cached_catalog
from app.catalog.categories import is_food_category, matching_categories_for_type, product_type_match
from app.catalog.normalise import allergen_declaration, product_fact
from app.catalog.vocab import (
    CONCEPTS_BY_ID,
    ProductConcept,
    expand_query_tokens,
    extract_inline_requirements,
    match_concept_in_text,
    resolve_query_concepts,
)
from app.contracts import (
    Approver,
    ExcludedProduct,
    Mandate,
    Preference,
    Product,
    ReasonCode,
    RequestItem,
    Requirement,
    TrustLabel,
)
from app.contracts.common import Contract
from app.policy.requirements import (
    check_requirements,
    claim_conflicts,
    conflict_is_decisive,
    hard_requirements,
)

_WORD_RE = re.compile(r"[\wåäöÅÄÖ%-]+", re.UNICODE)
_PRODUCT_ID_IN_QUERY_RE = re.compile(r"\b(\d{6})\b")
_FAT_PCT_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_SKIMMED_RE = re.compile(r"\b(?:skimmed|fat[- ]?free|rasvaton\w*|fettfri\w*|skummjölk\w*)\b", re.IGNORECASE)
_SEMI_SKIMMED_RE = re.compile(r"\b(?:semi[- ]skimmed|low[- ]fat|kevytmaito\w*|lättmjölk\w*)\b", re.IGNORECASE)
_WHOLE_MILK_RE = re.compile(r"\b(?:whole\s+milk\w*|täysmaito\w*|helmjölk\w*)\b", re.IGNORECASE)


def parse_fat_percent(raw: str) -> Decimal | None:
    """Parse a fat percentage string (e.g. '1.5', '1,5 %', '0%') into a normalised Decimal."""
    clean = raw.strip().replace(",", ".").rstrip("%").strip()
    if not clean:
        return None
    try:
        return Decimal(clean).normalize()
    except InvalidOperation:
        return None


def product_fat_percents(product: Product) -> frozenset[Decimal]:
    """Return proven fat percentage values for a product from its facts, claims, or multilingual names."""
    text = " ".join(p for p in (product.name, *product.names.values()) if p)
    found: set[Decimal] = set()
    for m in _FAT_PCT_RE.finditer(text):
        d = parse_fat_percent(m.group(1))
        if d is not None:
            found.add(d)
    for f in product.facts:
        if f.fact in ("fat.percent", "nutrition.fat") and f.value is not None:
            d = parse_fat_percent(str(f.value))
            if d is not None:
                found.add(d)
    if found:
        return frozenset(found)
    if _SEMI_SKIMMED_RE.search(text):
        return frozenset({Decimal("1.5").normalize()})
    if _SKIMMED_RE.search(text) or "FREE_FROM_FAT" in product.claims:
        return frozenset({Decimal("0").normalize()})
    if _WHOLE_MILK_RE.search(text):
        return frozenset({Decimal("3.5").normalize()})
    return frozenset()


def requested_fat_percent(
    preferences: Sequence[Preference] = (),
    query: str = "",
) -> Decimal | None:
    """Resolve an explicit fat percentage preference from `preferences` or inline `query` text."""
    for pref in preferences:
        if pref.kind == "FAT_PERCENT":
            d = parse_fat_percent(pref.value)
            if d is not None:
                return d
    for m in _FAT_PCT_RE.finditer(query):
        d = parse_fat_percent(m.group(1))
        if d is not None:
            return d
    return None


def narrow_candidates_by_fat_preference(
    candidates: Sequence[Product],
    preferences: Sequence[Preference] = (),
    *,
    query: str = "",
    target_product_id: str | None = None,
) -> list[Product]:
    """Narrow candidates when an explicit FAT_PERCENT preference is present (T24).

    Drops candidates whose fat % is proven different (from product data or name), while keeping
    products with unknown fat % ranked after proven matches.
    """
    target_fat = requested_fat_percent(preferences, query)
    if target_fat is None:
        return list(candidates)
    proven_matches: list[Product] = []
    unknown_fat: list[Product] = []
    for prod in candidates:
        if target_product_id and prod.productId == target_product_id:
            proven_matches.append(prod)
            continue
        fats = product_fat_percents(prod)
        if not fats:
            unknown_fat.append(prod)
        elif target_fat in fats:
            proven_matches.append(prod)
    return [*proven_matches, *unknown_fat]


class SearchProductsRequest(Contract):
    query: str = Field(default="", max_length=500)
    requirements: list[Requirement] = Field(default_factory=list)
    allergenMode: Literal["STANDARD", "STRICT"] = "STANDARD"
    forbiddenAllergens: list[str] = Field(default_factory=list)
    language: Literal["en", "fi", "sv"] | None = None
    limit: int = Field(default=20, ge=1, le=100)
    itemId: str | None = None
    productId: str | None = None
    includeNonFood: bool = False


class SearchProductsResult(Contract):
    query: str
    resolvedProductType: str | None = None
    appliedRequirements: list[Requirement] = Field(default_factory=list)
    candidates: list[Product] = Field(default_factory=list)
    excluded: list[ExcludedProduct] = Field(default_factory=list)


@dataclass(frozen=True)
class _IndexedProduct:
    product: Product
    is_food: bool
    category: str
    prefix3: str
    text_lower: str
    words: frozenset[str]
    reliability: float
    unknown_count: int


class _CatalogSearchIndex:
    def __init__(self, products: Sequence[Product]) -> None:
        self.all_entries: list[_IndexedProduct] = []
        self.by_id: dict[str, _IndexedProduct] = {}
        by_cat: defaultdict[str, list[_IndexedProduct]] = defaultdict(list)
        by_p3: defaultdict[str, list[_IndexedProduct]] = defaultdict(list)

        for prod in products:
            is_food = prod.isFood if prod.isFood is not None else is_food_category(prod.category)
            parts = [prod.name, *prod.names.values()]
            text_lower = " ".join(p for p in parts if p).lower()
            words = frozenset(_WORD_RE.findall(text_lower))
            rel_val = 0.0
            unk_count = 0
            for f in prod.facts:
                if f.fact == "reliability" and isinstance(f.value, (int, float)):
                    rel_val = float(f.value)
                if f.level == "UNKNOWN":
                    unk_count += 1
            entry = _IndexedProduct(
                product=prod,
                is_food=is_food,
                category=prod.category,
                prefix3=prod.category[:3],
                text_lower=text_lower,
                words=words,
                reliability=rel_val,
                unknown_count=unk_count,
            )
            self.all_entries.append(entry)
            self.by_id[prod.productId] = entry
            by_cat[prod.category].append(entry)
            by_p3[prod.category[:3]].append(entry)

        self.by_category = {k: tuple(v) for k, v in by_cat.items()}
        self.by_prefix3 = {k: tuple(v) for k, v in by_p3.items()}

    def entries_for_prefixes(self, prefixes: Iterable[str]) -> list[_IndexedProduct]:
        seen: set[str] = set()
        result: list[_IndexedProduct] = []
        for pref in prefixes:
            bucket: Sequence[_IndexedProduct]
            if pref in self.by_category:
                bucket = self.by_category[pref]
            elif len(pref) == 3 and pref in self.by_prefix3:
                bucket = self.by_prefix3[pref]
            else:
                bucket = [e for e in self.all_entries if e.category.startswith(pref)]
            for entry in bucket:
                if entry.product.productId not in seen:
                    seen.add(entry.product.productId)
                    result.append(entry)
        return result


_INDEX_CACHE: dict[tuple[int, int, str], _CatalogSearchIndex] = {}


def _get_index(products: Sequence[Product]) -> _CatalogSearchIndex:
    if not products:
        return _CatalogSearchIndex(())
    first_id = products[0].productId
    last_id = products[-1].productId
    key = (id(products), len(products), f"{first_id}:{last_id}")
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    built = _CatalogSearchIndex(products)
    if len(_INDEX_CACHE) >= 8:
        _INDEX_CACHE.clear()
    _INDEX_CACHE[key] = built
    return built


def _coerce_requirement(req: Requirement | str) -> Requirement:
    if isinstance(req, Requirement):
        return req
    norm = req.strip().upper()
    if norm.startswith("CLAIM:"):
        return Requirement(kind="CLAIM", code=norm.split(":", 1)[1].strip())
    if norm.startswith("AVOID_ALLERGEN:") or norm.startswith("ALLERGEN:"):
        return Requirement(kind="AVOID_ALLERGEN", code=norm.split(":", 1)[1].strip())
    if norm.startswith("FREE_FROM_") or norm in ("VEGAN", "VEGETARIAN", "LOW_LACTOSE", "NO_ADDED_SUGARS", "HIGH_PROTEIN", "HIGH_FIBRE"):
        return Requirement(kind="CLAIM", code=norm)
    return Requirement(kind="AVOID_ALLERGEN", code=norm)


def _make_search_mandate(
    allergen_mode: Literal["STANDARD", "STRICT"],
    forbidden_allergens: Sequence[str],
) -> Mandate:
    return Mandate(
        customerId="search-context",
        version=1,
        organisationName="Search Context",
        segment="search",
        allergenMode=allergen_mode,
        autoApproveLimitEur=Decimal("1000.00"),
        hardLimitEur=Decimal("5000.00"),
        autoAcceptValioSubstitutes=False,
        autoAcceptSupplierStock=False,
        minReliability=Decimal("0.80"),
        enforceCutoff=True,
        forbiddenAllergens=list(forbidden_allergens),
        approver=Approver(name="Approver", role="Approver"),
    )


def evaluate_product_static_requirements(
    product: Product,
    requirements: Sequence[Requirement],
    mandate: Mandate,
    *,
    named: bool = False,
    item_id: str | None = None,
) -> tuple[list[ReasonCode], list[TrustLabel]]:
    """Evaluate a product against static hard requirements (P2.4, P4, §6.3, K1/K2 conflicts)."""
    combined_reqs = hard_requirements(requirements, mandate)
    findings = check_requirements(
        product,
        requirements,
        mandate,
        named=named,
        item_id=item_id,
    )
    reasons: list[ReasonCode] = []
    facts: list[TrustLabel] = []

    for f in findings:
        rc = ReasonCode(f.code)
        if rc not in reasons:
            reasons.append(rc)

    # Patch P4: lactose-free is proven ONLY by the claim FREE_FROM_LACTOSE (never by ML FREE_FROM, X99, or NOT_LISTED).
    for req in combined_reqs:
        if req.kind == "AVOID_ALLERGEN" and req.code.upper() == "ML":
            if "FREE_FROM_LACTOSE" not in product.claims:
                if ReasonCode.CLAIM_MISSING not in reasons and ReasonCode.ALLERGEN_CONTAINS not in reasons:
                    reasons.append(ReasonCode.CLAIM_MISSING)

    # K1 and K2 decisive data conflicts (§5.3, L9).
    conflicts = claim_conflicts(product)
    for c_code in sorted(conflicts):
        if conflict_is_decisive(c_code, combined_reqs):
            if ReasonCode.DATA_CONFLICT not in reasons:
                reasons.append(ReasonCode.DATA_CONFLICT)

    # Collect decisive TrustLabels for any exclusion reasons.
    if reasons:
        for req in combined_reqs:
            if req.kind == "CLAIM":
                cf = next((f for f in product.facts if f.fact == f"claim.{req.code}"), None)
                if cf is not None and cf not in facts:
                    facts.append(cf)
                if req.code == "FREE_FROM_GLUTEN":
                    aw = next((a.evidence for a in product.allergens if a.code == "AW"), None)
                    if aw is not None and aw not in facts:
                        facts.append(aw)
                elif req.code == "FREE_FROM_LACTOSE":
                    ml = next((a.evidence for a in product.allergens if a.code == "ML"), None)
                    if ml is not None and ml not in facts:
                        facts.append(ml)
            else:
                ev = allergen_declaration(product, req.code).evidence
                if ev not in facts:
                    facts.append(ev)
        if not product.hasAllergenDeclaration and mandate.allergenMode == "STRICT":
            unk = product_fact(product, "allergen")
            if unk not in facts:
                facts.append(unk)

    return reasons, facts


def _token_group_matches(group: tuple[str, ...], entry: _IndexedProduct) -> bool:
    for term in group:
        if " " in term:
            if term in entry.text_lower:
                return True
        elif term in entry.words or f"{term}s" in entry.words:
            return True
        elif len(term) >= 5 and term in entry.text_lower:
            return True
    return False


def search_products(
    query: str | RequestItem | SearchProductsRequest = "",
    requirements: Iterable[Requirement | str] = (),
    *,
    preferences: Sequence[Preference] = (),
    mandate: Mandate | None = None,
    allergen_mode: Literal["STANDARD", "STRICT"] | None = None,
    forbidden_allergens: Iterable[str] = (),
    language: Literal["en", "fi", "sv"] | None = None,
    limit: int = 20,
    item_id: str | None = None,
    product_id: str | None = None,
    include_non_food: bool = False,
    products: Sequence[Product] | CatalogArtifact | None = None,
) -> SearchProductsResult:
    """Run static-only lookup (§7.3) returning compliant candidates[] and excluded[] with reason codes."""
    del language  # Multilingual matching across EN/FI/SV is always active.

    if isinstance(query, SearchProductsRequest):
        req_obj = query
        raw_query = req_obj.query
        explicit_reqs = [_coerce_requirement(r) for r in (*req_obj.requirements, *requirements)]
        explicit_prefs = list(preferences)
        mode = allergen_mode or (mandate.allergenMode if mandate else req_obj.allergenMode)
        forbidden = list(dict.fromkeys([*(mandate.forbiddenAllergens if mandate else ()), *req_obj.forbiddenAllergens, *forbidden_allergens]))
        limit = req_obj.limit
        item_id = item_id or req_obj.itemId
        product_id = product_id or req_obj.productId
        include_non_food = include_non_food or req_obj.includeNonFood
    elif isinstance(query, RequestItem):
        item_obj = query
        raw_query = item_obj.what
        explicit_reqs = [_coerce_requirement(r) for r in (*item_obj.requirements, *requirements)]
        explicit_prefs = [*item_obj.preferences, *preferences]
        mode = allergen_mode or (mandate.allergenMode if mandate else "STANDARD")
        forbidden = list(dict.fromkeys([*(mandate.forbiddenAllergens if mandate else ()), *forbidden_allergens]))
        item_id = item_id or item_obj.itemId
        product_id = product_id or item_obj.productId
    else:
        raw_query = str(query or "")
        explicit_reqs = [_coerce_requirement(r) for r in requirements]
        explicit_prefs = list(preferences)
        mode = allergen_mode or (mandate.allergenMode if mandate else "STANDARD")
        forbidden = list(dict.fromkeys([*(mandate.forbiddenAllergens if mandate else ()), *forbidden_allergens]))

    effective_mandate = (
        mandate
        if (mandate is not None and mandate.allergenMode == mode and list(mandate.forbiddenAllergens) == forbidden)
        else _make_search_mandate(mode, forbidden)
    )

    if products is None:
        product_seq: Sequence[Product] = get_cached_catalog().products
    elif isinstance(products, CatalogArtifact):
        product_seq = products.products
    else:
        product_seq = products

    index = _get_index(product_seq)

    # Extract inline product ID if present in raw_query
    id_match = _PRODUCT_ID_IN_QUERY_RE.search(raw_query)
    target_product_id = (product_id or (id_match.group(1) if id_match else None))
    query_without_id = _PRODUCT_ID_IN_QUERY_RE.sub(" ", raw_query).strip() if id_match else raw_query

    cleaned_query, inline_reqs = extract_inline_requirements(query_without_id)
    combined_reqs = list(dict.fromkeys([*explicit_reqs, *inline_reqs]))
    applied_reqs = list(hard_requirements(combined_reqs, effective_mandate))

    concepts = resolve_query_concepts(cleaned_query)
    primary_concept: ProductConcept | None = concepts[0] if concepts else None
    parent_concept: ProductConcept | None = (
        CONCEPTS_BY_ID.get(primary_concept.parent_concept)
        if primary_concept and primary_concept.parent_concept
        else None
    )

    allow_non_food = include_non_food or (primary_concept is not None and not primary_concept.is_food)
    token_groups, _ = expand_query_tokens(cleaned_query)

    # Candidate pool selection:
    pool: list[_IndexedProduct] = []
    seen_pool: set[str] = set()

    def add_to_pool(entry: _IndexedProduct) -> None:
        if not allow_non_food and not entry.is_food:
            return
        if entry.product.productId not in seen_pool:
            seen_pool.add(entry.product.productId)
            pool.append(entry)

    if target_product_id and target_product_id in index.by_id:
        add_to_pool(index.by_id[target_product_id])

    if primary_concept is not None:
        active_concept = parent_concept or primary_concept
        cat_entries = index.entries_for_prefixes(active_concept.category_prefixes)
        for entry in cat_entries:
            if match_concept_in_text(primary_concept, entry.text_lower, entry.words) or (
                parent_concept is not None and match_concept_in_text(parent_concept, entry.text_lower, entry.words)
            ):
                add_to_pool(entry)
        # Fallback for synthetic categories (e.g. "dairy", "pasta", "pantry", "synthetic") or cross-category items:
        if not pool:
            for entry in index.all_entries:
                if match_concept_in_text(primary_concept, entry.text_lower, entry.words) or (
                    parent_concept is not None and match_concept_in_text(parent_concept, entry.text_lower, entry.words)
                ):
                    add_to_pool(entry)
    elif token_groups:
        cat_prefixes = matching_categories_for_type(cleaned_query)
        search_space = index.entries_for_prefixes(cat_prefixes) if cat_prefixes else index.all_entries
        for entry in search_space:
            if all(_token_group_matches(grp, entry) for grp in token_groups):
                add_to_pool(entry)
        if not pool and search_space is not index.all_entries:
            for entry in index.all_entries:
                if all(_token_group_matches(grp, entry) for grp in token_groups):
                    add_to_pool(entry)
        if not pool and len(token_groups) > 1:
            for entry in index.all_entries:
                if any(_token_group_matches(grp, entry) for grp in token_groups):
                    add_to_pool(entry)
    else:
        for entry in index.all_entries:
            add_to_pool(entry)

    compliant_entries: list[_IndexedProduct] = []
    excluded_list: list[ExcludedProduct] = []
    excluded_by_id: dict[str, ExcludedProduct] = {}

    for entry in pool:
        prod = entry.product
        named = target_product_id is not None and prod.productId == target_product_id
        reasons, facts = evaluate_product_static_requirements(
            prod,
            combined_reqs,
            effective_mandate,
            named=named,
            item_id=item_id,
        )
        if reasons:
            ex = ExcludedProduct(
                productId=prod.productId,
                name=prod.name,
                itemId=item_id,
                reasons=reasons,
                facts=facts,
            )
            excluded_list.append(ex)
            excluded_by_id[prod.productId] = ex
        else:
            compliant_entries.append(entry)

    q_lower = cleaned_query.lower()

    requirement_codes = tuple(r.code for r in applied_reqs)

    def rank_key(entry: _IndexedProduct) -> tuple[int, int, int, int, int, int, float, int, str]:
        prod = entry.product
        is_exact_id = 0 if (target_product_id and prod.productId == target_product_id) else 1
        is_primary_concept = (
            0
            if (primary_concept is None or match_concept_in_text(primary_concept, entry.text_lower, entry.words))
            else 1
        )
        # T34: the buyer's use-changing qualifiers (whipping vs cooking cream, dairy vs alternative) come
        # right after the product concept, so exact types fill the shortlist before substitutes.
        type_mismatch = 0 if product_type_match(
            cleaned_query,
            (prod.name, *prod.names.values()),
            concept_id=primary_concept.concept_id if primary_concept else None,
            requirement_codes=requirement_codes,
        ).exact else 1
        matched_tokens = sum(1 for grp in token_groups if _token_group_matches(grp, entry))
        is_specialty = 0
        if primary_concept and primary_concept.prefer_plain_over:
            for term in primary_concept.prefer_plain_over:
                if term not in q_lower and term in entry.text_lower:
                    is_specialty = 1
                    break
        cpsu = prod.contentPerSalesUnit
        if cpsu is None:
            content_rank = 2
        elif cpsu.level == "DECLARED":
            content_rank = 0
        else:
            content_rank = 1
        return (
            is_exact_id,
            is_primary_concept,
            type_mismatch,
            -matched_tokens,
            is_specialty,
            content_rank,
            -entry.reliability,
            entry.unknown_count,
            prod.productId,
        )

    compliant_entries.sort(key=rank_key)
    ordered_compliant = narrow_candidates_by_fat_preference(
        [e.product for e in compliant_entries],
        explicit_prefs,
        query=raw_query,
        target_product_id=target_product_id,
    )
    candidates = ordered_compliant[:limit]

    # Check Valio-listed substitutes on returned candidates against hard requirements (§7.8, P8 B).
    for cand in candidates:
        for sub_id in cand.substitutions:
            sub_entry = index.by_id.get(sub_id)
            if sub_entry is None:
                continue
            sub_prod = sub_entry.product
            sub_reasons, sub_facts = evaluate_product_static_requirements(
                sub_prod,
                combined_reqs,
                effective_mandate,
                named=False,
                item_id=item_id,
            )
            if sub_reasons:
                merged_reasons = list(sub_reasons)
                if ReasonCode.SUBSTITUTE_REJECTED not in merged_reasons:
                    merged_reasons.append(ReasonCode.SUBSTITUTE_REJECTED)
                existing = excluded_by_id.get(sub_id)
                updated_ex = ExcludedProduct(
                    productId=sub_prod.productId,
                    name=sub_prod.name,
                    itemId=item_id,
                    reasons=merged_reasons,
                    facts=sub_facts,
                    substituteFor=cand.productId,
                )
                if existing is not None:
                    idx = excluded_list.index(existing)
                    excluded_list[idx] = updated_ex
                else:
                    excluded_list.append(updated_ex)
                excluded_by_id[sub_id] = updated_ex

    return SearchProductsResult(
        query=raw_query,
        resolvedProductType=primary_concept.concept_id if primary_concept else None,
        appliedRequirements=applied_reqs,
        candidates=candidates,
        excluded=excluded_list,
    )


searchProducts = search_products

__all__ = [
    "SearchProductsRequest",
    "SearchProductsResult",
    "evaluate_product_static_requirements",
    "narrow_candidates_by_fat_preference",
    "parse_fat_percent",
    "product_fat_percents",
    "requested_fat_percent",
    "searchProducts",
    "search_products",
]
