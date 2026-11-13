"""Hand-made synthetic product record builders for unit tests (Patch P7 compliant)."""


def synthetic_product_record(
    *,
    product_id: str = "SYN-0001",
    names: dict[str, str] | None = None,
    category: str = "10400",
    sales_unit: str = "ST",
    base_unit: str = "ST",
    allowed_lot_size: float | int = 1,
    units: list[dict] | None = None,
    unit_conversions: list[dict] | None = None,
    variable_unit: bool = False,
    allergens: list[tuple[str, str]] | None = None,
    non_allergens: list[tuple[str, str]] | None = None,
    claims: list[str] | None = None,
    labels: list[str] | None = None,
    country_of_origin: str | None = "fi",
    key_ingredients: dict[str, str] | None = None,
    ingredients: list[dict] | None = None,
    substitutions: list[str] | None = None,
    deposits: list[dict] | None = None,
    vendor_name: str = "Synthetic Dairy Oy",
    deleted: bool = False,
) -> dict:
    """Build a synthetic Valio-shaped product dictionary from explicit test parameters."""
    resolved_names = names if names is not None else {
        "en": "Synthetic Milk Drink 1 l",
        "fi": "Synteettinen maitojuoma 1 l",
        "sv": "Syntetisk mjölkdryck 1 l",
    }
    classifications: list[dict] = []
    if allergens is not None:
        classifications.append({
            "name": "allergen",
            "type": "ENUM",
            "values": [{"id": code, "unit": unit} for code, unit in allergens],
        })
    if non_allergens is not None:
        classifications.append({
            "name": "nonAllergen",
            "type": "ENUM",
            "values": [{"id": code, "unit": unit} for code, unit in non_allergens],
        })
    if claims:
        classifications.append({
            "name": "nutritionalClaim",
            "type": "ENUM",
            "values": [{"id": c, "unit": "FREE_FROM"} for c in claims],
        })
    if labels:
        classifications.append({
            "name": "packagingMarkedLabel",
            "type": "ENUM",
            "values": [{"id": lbl} for lbl in labels],
        })

    resolved_units = units if units is not None else [{"unitId": sales_unit, "sizeInBaseUnits": 1}]
    synkka_data: dict = {
        "names": [{"language": lang, "value": text} for lang, text in resolved_names.items()],
        "classifications": classifications,
        "keyIngredients": [
            {"language": lang, "value": text}
            for lang, text in (key_ingredients or {"en": "milk, vitamin D", "fi": "maito, D-vitamiini"}).items()
        ],
        "marketingTexts": [],
        "materialAdditionalDescriptions": [],
        "medias": [],
        "nutritionalContent": [],
        "variableUnit": variable_unit,
    }
    if unit_conversions is not None:
        synkka_data["unitConversions"] = unit_conversions
    if ingredients is not None:
        synkka_data["ingredients"] = ingredients

    record: dict = {
        "id": product_id,
        "salesUnit": sales_unit,
        "baseUnit": base_unit,
        "allowedLotSize": allowed_lot_size,
        "category": category,
        "deleted": deleted,
        "deposits": deposits or [],
        "substitutions": [
            {"product": product_id, "substituteProduct": sub_id}
            for sub_id in (substitutions or [])
        ],
        "synkkaData": synkka_data,
        "units": resolved_units,
        "vendorName": vendor_name,
    }
    if country_of_origin is not None:
        record["countryOfOrigin"] = country_of_origin
    return record
