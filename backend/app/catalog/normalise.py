"""Normalise Valio Aimo product records and CSV values into trust-labelled Product contracts."""

import re
from collections.abc import Collection, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.catalog.categories import is_food_category
from app.contracts.common import RequestUnit
from app.contracts.models import (
    AllergenDeclaration,
    ContentPerSalesUnit,
    Deposit,
    Product,
    UnitConversion,
)
from app.contracts.reasons import ReasonCode
from app.contracts.trust import Conflict, TrustLabel
from app.trust.labels import (
    GLUTEN_CEREAL_SUBCODES,
    NON_OAT_GLUTEN_CEREAL_SUBCODES,
    SOURCE_ALLERGEN,
    SOURCE_DERIVED_NAME,
    SOURCE_DERIVED_PRICE,
    SOURCE_NON_ALLERGEN,
    SOURCE_SALES_UNIT,
    SOURCE_UNIT_CONVERSIONS,
    SOURCE_VARIABLE_UNIT,
    TREE_NUT_SUBCODES,
    allergen_name,
    claim_label,
    co2_label,
    detect_k1,
    detect_k2,
    detect_k5,
    is_eu14_allergen,
    origin_label,
    packaging_label,
    reliability_label,
    valio_substitute_label,
)

ADJ_MULT_QTY_RE = re.compile(
    r"(?:^|\b)(\d+)\s*[xX*]\s*(?:ca\.?\s*|app\.?\s*|appr\.?\s*|n\.?\s*)?(\d+(?:[.,]\d+)?)\s*(kg|g|l|dl|cl|ml)\b",
    re.IGNORECASE,
)
PREFIX_MULT_RE = re.compile(r"^\s*(\d+)\s*[xX]\b")
QTY_RE = re.compile(r"(?<!/)(\b\d+(?:[.,]\d+)?)\s*(kg|g|l|dl|cl|ml)\b", re.IGNORECASE)

STORAGE_TEMPERATURE_CODES: frozenset[str] = frozenset({
    "warmTemperature", "coolTemperature", "freezeTemperature",
})

_ALLERGEN_STATUS_PRECEDENCE: dict[str, int] = {
    "FREE_FROM": 1,
    "UNDECLARED": 2,
    "MAY_CONTAIN": 3,
    "CONTAINS": 4,
}


def clean_decimal(value: Decimal | int | float | str) -> Decimal:
    """Normalise a Decimal without scientific notation so JSON string serialisation is exact."""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    if dec == dec.to_integral():
        return dec.quantize(Decimal(1))
    norm = dec.normalize()
    text = format(norm, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return Decimal(text)


def extract_localized_texts(items: list[dict] | None) -> dict[str, str]:
    if not items:
        return {}
    result: dict[str, str] = {}
    for entry in items:
        lang = (entry.get("language") or "").strip().lower()
        val = (entry.get("value") or "").strip()
        if lang and val and lang not in result:
            result[lang] = val
    return result


def extract_ingredients(synkka: dict) -> dict[str, str]:
    texts = extract_localized_texts(synkka.get("keyIngredients"))
    structured = synkka.get("ingredients") or []
    if structured and len(texts) < 3:
        ordered = sorted(structured, key=lambda item: item.get("sequence", 0))
        by_lang: dict[str, list[str]] = {}
        for item in ordered:
            for name_obj in item.get("names") or []:
                lang = (name_obj.get("language") or "").strip().lower()
                val = (name_obj.get("value") or "").strip()
                if lang and val and lang not in texts:
                    by_lang.setdefault(lang, []).append(val)
        for lang, parts in by_lang.items():
            if lang not in texts and parts:
                texts[lang] = ", ".join(parts)
    return texts


def _convert_physical_unit(raw_value: Decimal | int | float | str, raw_unit: str) -> tuple[Decimal, RequestUnit] | None:
    dec = Decimal(str(raw_value).replace(",", "."))
    if dec <= 0:
        return None
    u = raw_unit.strip().upper()
    if u in ("LTR", "L"):
        return clean_decimal(dec), "l"
    if u in ("DL", "DLT"):
        return clean_decimal(dec / Decimal(10)), "l"
    if u in ("CL", "CLT"):
        return clean_decimal(dec / Decimal(100)), "l"
    if u in ("ML", "MLT"):
        return clean_decimal(dec / Decimal(1000)), "l"
    if u in ("KGM", "KG"):
        return clean_decimal(dec), "kg"
    if u in ("GRM", "G"):
        return clean_decimal(dec / Decimal(1000)), "kg"
    if u in ("H87", "PTN", "PR", "PCS", "ST"):
        return clean_decimal(dec), "pcs"
    return None


def resolve_method_1_content(
    synkka: dict,
    sales_unit: str,
    size_in_base_units: Decimal | None,
) -> ContentPerSalesUnit | None:
    """Method 1 (§5.2): BASE_UNIT_OR_EACH volume or netWeight × units[salesUnit].sizeInBaseUnits."""
    if size_in_base_units is None or size_in_base_units <= 0:
        return None
    base = next(
        (
            item
            for item in (synkka.get("unitConversions") or [])
            if item.get("synkkaPackagingType") == "BASE_UNIT_OR_EACH"
        ),
        None,
    )
    if not base:
        return None
    volume = base.get("volume") or {}
    net_weight = base.get("netWeight") or {}
    vol_unit = (volume.get("unit") or "").strip().upper()
    vol_val = volume.get("value")
    wt_unit = (net_weight.get("unit") or "").strip().upper()
    wt_val = net_weight.get("value")

    if vol_unit in ("LTR", "MLT", "CLT") and vol_val is not None and float(vol_val) > 0:
        conv = _convert_physical_unit(vol_val, vol_unit)
        if conv:
            return ContentPerSalesUnit(
                value=clean_decimal(size_in_base_units * conv[0]),
                unit=conv[1],
                level="DECLARED",
                method=f"unitConversions.BASE_UNIT_OR_EACH.volume x units.{sales_unit}.sizeInBaseUnits",
            )
    if wt_unit in ("KGM", "GRM") and wt_val is not None and float(wt_val) > 0:
        conv = _convert_physical_unit(wt_val, wt_unit)
        if conv:
            return ContentPerSalesUnit(
                value=clean_decimal(size_in_base_units * conv[0]),
                unit=conv[1],
                level="DECLARED",
                method=f"unitConversions.BASE_UNIT_OR_EACH.netWeight x units.{sales_unit}.sizeInBaseUnits",
            )
    if vol_unit in ("KGM", "GRM", "H87", "PTN", "PR") and vol_val is not None and float(vol_val) > 0:
        conv = _convert_physical_unit(vol_val, vol_unit)
        if conv:
            return ContentPerSalesUnit(
                value=clean_decimal(size_in_base_units * conv[0]),
                unit=conv[1],
                level="DECLARED",
                method=f"unitConversions.BASE_UNIT_OR_EACH.volume x units.{sales_unit}.sizeInBaseUnits",
            )
    return None


def resolve_method_2_content(
    catalog_price: Decimal | float | str | None,
    comparison_price: Decimal | float | str | None,
    comparison_unit: str | None,
) -> ContentPerSalesUnit | None:
    """Method 2 (§5.2): catalogPrice.value ÷ comparisonPrice.value when comparisonUnit is L or KG."""
    if catalog_price is None or comparison_price is None or not comparison_unit:
        return None
    cat_dec = Decimal(str(catalog_price))
    cmp_dec = Decimal(str(comparison_price))
    if cat_dec <= 0 or cmp_dec <= 0:
        return None
    unit_norm = comparison_unit.strip().upper()
    if unit_norm in ("L", "LTR"):
        target_unit: RequestUnit = "l"
    elif unit_norm in ("KG", "KGM"):
        target_unit = "kg"
    else:
        return None
    ratio = (cat_dec / cmp_dec).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if ratio <= 0:
        return None
    return ContentPerSalesUnit(
        value=clean_decimal(ratio),
        unit=target_unit,
        level="ESTIMATE",
        method="catalogPrice.value / comparisonPrice.value",
    )


def parse_content_from_name(
    name: str,
    size_in_base_units: Decimal = Decimal(1),
) -> ContentPerSalesUnit | None:
    """Method 3 (§5.2): parse litres or kg from the product name ('12x … 400g', '1 l', '2,5 dl', '10 L')."""
    if not name:
        return None
    adj = ADJ_MULT_QTY_RE.search(name)
    if adj:
        mult = Decimal(adj.group(1))
        conv = _convert_physical_unit(adj.group(2), adj.group(3))
        if conv and mult > 0:
            return ContentPerSalesUnit(
                value=clean_decimal(mult * conv[0]),
                unit=conv[1],
                level="ESTIMATE",
                method="name",
            )
    matches = list(QTY_RE.finditer(name))
    if not matches:
        return None
    last = matches[-1]
    conv = _convert_physical_unit(last.group(1), last.group(2))
    if not conv:
        return None
    prefix = PREFIX_MULT_RE.search(name)
    mult = Decimal(prefix.group(1)) if prefix else (size_in_base_units if size_in_base_units > 0 else Decimal(1))
    return ContentPerSalesUnit(
        value=clean_decimal(mult * conv[0]),
        unit=conv[1],
        level="ESTIMATE",
        method="name",
    )


def _content_trust_label(
    content: ContentPerSalesUnit | None,
    sales_unit: str,
    conflict: Conflict | None = None,
) -> TrustLabel:
    if content is None:
        return TrustLabel(
            fact="content.perSalesUnit",
            value=None,
            display="Unknown",
            level="UNKNOWN",
            source=SOURCE_UNIT_CONVERSIONS,
            method=None,
            conflict=conflict,
        )
    if content.method == "salesUnit.KG":
        source = SOURCE_SALES_UNIT
    elif content.method == "variableUnit":
        source = SOURCE_VARIABLE_UNIT
    elif content.method == "catalogPrice.value / comparisonPrice.value":
        source = SOURCE_DERIVED_PRICE
    elif content.method == "name":
        source = SOURCE_DERIVED_NAME
    else:
        source = SOURCE_UNIT_CONVERSIONS
    val_str = format(content.value, "f")
    return TrustLabel(
        fact="content.perSalesUnit",
        value=float(content.value) if "." in val_str else int(content.value),
        display=f"{val_str} {content.unit} / {sales_unit}",
        level=content.level,
        source=source,
        method=content.method,
        conflict=conflict,
    )


def apply_price_ratio_content(
    product: Product,
    catalog_price: Decimal | float | str | None,
    comparison_price: Decimal | float | str | None,
    comparison_unit: str | None,
) -> Product:
    """Hook for Method 2 (§5.2): apply price-ratio content estimate and detect K5 against Method 1."""
    m2 = resolve_method_2_content(catalog_price, comparison_price, comparison_unit)
    if m2 is None:
        return product
    current = product.contentPerSalesUnit
    k5_conflict: Conflict | None = None
    chosen = current
    if current is not None and current.level == "DECLARED":
        if current.method and current.method.startswith("unitConversions."):
            k5_conflict = detect_k5(current.value, current.unit, m2.value, m2.unit)
    else:
        chosen = m2

    new_content_label = _content_trust_label(chosen, product.salesUnit, conflict=k5_conflict)
    updated_facts = [
        new_content_label if f.fact == "content.perSalesUnit" else f
        for f in product.facts
    ]
    if not any(f.fact == "content.perSalesUnit" for f in updated_facts):
        updated_facts.insert(0, new_content_label)
    return product.model_copy(
        update={
            "contentPerSalesUnit": chosen,
            "facts": updated_facts,
        }
    )


def _allergen_evidence(
    code: str,
    status: Literal["CONTAINS", "MAY_CONTAIN", "FREE_FROM", "NOT_LISTED", "UNDECLARED", "UNKNOWN"],
    source_group: Literal["allergen", "nonAllergen"],
    method: str | None = None,
    conflict: Conflict | None = None,
) -> TrustLabel:
    source = SOURCE_ALLERGEN if source_group == "allergen" else SOURCE_NON_ALLERGEN
    label_name = allergen_name(code)
    if code == "X99" and status == "FREE_FROM":
        display = "No declaration-obligatory allergens (EU-14)"
        level = "DECLARED"
        val: str | None = "FREE_FROM"
    elif status == "CONTAINS":
        display = f"Contains {label_name}"
        level = "DECLARED"
        val = "CONTAINS"
    elif status == "MAY_CONTAIN":
        display = f"May contain {label_name}"
        level = "DECLARED"
        val = "MAY_CONTAIN"
    elif status == "FREE_FROM":
        display = f"Free from {label_name}" if method != "X99" else f"Free from {label_name} (X99)"
        level = "DECLARED"
        val = "FREE_FROM"
    elif status == "NOT_LISTED":
        display = f"Not listed ({label_name})"
        level = "DECLARED"
        val = "NOT_LISTED"
    elif status == "UNDECLARED":
        display = "No allergen data from supplier"
        level = "UNKNOWN"
        val = "UNDECLARED"
    else:
        display = "Unknown" if method == "X99" else "No allergen data from supplier"
        level = "UNKNOWN"
        val = None
    return TrustLabel(
        fact=f"allergen.{code}",
        value=val,
        display=display,
        level=level,
        source=source,
        method=method,
        conflict=conflict,
    )


def normalise_allergens(
    synkka: dict,
    claims: Collection[str],
) -> tuple[bool, list[AllergenDeclaration], list[TrustLabel]]:
    """Normalise allergen and nonAllergen groups with K1/K2 conflicts and X99 (Patch P4)."""
    groups = {g.get("name"): g.get("values") or [] for g in (synkka.get("classifications") or [])}
    alg_items = groups.get("allergen") or []
    non_alg_items = groups.get("nonAllergen") or []

    alg_units: dict[str, str] = {}
    for item in alg_items:
        code = (item.get("id") or "").strip().upper()
        unit = (item.get("unit") or "CONTAINS").strip().upper()
        if not code:
            continue
        if code == "X99" and unit != "UNDECLARED":
            unit = "FREE_FROM"
        prev = alg_units.get(code)
        if prev is None or _ALLERGEN_STATUS_PRECEDENCE.get(unit, 0) > _ALLERGEN_STATUS_PRECEDENCE.get(prev, 0):
            alg_units[code] = unit

    non_alg_units: dict[str, str] = {}
    for item in non_alg_items:
        code = (item.get("id") or "").strip().upper()
        unit = (item.get("unit") or "FREE_FROM").strip().upper()
        if not code:
            continue
        if unit == "FREE_FROM":
            non_alg_units[code] = "FREE_FROM"

    k1 = detect_k1(alg_units, claims)
    k2 = detect_k2(alg_units, claims)

    has_declaration = bool(
        any(unit != "UNDECLARED" for unit in alg_units.values()) or bool(non_alg_units)
    )

    combined: dict[str, tuple[Literal["CONTAINS", "MAY_CONTAIN", "FREE_FROM", "UNDECLARED"], Literal["allergen", "nonAllergen"]]] = {}
    for code, unit in non_alg_units.items():
        combined[code] = ("FREE_FROM", "nonAllergen")
    for code, unit in alg_units.items():
        if unit in ("CONTAINS", "MAY_CONTAIN", "UNDECLARED"):
            combined[code] = (unit, "allergen")
        elif unit == "FREE_FROM" and code not in combined:
            combined[code] = ("FREE_FROM", "allergen")

    declarations: list[AllergenDeclaration] = []
    allergen_facts: list[TrustLabel] = []

    for code in sorted(combined):
        status, grp = combined[code]
        conflict = k1 if (code == "AW" and status == "CONTAINS") else (k2 if (code == "ML" and status == "CONTAINS") else None)
        method = "X99" if code == "X99" else None
        ev = _allergen_evidence(code, status, grp, method=method, conflict=conflict)
        declarations.append(AllergenDeclaration(code=code, status=status, evidence=ev))
        if grp == "allergen" or code == "X99" or conflict is not None:
            allergen_facts.append(ev)

    if not has_declaration and not allergen_facts:
        allergen_facts.append(
            TrustLabel(
                fact="allergen",
                value=None,
                display="No allergen data from supplier",
                level="UNKNOWN",
                source=SOURCE_ALLERGEN,
                method=None,
                conflict=None,
            )
        )

    return has_declaration, declarations, allergen_facts


def allergen_declaration(product: Product, code: str) -> AllergenDeclaration:
    """Resolve the AllergenDeclaration for any GS1 allergen code on a normalised Product (§5.2, P4)."""
    norm = code.strip().upper()
    by_code = {a.code.upper(): a for a in product.allergens}
    has_x99 = "X99" in by_code and by_code["X99"].status == "FREE_FROM"

    if norm in by_code and norm != "X99":
        direct = by_code[norm]
        # Patch P4: X99 proves nothing for non-EU-14 codes, and lactose-free is proven only by FREE_FROM_LACTOSE.
        if has_x99 and not is_eu14_allergen(norm) and direct.status == "FREE_FROM":
            return AllergenDeclaration(
                code=norm,
                status="UNKNOWN",
                evidence=_allergen_evidence(norm, "UNKNOWN", "allergen", method="X99"),
            )
        return direct

    # Group hierarchy for AN (tree nuts) and AW (cereals containing gluten).
    if norm == "AN":
        sub_statuses = [by_code[sub].status for sub in TREE_NUT_SUBCODES if sub in by_code]
        if "CONTAINS" in sub_statuses:
            return AllergenDeclaration(
                code="AN",
                status="CONTAINS",
                evidence=_allergen_evidence("AN", "CONTAINS", "allergen", method="subcode:AN"),
            )
        if "MAY_CONTAIN" in sub_statuses:
            return AllergenDeclaration(
                code="AN",
                status="MAY_CONTAIN",
                evidence=_allergen_evidence("AN", "MAY_CONTAIN", "allergen", method="subcode:AN"),
            )
    elif norm in TREE_NUT_SUBCODES and "AN" in by_code:
        parent = by_code["AN"]
        if parent.status == "FREE_FROM":
            return AllergenDeclaration(
                code=norm,
                status="FREE_FROM",
                evidence=_allergen_evidence(norm, "FREE_FROM", "nonAllergen", method="group:AN"),
            )

    if norm == "AW":
        # §6.3: GO (oats) CONTAINS + FREE_FROM_GLUTEN is allowed gluten-free oats, not AW CONTAINS.
        relevant_subs = (
            NON_OAT_GLUTEN_CEREAL_SUBCODES
            if "FREE_FROM_GLUTEN" in product.claims
            else GLUTEN_CEREAL_SUBCODES
        )
        sub_statuses = [by_code[sub].status for sub in relevant_subs if sub in by_code]
        if "CONTAINS" in sub_statuses:
            return AllergenDeclaration(
                code="AW",
                status="CONTAINS",
                evidence=_allergen_evidence("AW", "CONTAINS", "allergen", method="subcode:AW"),
            )
        if "MAY_CONTAIN" in sub_statuses:
            return AllergenDeclaration(
                code="AW",
                status="MAY_CONTAIN",
                evidence=_allergen_evidence("AW", "MAY_CONTAIN", "allergen", method="subcode:AW"),
            )
    elif norm in GLUTEN_CEREAL_SUBCODES and "AW" in by_code:
        parent = by_code["AW"]
        if parent.status == "FREE_FROM":
            return AllergenDeclaration(
                code=norm,
                status="FREE_FROM",
                evidence=_allergen_evidence(norm, "FREE_FROM", "nonAllergen", method="group:AW"),
            )

    # Patch P4: X99 proves free-from ONLY for EU-14 codes and their subcodes; every other code is UNKNOWN.
    if has_x99:
        x99_src: Literal["allergen", "nonAllergen"] = (
            "allergen" if by_code["X99"].evidence.source == SOURCE_ALLERGEN else "nonAllergen"
        )
        if is_eu14_allergen(norm):
            return AllergenDeclaration(
                code=norm,
                status="FREE_FROM",
                evidence=_allergen_evidence(norm, "FREE_FROM", x99_src, method="X99"),
            )
        return AllergenDeclaration(
            code=norm,
            status="UNKNOWN",
            evidence=_allergen_evidence(norm, "UNKNOWN", x99_src, method="X99"),
        )

    if not product.hasAllergenDeclaration:
        return AllergenDeclaration(
            code=norm,
            status="UNKNOWN",
            evidence=_allergen_evidence(norm, "UNKNOWN", "allergen"),
        )

    return AllergenDeclaration(
        code=norm,
        status="NOT_LISTED",
        evidence=_allergen_evidence(norm, "NOT_LISTED", "allergen", method="implicit_not_listed"),
    )


def is_proven_free_from_allergen(
    product: Product,
    code: str,
    mode: Literal["STANDARD", "STRICT"] = "STANDARD",
) -> tuple[bool, ReasonCode | None, TrustLabel]:
    """Evaluate 'avoid allergen X' under Patch P2.4 and P4."""
    norm = code.strip().upper()
    decl = allergen_declaration(product, norm)
    if decl.evidence.conflict is not None:
        return False, ReasonCode.DATA_CONFLICT, decl.evidence
    if mode == "STRICT" and product.isFood is not False and not product.hasAllergenDeclaration:
        return False, ReasonCode.ALLERGEN_UNKNOWN, decl.evidence
    if decl.status == "CONTAINS":
        return False, ReasonCode.ALLERGEN_CONTAINS, decl.evidence
    if decl.status == "MAY_CONTAIN":
        return False, ReasonCode.ALLERGEN_MAY_CONTAIN, decl.evidence
    if decl.status in ("UNKNOWN", "UNDECLARED"):
        return False, ReasonCode.ALLERGEN_UNKNOWN, decl.evidence
    # Patch P4: lactose-free is proven only by the claim FREE_FROM_LACTOSE.
    if norm == "ML":
        if "FREE_FROM_LACTOSE" in product.claims:
            k2 = next((f.conflict for f in product.facts if f.fact == "claim.FREE_FROM_LACTOSE" and f.conflict), None)
            if k2 is not None:
                return False, ReasonCode.DATA_CONFLICT, decl.evidence
            return True, None, decl.evidence
        return False, ReasonCode.CLAIM_MISSING, decl.evidence
    if decl.status == "FREE_FROM":
        return True, None, decl.evidence
    if decl.status == "NOT_LISTED":
        if mode == "STANDARD":
            return True, None, decl.evidence
        return False, ReasonCode.ALLERGEN_IMPLICIT, decl.evidence
    return False, ReasonCode.ALLERGEN_UNKNOWN, decl.evidence


def satisfies_claim(product: Product, claim_code: str) -> tuple[bool, ReasonCode | None, TrustLabel | None]:
    """Evaluate a required nutritional claim (§6.3, P2.3, P4). LOW_LACTOSE never satisfies FREE_FROM_LACTOSE."""
    norm = claim_code.strip().upper()
    if norm not in product.claims:
        return False, ReasonCode.CLAIM_MISSING, None
    fact = next((f for f in product.facts if f.fact == f"claim.{norm}"), None)
    if fact and fact.conflict is not None:
        return False, ReasonCode.DATA_CONFLICT, fact
    # Check allergen-side K1/K2 conflicts even if queried via claim.
    if norm == "FREE_FROM_GLUTEN":
        aw = next((a for a in product.allergens if a.code == "AW"), None)
        if aw and aw.evidence.conflict is not None:
            return False, ReasonCode.DATA_CONFLICT, aw.evidence
    elif norm == "FREE_FROM_LACTOSE":
        ml = next((a for a in product.allergens if a.code == "ML"), None)
        if ml and ml.evidence.conflict is not None:
            return False, ReasonCode.DATA_CONFLICT, ml.evidence
    return True, None, fact


def product_fact(product: Product, fact_name: str) -> TrustLabel:
    """Look up a TrustLabel on a Product by fact name, returning an UNKNOWN TrustLabel when absent (§5.2)."""
    for item in product.facts:
        if item.fact == fact_name:
            return item
    if fact_name.startswith("allergen."):
        return allergen_declaration(product, fact_name.split(".", 1)[1]).evidence
    if fact_name == "origin":
        return origin_label(product.countryOfOrigin)
    if fact_name == "reliability":
        return reliability_label(None)
    if fact_name == "co2":
        return co2_label(None)
    if fact_name == "content.perSalesUnit":
        return _content_trust_label(product.contentPerSalesUnit, product.salesUnit)
    return TrustLabel(
        fact=fact_name,
        value=None,
        display="Unknown",
        level="UNKNOWN",
        source=SOURCE_ALLERGEN,
        method=None,
        conflict=None,
    )


def normalise_product(
    record: Mapping[str, object],
    *,
    reliability: float | Decimal | None = None,
    co2: float | Decimal | None = None,
    catalog_price: Decimal | float | str | None = None,
    comparison_price: Decimal | float | str | None = None,
    comparison_unit: str | None = None,
) -> Product:
    """Normalise a raw Valio product record into a v0.1 Product with TrustLabels (§5.2, P4)."""
    raw_id = record.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError("Original Valio product IDs must be non-empty strings.")
    product_id = raw_id.strip()
    sales_unit = str(record["salesUnit"]).strip()
    base_unit = str(record.get("baseUnit") or sales_unit).strip()
    category = str(record.get("category") or "unknown").strip()
    allowed_lot_size = clean_decimal(record.get("allowedLotSize", 1))

    synkka = dict(record.get("synkkaData") or {})
    names = extract_localized_texts(synkka.get("names"))
    name = (
        names.get("en")
        or names.get("fi")
        or names.get("sv")
        or next(iter(names.values()), f"Product {product_id} (name unknown)")
    )
    ingredients = extract_ingredients(synkka)
    is_food = is_food_category(category, dict(record))
    variable_weight = bool(synkka.get("variableUnit", False))

    units: list[UnitConversion] = []
    size_in_base_units: Decimal | None = None
    for u in record.get("units") or []:
        uid = (u.get("unitId") or "").strip()
        raw_sbu = u.get("sizeInBaseUnits")
        if uid and raw_sbu is not None and float(raw_sbu) > 0:
            sbu_dec = clean_decimal(raw_sbu)
            units.append(UnitConversion(unitId=uid, sizeInBaseUnits=sbu_dec))
            if uid == sales_unit and size_in_base_units is None:
                size_in_base_units = sbu_dec

    groups = {g.get("name"): g.get("values") or [] for g in (synkka.get("classifications") or [])}
    claims = sorted({
        (c.get("id") or "").strip()
        for c in (groups.get("nutritionalClaim") or [])
        if (c.get("id") or "").strip()
    })
    labels = sorted({
        (lbl.get("id") or "").strip()
        for lbl in (groups.get("packagingMarkedLabel") or [])
        if (lbl.get("id") or "").strip() and (lbl.get("id") or "").strip() not in STORAGE_TEMPERATURE_CODES
    })

    has_allergen_decl, allergen_decls, allergen_facts = normalise_allergens(synkka, claims)
    alg_units_map = {a.code: a.status for a in allergen_decls}
    k1 = detect_k1(alg_units_map, claims)
    k2 = detect_k2(alg_units_map, claims)

    # Content per sales unit (§5.2):
    # 1. Sold by KG (salesUnit KG, or variableUnit) -> 1 kg per sales unit.
    # 2. Method 1: BASE_UNIT_OR_EACH × units[salesUnit].sizeInBaseUnits -> DECLARED.
    # 3. Method 2: catalogPrice.value / comparisonPrice.value -> ESTIMATE (with K5 check against Method 1).
    # 4. Method 3: parsed from product name -> ESTIMATE (weak).
    m1_content = resolve_method_1_content(synkka, sales_unit, size_in_base_units)
    m2_content = resolve_method_2_content(catalog_price, comparison_price, comparison_unit)
    k5_conflict: Conflict | None = None

    if sales_unit == "KG":
        content: ContentPerSalesUnit | None = ContentPerSalesUnit(
            value=Decimal(1),
            unit="kg",
            level="DECLARED",
            method="salesUnit.KG",
        )
    elif variable_weight:
        content = ContentPerSalesUnit(
            value=Decimal(1),
            unit="kg",
            level="DECLARED",
            method="variableUnit",
        )
    elif m1_content is not None:
        content = m1_content
        if m2_content is not None:
            k5_conflict = detect_k5(m1_content.value, m1_content.unit, m2_content.value, m2_content.unit)
    elif m2_content is not None:
        content = m2_content
    else:
        content = None
        for candidate_name in (name, names.get("fi", ""), names.get("sv", "")):
            if candidate_name:
                content = parse_content_from_name(candidate_name, size_in_base_units or Decimal(1))
                if content is not None:
                    break

    raw_origin = record.get("countryOfOrigin")
    country_of_origin = str(raw_origin).strip().lower() if isinstance(raw_origin, str) and raw_origin.strip() else None

    substitutions: list[str] = []
    for sub in record.get("substitutions") or []:
        if sub.get("product", product_id) == product_id:
            target = (sub.get("substituteProduct") or "").strip()
            if target and target not in substitutions:
                substitutions.append(target)

    deposits: list[Deposit] = []
    for dep in record.get("deposits") or []:
        mat = str(dep.get("material") or dep.get("productId") or "").strip()
        qty = dep.get("quantity")
        if mat and qty is not None and float(qty) > 0:
            deposits.append(
                Deposit(
                    productId=mat,
                    quantity=clean_decimal(qty),
                    unitPriceExclVat=None,
                )
            )

    facts: list[TrustLabel] = [
        _content_trust_label(content, sales_unit, conflict=k5_conflict),
        *allergen_facts,
    ]
    for claim_id in claims:
        c_conflict = k1 if claim_id == "FREE_FROM_GLUTEN" else (k2 if claim_id == "FREE_FROM_LACTOSE" else None)
        facts.append(claim_label(claim_id, conflict=c_conflict))
    for label_id in labels:
        facts.append(packaging_label(label_id))
    if country_of_origin is not None:
        facts.append(origin_label(country_of_origin))
    if reliability is not None:
        facts.append(reliability_label(reliability))
    if co2 is not None:
        facts.append(co2_label(co2))
    for sub_id in substitutions:
        facts.append(valio_substitute_label(sub_id))

    return Product(
        productId=product_id,
        name=name,
        names=names,
        category=category,
        isFood=is_food,
        salesUnit=sales_unit,
        baseUnit=base_unit,
        allowedLotSize=allowed_lot_size,
        units=units,
        contentPerSalesUnit=content,
        variableWeight=variable_weight,
        hasAllergenDeclaration=has_allergen_decl,
        allergens=allergen_decls,
        claims=claims,
        labels=labels,
        countryOfOrigin=country_of_origin,
        ingredients=ingredients,
        substitutions=substitutions,
        deposits=deposits,
        facts=facts,
    )
