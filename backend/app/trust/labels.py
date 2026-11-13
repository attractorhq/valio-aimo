"""Agent Trust Labels v0.1 helpers, GS1/P4 allergen rules, and K1–K5 conflict builders."""

from collections.abc import Collection, Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType

from app.contracts.common import TrustLevel
from app.contracts.trust import Conflict, FactValue, TrustLabel, TrustSource

EU14_MAIN_CODES: frozenset[str] = frozenset({
    "AC", "AE", "AF", "AM", "AN", "AP", "AS", "AU", "AW", "AY", "BC", "BM", "NL", "UM",
})
TREE_NUT_SUBCODES: frozenset[str] = frozenset({"SA", "SH", "SC", "SP", "SR", "ST", "SW", "SM"})
GLUTEN_CEREAL_SUBCODES: frozenset[str] = frozenset({"UW", "GB", "NR", "GO", "GS", "GK"})
NON_OAT_GLUTEN_CEREAL_SUBCODES: frozenset[str] = GLUTEN_CEREAL_SUBCODES - {"GO"}

# Patch P4: X99 proves "free from" ONLY for these 28 EU-14 codes and subcodes.
EU14_ALLERGEN_CODES: frozenset[str] = EU14_MAIN_CODES | TREE_NUT_SUBCODES | GLUTEN_CEREAL_SUBCODES

NON_EU14_ALLERGEN_CODES: frozenset[str] = frozenset({
    "ML", "NM", "NE", "NP", "NC", "CM", "BF", "PO", "NW", "NK", "BP", "PN", "SO", "UN", "ABD", "ADQ",
})

ALLERGEN_NAMES: Mapping[str, str] = MappingProxyType({
    "AC": "crustaceans",
    "AE": "eggs",
    "AF": "fish",
    "AM": "milk",
    "AN": "tree nuts",
    "AP": "peanuts",
    "AS": "sesame",
    "AU": "sulphites",
    "AW": "cereals containing gluten",
    "AY": "soy",
    "BC": "celery",
    "BM": "mustard",
    "NL": "lupin",
    "UM": "molluscs",
    "SA": "almonds",
    "SH": "hazelnuts",
    "SC": "cashews",
    "SP": "pecans",
    "SR": "brazil nuts",
    "ST": "pistachios",
    "SW": "walnuts",
    "SM": "macadamia nuts",
    "UW": "wheat",
    "GB": "barley",
    "NR": "rye",
    "GO": "oats",
    "GS": "spelt",
    "GK": "kamut",
    "ML": "lactose",
    "NM": "corn",
    "NE": "peas",
    "NP": "pulses",
    "NC": "cocoa",
    "CM": "chicken",
    "BF": "beef",
    "PO": "pork",
    "NW": "carrot",
    "NK": "coriander",
    "BP": "linalool",
    "PN": "pine nuts",
    "SO": "sunflower seeds",
    "UN": "other",
    "ABD": "poppy seeds",
    "ADQ": "glutamate",
    "X99": "no declaration-obligatory allergens",
})

CLAIM_DISPLAY_NAMES: Mapping[str, str] = MappingProxyType({
    "FREE_FROM_LACTOSE": "Lactose-free",
    "FREE_FROM_GLUTEN": "Gluten-free",
    "VEGAN": "Vegan",
    "VEGETARIAN": "Vegetarian",
    "LOW_LACTOSE": "Low lactose",
    "FREE_FROM_EGGS": "Egg-free",
    "FREE_FROM_SOY": "Soy-free",
    "FREE_FROM_WHEAT": "Wheat-free",
    "NO_ADDED_SUGARS": "No added sugars",
    "HIGH_FIBRE": "High fibre",
    "HIGH_PROTEIN": "High protein",
    "LOW_FAT": "Low fat",
    "FREE_FROM_FAT": "Fat-free",
    "LOW_SODIUM_SALT": "Low salt",
    "FREE_FROM_SODIUM_SALT": "Salt-free",
    "FREE_FROM_PRESERVATIVES": "Preservative-free",
    "FREE_FROM_COLOURING_AGENTS": "No colouring agents",
    "FREE_FROM_ADDITIVES": "Additive-free",
    "FREE_FROM_SWEETENERS": "Sweetener-free",
    "FREE_FROM_ARTIFICIAL_SWEETENERS": "No artificial sweeteners",
    "FREE_FROM_GMO": "GMO-free",
})

LABEL_DISPLAY_NAMES: Mapping[str, str] = MappingProxyType({
    "GOODS_FROM_FINLAND_BLUE_SWAN": "Hyvää Suomesta (Blue Swan)",
    "MADE_IN_FINLAND_FLAG_WITH_KEY": "Avainlippu (Key Flag)",
    "FINNISH_HEART_SYMBOL": "Finnish Heart Symbol",
    "EU_ORGANIC_FARMING": "EU Organic",
    "SVANEN": "Nordic Swan Ecolabel",
    "RAINFOREST_ALLIANCE": "Rainforest Alliance",
    "EU_ECO_LABEL": "EU Ecolabel",
    "MARINE_STEWARDSHIP_COUNCIL_LABEL": "MSC",
    "FAIR_TRADE_MARK": "Fairtrade",
    "AQUACULTURE_STEWARDSHIP_COUNCIL": "ASC",
    "KRAV_MARK": "KRAV Organic",
    "OFFICIAL_ECO_LABEL_SUN": "Luomu (Sun Symbol)",
    "SOIL_ASSOCIATION_ORGANIC_SYMBOL": "Soil Association Organic",
    "PUHTAASTI_KOTIMAINEN": "Puhtaasti Kotimainen",
    "DOLPHIN_SAFE": "Dolphin Safe",
    "UTZ_CERTIFIED": "UTZ Certified",
    "warmTemperature": "Ambient storage",
    "coolTemperature": "Chilled storage",
    "freezeTemperature": "Frozen storage",
})

SOURCE_ALLERGEN = TrustSource(
    system="valio-aimo.product-data",
    field="synkkaData.classifications[allergen]",
    retrievedAt=None,
)
SOURCE_NON_ALLERGEN = TrustSource(
    system="valio-aimo.product-data",
    field="synkkaData.classifications[nonAllergen]",
    retrievedAt=None,
)
SOURCE_CLAIM = TrustSource(
    system="valio-aimo.product-data",
    field="synkkaData.classifications[nutritionalClaim]",
    retrievedAt=None,
)
SOURCE_LABEL = TrustSource(
    system="valio-aimo.product-data",
    field="synkkaData.classifications[packagingMarkedLabel]",
    retrievedAt=None,
)
SOURCE_ORIGIN = TrustSource(
    system="valio-aimo.product-data",
    field="countryOfOrigin",
    retrievedAt=None,
)
SOURCE_UNIT_CONVERSIONS = TrustSource(
    system="valio-aimo.product-data",
    field="synkkaData.unitConversions",
    retrievedAt=None,
)
SOURCE_SALES_UNIT = TrustSource(
    system="valio-aimo.product-data",
    field="salesUnit",
    retrievedAt=None,
)
SOURCE_VARIABLE_UNIT = TrustSource(
    system="valio-aimo.product-data",
    field="synkkaData.variableUnit",
    retrievedAt=None,
)
SOURCE_DERIVED_NAME = TrustSource(
    system="derived",
    field="synkkaData.names",
    retrievedAt=None,
)
SOURCE_DERIVED_PRICE = TrustSource(
    system="derived",
    field="catalogPrice.value / comparisonPrice.value",
    retrievedAt=None,
)
SOURCE_RELIABILITY = TrustSource(
    system="valio-aimo.delivery-reliability",
    field="deliveryReliability",
    retrievedAt=None,
)
SOURCE_CO2 = TrustSource(
    system="valio-aimo.carbon-footprint",
    field="AVG kg CO2-eq/kg",
    retrievedAt=None,
)
SOURCE_SUBSTITUTION = TrustSource(
    system="valio-aimo.substitution-rules",
    field="substitutions",
    retrievedAt=None,
)

CONFLICT_K1 = Conflict(
    code="K1",
    message="Allergen AW (cereals containing gluten) is declared CONTAINS together with claim FREE_FROM_GLUTEN.",
)
CONFLICT_K2 = Conflict(
    code="K2",
    message="Allergen ML (lactose) is declared CONTAINS together with claim FREE_FROM_LACTOSE.",
)


def is_eu14_allergen(code: str) -> bool:
    """Return True if the GS1 allergen code is in the EU-14 groups or their subcodes (Patch P4)."""
    return code.upper() in EU14_ALLERGEN_CODES


def x99_proves_free_from(code: str) -> bool:
    """Patch P4: X99 proves free-from ONLY for the EU-14 allergen codes and subcodes."""
    return is_eu14_allergen(code)


def allergen_name(code: str) -> str:
    norm = code.upper()
    return ALLERGEN_NAMES.get(norm, norm)


def conflict_k1() -> Conflict:
    return CONFLICT_K1


def conflict_k2() -> Conflict:
    return CONFLICT_K2


def conflict_k3(closing_time: str, available_date: str) -> Conflict:
    return Conflict(
        code="K3",
        message=f"Order cut-off ({closing_time}) has passed while delivery date {available_date} is still offered.",
    )


def conflict_k4(product_unit: str, availability_unit: str) -> Conflict:
    return Conflict(
        code="K4",
        message=(
            f"Availability salesUnit ({availability_unit}) differs from product salesUnit ({product_unit}) "
            "and cannot be converted via units[]."
        ),
    )


def conflict_k5(declared_value: Decimal, derived_value: Decimal, unit: str) -> Conflict:
    return Conflict(
        code="K5",
        message=(
            f"Declared pack content ({format(declared_value, 'f')} {unit}) and price-ratio estimate "
            f"({format(derived_value, 'f')} {unit}) disagree by more than 5 %."
        ),
    )


def detect_k1(allergen_units: Mapping[str, str], claims: Collection[str]) -> Conflict | None:
    """K1: AW (cereals containing gluten) CONTAINS together with claim FREE_FROM_GLUTEN."""
    if allergen_units.get("AW") == "CONTAINS" and "FREE_FROM_GLUTEN" in claims:
        return CONFLICT_K1
    return None


def detect_k2(allergen_units: Mapping[str, str], claims: Collection[str]) -> Conflict | None:
    """K2: ML (lactose) CONTAINS together with claim FREE_FROM_LACTOSE."""
    if allergen_units.get("ML") == "CONTAINS" and "FREE_FROM_LACTOSE" in claims:
        return CONFLICT_K2
    return None


def detect_k5(
    declared_value: Decimal,
    declared_unit: str,
    derived_value: Decimal,
    derived_unit: str,
) -> Conflict | None:
    """K5: pack-content methods (1) and (2) disagree by more than 5 %."""
    if declared_unit.lower() != derived_unit.lower():
        return conflict_k5(declared_value, derived_value, declared_unit)
    if declared_value <= 0:
        return None
    diff_ratio = abs(declared_value - derived_value) / declared_value
    if diff_ratio > Decimal("0.05"):
        return conflict_k5(declared_value, derived_value, declared_unit)
    return None


def make_trust_label(
    *,
    fact: str,
    value: FactValue,
    display: str | None,
    level: TrustLevel,
    source: TrustSource,
    method: str | None = None,
    conflict: Conflict | None = None,
) -> TrustLabel:
    """Build a TrustLabel enforcing §5.4 ('UNKNOWN is shown as Unknown, never blank')."""
    text = (display or "").strip()
    if not text:
        text = "Unknown" if level == "UNKNOWN" else str(value)
    return TrustLabel(
        fact=fact,
        value=value,
        display=text,
        level=level,
        source=source,
        method=method,
        conflict=conflict,
    )


def claim_label(claim_id: str, conflict: Conflict | None = None) -> TrustLabel:
    display = CLAIM_DISPLAY_NAMES.get(claim_id, claim_id.replace("_", " ").capitalize())
    return TrustLabel(
        fact=f"claim.{claim_id}",
        value=True,
        display=display,
        level="DECLARED",
        source=SOURCE_CLAIM,
        method=None,
        conflict=conflict,
    )


def packaging_label(label_id: str) -> TrustLabel:
    display = LABEL_DISPLAY_NAMES.get(label_id, label_id)
    return TrustLabel(
        fact=f"label.{label_id}",
        value=True,
        display=display,
        level="DECLARED",
        source=SOURCE_LABEL,
        method=None,
        conflict=None,
    )


def origin_label(country_of_origin: str | None) -> TrustLabel:
    if country_of_origin and country_of_origin.strip():
        code = country_of_origin.strip().lower()
        return TrustLabel(
            fact="origin",
            value=code,
            display=f"Origin: {code.upper()}",
            level="DECLARED",
            source=SOURCE_ORIGIN,
            method=None,
            conflict=None,
        )
    return TrustLabel(
        fact="origin",
        value=None,
        display="Unknown",
        level="UNKNOWN",
        source=SOURCE_ORIGIN,
        method=None,
        conflict=None,
    )


def reliability_label(reliability: float | Decimal | None) -> TrustLabel:
    if reliability is None:
        return TrustLabel(
            fact="reliability",
            value=None,
            display="Unknown",
            level="UNKNOWN",
            source=SOURCE_RELIABILITY,
            method=None,
            conflict=None,
        )
    dec = Decimal(str(reliability))
    pct = (dec * Decimal(100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    pct_str = str(int(pct)) if pct == pct.to_integral() else format(pct, "f")
    return TrustLabel(
        fact="reliability",
        value=float(dec),
        display=f"Delivered in full {pct_str} % historically",
        level="ESTIMATE",
        source=SOURCE_RELIABILITY,
        method="historical delivery fraction",
        conflict=None,
    )


def co2_label(co2_kg_per_kg: float | Decimal | None) -> TrustLabel:
    if co2_kg_per_kg is None:
        return TrustLabel(
            fact="co2",
            value=None,
            display="Unknown",
            level="UNKNOWN",
            source=SOURCE_CO2,
            method=None,
            conflict=None,
        )
    dec = Decimal(str(co2_kg_per_kg)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    clean = dec.quantize(Decimal("0.1")) if dec == dec.quantize(Decimal("0.1")) else dec.normalize()
    return TrustLabel(
        fact="co2",
        value=float(dec),
        display=f"{format(clean, 'f')} kg CO2e/kg · product-group average",
        level="ESTIMATE",
        source=SOURCE_CO2,
        method="product-group average",
        conflict=None,
    )


def valio_substitute_label(substitute_product_id: str) -> TrustLabel:
    return TrustLabel(
        fact="substitute.valio",
        value=substitute_product_id,
        display=f"Valio Aimo listed substitute: {substitute_product_id} (buyer constraints must be re-checked)",
        level="VERIFIED",
        source=SOURCE_SUBSTITUTION,
        method=None,
        conflict=None,
    )


def trust_summary(facts: Iterable[TrustLabel]) -> dict[str, int]:
    """Return counts per trust level plus conflict count (§5.4)."""
    counts = {"VERIFIED": 0, "DECLARED": 0, "ESTIMATE": 0, "UNKNOWN": 0, "CONFLICT": 0}
    for item in facts:
        counts[item.level] = counts.get(item.level, 0) + 1
        if item.conflict is not None:
            counts["CONFLICT"] += 1
    return counts
