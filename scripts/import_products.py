"""Explicit catalog import. No guessing of the unverified vendor schema or units."""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jsonschema import validate as validate_json_schema
from sqlalchemy import select

from app.catalog.build import load_csv_floats
from app.config import get_settings
from app.db.models import Base, Product
from app.db.seed import seed
from app.db.session import SessionLocal, engine
from app.integrations.valio_catalog import normalize_product
from app.schemas import ProductOut


def field(data, path):
    for part in path.split(".") if path else []:
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def csv_values(path, code_column, value_column):
    return load_csv_floats(path, code_column, value_column)


def import_catalog(
    raw: Path, mapping_path: Path | None = None, reliability_columns=None, carbon_columns=None
):
    products_path = raw / "products.json"
    Base.metadata.create_all(engine)
    if not products_path.exists():
        if not get_settings().mock_mode:
            raise ValueError(
                "No products.json found. Real-data mode never falls back to fabricated products."
            )
        with SessionLocal() as db:
            seed(db)
        return {"mode": "synthetic fixtures", "products": 8}

    document = json.loads(products_path.read_text(encoding="utf-8-sig"))
    schema_path = raw / "product-schema.json"
    if schema_path.exists():
        validate_json_schema(document, json.loads(schema_path.read_text(encoding="utf-8-sig")))
    mapping = json.loads(mapping_path.read_text()) if mapping_path else {}
    records = field(document, mapping.get("root", ""))
    if not isinstance(records, list):
        raise ValueError("Catalog must be a JSON array, or mapping.root must name an array.")
    vendor = not mapping and records and "salesUnit" in records[0] and "id" in records[0]
    reliability = csv_values(
        raw / "delivery-reliability.csv",
        *(
            reliability_columns
            or (("productId", "deliveryReliability") if vendor else ("product_code", "reliability"))
        ),
    )
    carbon = csv_values(
        raw / "carbon-footprint.csv",
        *(
            carbon_columns
            or (("productId", "AVG kg CO2-eq/kg") if vendor else ("product_code", "AVG kg CO2-eq/kg"))
        ),
    )
    normalized = []
    seen = set()
    for record in records:
        if vendor:
            if record.get("deleted"):
                continue
            record = normalize_product(record).model_dump(mode="json")
        data = (
            {key: field(record, path) for key, path in mapping["fields"].items()}
            if "fields" in mapping
            else dict(record)
        )
        for key, translations in mapping.get("values", {}).items():
            original = data.get(key)
            if original not in translations:
                raise ValueError(f"Unmapped value for {key}: {original!r}; no units/facts are inferred.")
            data[key] = translations[original]
        # Unknowns can be absent/null. Required code/name/category/unit/pack may NOT be guessed.
        data = {k: v for k, v in data.items() if v is not None}
        if not isinstance(data.get("code"), str) or not data["code"].strip():
            raise ValueError(
                "Product codes must be original strings; numeric conversion could lose leading zeroes."
            )
        if data["code"] in seen or data["code"].startswith("DEMO-"):
            raise ValueError(f"Duplicate or reserved fixture code: {data['code']}")
        seen.add(data["code"])
        if not vendor:
            data["source"] = "Imported challenge catalog (synthetic supplier data)"
        if data["code"] in reliability:
            data["reliability"] = reliability[data["code"]]
        if data["code"] in carbon:
            data["carbon_kg_co2_per_kg"] = carbon[data["code"]]
            data["carbon_scope"] = "category_average"
        normalized.append(ProductOut.model_validate(data))
    if not normalized:
        raise ValueError("Refusing an empty catalog import.")
    # Validate the entire batch before the first write. Upsert only; never delete user records.
    with SessionLocal() as db:
        if db.scalar(select(Product).where(Product.code.like("DEMO-%"))):
            raise ValueError(
                "This database contains demo fixtures. Use a NEW DATABASE_URL for imported data."
            )
        for product in normalized:
            db.merge(
                Product(
                    code=product.code,
                    name=product.name,
                    category=product.category,
                    data=product.model_dump(mode="json"),
                )
            )
        seed(db, include_products=False)
        db.commit()
    return {
        "mode": "imported catalog",
        "products": len(normalized),
        "vendor_schema_validated": schema_path.exists(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data/raw")
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--reliability-code-column")
    parser.add_argument("--reliability-value-column")
    parser.add_argument("--carbon-code-column")
    parser.add_argument("--carbon-value-column")
    args = parser.parse_args()
    try:
        result = import_catalog(
            args.raw_dir,
            args.mapping,
            (args.reliability_code_column, args.reliability_value_column)
            if args.reliability_code_column and args.reliability_value_column
            else None,
            (args.carbon_code_column, args.carbon_value_column)
            if args.carbon_code_column and args.carbon_value_column
            else None,
        )
    except Exception as exc:
        parser.exit(1, f"Import failed (no batch changes applied): {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
