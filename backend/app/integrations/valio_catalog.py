"""Normalize the actual challenge product schema, retaining untranslatable technical units.

Do not infer product facts or sizes from names, comparison prices or gross packaging weight.
"""

from decimal import Decimal

from app.catalog.normalise import normalise_product
from app.schemas import ProductOut
from app.trust.labels import EU14_ALLERGEN_CODES

# GS1 allergen identifiers present in Synkka classifications. Keep original codes as well.
# Verified against the dataset (13 Nov): ML = lactose (milk chocolate, cheese), UM = molluscs
# (clams, mussels, octopus), AW = cereals containing gluten (any gluten cereal), UW = wheat.
ALLERGENS = {
    "AC": "crustaceans",
    "AE": "eggs",
    "AF": "fish",
    "AM": "milk",
    "AN": "nuts",
    "AP": "peanuts",
    "AS": "sesame",
    "AU": "sulphites",
    "AW": "gluten",
    "UW": "wheat",
    "AY": "soy",
    "BC": "celery",
    "BM": "mustard",
    "ML": "lactose",
    "UM": "molluscs",
    "NL": "lupin",
    "NR": "rye",
    "GB": "barley",
    "GO": "oats",
    "GS": "spelt",
    "GK": "kamut",
    "SA": "almonds",
    "SH": "hazelnuts",
    "SC": "cashews",
    "SP": "pecans",
    "SR": "brazil nuts",
    "ST": "pistachios",
    "SW": "walnuts",
    "SM": "macadamia nuts",
}
NON_OAT_GLUTEN_CEREALS = {"wheat", "rye", "barley", "spelt", "kamut"}


def normalize_product(record: dict) -> ProductOut:
    code, sales_unit = record["id"], record["salesUnit"]
    if not isinstance(code, str):
        raise ValueError("Original Valio IDs must be strings.")
    synkka = record.get("synkkaData") or {}
    names = {n["language"]: n["value"] for n in synkka.get("names", []) if n.get("value")}
    name = names.get("en") or names.get("fi") or next(iter(names.values()), f"Product {code} (name unknown)")
    groups = {g["name"]: g["values"] for g in synkka.get("classifications", [])}
    claims = {c["id"] for c in groups.get("nutritionalClaim", [])}
    dietary = {}
    for claim, attribute in [
        ("FREE_FROM_LACTOSE", "lactose_free"),
        ("FREE_FROM_GLUTEN", "gluten_free"),
        ("VEGAN", "vegan"),
        ("VEGETARIAN", "vegetarian"),
    ]:
        if claim in claims:
            dietary[attribute] = True
    if "LOW_LACTOSE" in claims and "FREE_FROM_LACTOSE" not in claims:
        dietary["lactose_free"] = False

    positive = [
        a["id"]
        for a in groups.get("allergen", [])
        if a.get("unit") == "CONTAINS" and a.get("id") != "X99"
    ]
    free_codes = [a["id"] for a in groups.get("nonAllergen", []) if a.get("unit") == "FREE_FROM"]
    has_x99 = any(
        a.get("id") == "X99" and a.get("unit") != "UNDECLARED"
        for a in (*groups.get("allergen", []), *groups.get("nonAllergen", []))
    )
    if has_x99:
        # Patch P4: X99 proves free-from ONLY for the EU-14 allergen codes and subcodes.
        free_codes.extend(c for c in EU14_ALLERGEN_CODES if c not in positive)

    # A partial list of positives never establishes absence of a different allergen.
    allergens = [ALLERGENS.get(c, f"gs1:{c}") for c in positive]
    # §6.3: GO (oats) CONTAINS + FREE_FROM_GLUTEN is allowed gluten-free oats, not gluten.
    if NON_OAT_GLUTEN_CEREALS.intersection(allergens) or (
        "oats" in allergens and not dietary.get("gluten_free")
    ):
        if "gluten" not in allergens:
            allergens.append("gluten")
    free = [ALLERGENS.get(c, f"gs1:{c}") for c in free_codes if c != "X99"]
    if dietary.get("gluten_free") and "AW" not in positive:
        free.append("gluten")

    # Default to exact supplier unit. Normalize physical quantity only with explicit base-level facts.
    unit, factor = sales_unit, Decimal(1)
    if sales_unit == "KG":
        unit = "KG"
    elif not synkka.get("variableUnit"):
        base = next(
            (
                x
                for x in synkka.get("unitConversions", [])
                if x.get("synkkaPackagingType") == "BASE_UNIT_OR_EACH"
            ),
            None,
        )
        sales = next((x for x in record.get("units", []) if x["unitId"] == sales_unit), None)
        count = Decimal(str(sales["sizeInBaseUnits"])) if sales else None
        # BASE_UNIT_OR_EACH describes a consumer piece; never map it to a KG base automatically.
        if record["baseUnit"] == "ST" and base and count and count > 0:
            volume, weight = base.get("volume", {}), base.get("netWeight", {})
            if volume.get("unit") in ("LTR", "MLT") and Decimal(str(volume["value"])) > 0:
                unit = "L"
                factor = count * Decimal(str(volume["value"])) / (1000 if volume["unit"] == "MLT" else 1)
            elif weight.get("unit") in ("GRM", "KGM") and Decimal(str(weight["value"])) > 0:
                unit = "KG"
                factor = count * Decimal(str(weight["value"])) / (1000 if weight["unit"] == "GRM" else 1)
        # Unknown packaging sizes stay in the original technical sales unit, not guessed EA/L/KG.
    lot = Decimal(str(record["allowedLotSize"])) * factor
    return ProductOut(
        code=code,
        name=name,
        names=names,
        category=record["category"],
        unit=unit,
        pack_quantity=lot,
        sales_unit=sales_unit,
        quantity_per_sales_unit=factor,
        dietary=dietary,
        allergens=allergens if positive or free_codes or has_x99 else None,
        free_from_allergens=sorted(set(free)),
        allergen_codes=positive,
        nutrition={"declaration": synkka["nutritionalContent"]} if synkka.get("nutritionalContent") else None,
        substitution_codes=sorted(
            {s["substituteProduct"] for s in record.get("substitutions", []) if s.get("product") == code}
        ),
        deposits=record.get("deposits", []),
        variable_weight=synkka.get("variableUnit"),
        source="Valio Junction supplied catalog (synthetic challenge data)",
    )


__all__ = ["ALLERGENS", "NON_OAT_GLUTEN_CEREALS", "normalise_product", "normalize_product"]
