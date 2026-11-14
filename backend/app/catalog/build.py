"""Build and load the normalised, trust-labelled catalogue artifact in data/catalog/."""

import csv
import json
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from jsonschema import validate as validate_json_schema

from app.catalog.normalise import normalise_product
from app.contracts.common import VERSION, Contract
from app.contracts.models import Product

ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_RAW_DIR = ROOT_DIR / "data/raw"
DEFAULT_CATALOG_DIR = ROOT_DIR / "data/catalog"
DEFAULT_ARTIFACT_PATH = DEFAULT_CATALOG_DIR / "catalog.json"


class CatalogStats(Contract):
    products: int
    vendors: int
    categories: int
    foodProducts: int
    nonFoodProducts: int
    rawFileBytes: int | None = None
    allergenUnits: dict[str, int]
    nonAllergenUnits: dict[str, int]
    noAllergenDeclaration: int
    onlyUndeclaredAllergens: int
    hasAllergenDeclaration: int
    x99Products: int
    conflicts: dict[str, int]
    contentMethod1UnitConversions: int
    contentMethod1Resolved: int
    contentKgOrVariableWeight: int
    contentSalesUnitKg: int
    contentVariableWeight: int
    contentMethod3NameEstimate: int
    contentUnknown: int
    reliabilityKnown: int
    reliabilityUnknown: int
    co2Known: int
    co2Unknown: int
    productsWithSubstitutions: int
    productsWithDeposits: int


class CatalogArtifact(Contract):
    version: str = VERSION
    stats: CatalogStats
    products: list[Product]

    def by_id(self) -> dict[str, Product]:
        return {p.productId: p for p in self.products}


def load_csv_floats(
    path: Path,
    code_column: str = "productId",
    value_column: str = "deliveryReliability",
) -> dict[str, float]:
    """Read productId -> float map from a CSV file; missing files return an empty dict."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample.strip() else csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        if not reader.fieldnames or not {code_column, value_column} <= set(reader.fieldnames):
            raise ValueError(f"{path}: expected columns ({code_column!r}, {value_column!r}), got {reader.fieldnames}")
        result: dict[str, float] = {}
        for row in reader:
            code = (row.get(code_column) or "").strip()
            value = (row.get(value_column) or "").strip()
            if not code or not value:
                continue
            if code in result:
                raise ValueError(f"Duplicate product code in {path}: {code}")
            result[code] = float(value)
        return result


def compute_catalog_stats(
    raw_records: Sequence[dict],
    products: Sequence[Product],
    reliability_map: dict[str, float],
    co2_map: dict[str, float],
    raw_file_bytes: int | None = None,
) -> CatalogStats:
    vendors = {str(r.get("vendorName") or "").strip() for r in raw_records if r.get("vendorName")}
    categories = {p.category for p in products}
    food_count = sum(1 for p in products if p.isFood is True)
    non_food_count = len(products) - food_count

    alg_units: Counter[str] = Counter()
    non_alg_units: Counter[str] = Counter()
    no_alg_decl = 0
    only_undeclared = 0
    x99_count = 0
    m1_raw = 0
    sales_unit_kg = 0
    var_weight = 0

    for rec in raw_records:
        if rec.get("salesUnit") == "KG":
            sales_unit_kg += 1
        synkka = rec.get("synkkaData") or {}
        if synkka.get("variableUnit"):
            var_weight += 1
        if any(
            u.get("synkkaPackagingType") == "BASE_UNIT_OR_EACH"
            for u in (synkka.get("unitConversions") or [])
        ):
            m1_raw += 1
        groups = {g.get("name"): g.get("values") or [] for g in (synkka.get("classifications") or [])}
        alg = groups.get("allergen") or []
        nalg = groups.get("nonAllergen") or []
        if not alg and not nalg:
            no_alg_decl += 1
        elif alg and not nalg and all((a.get("unit") or "").upper() == "UNDECLARED" for a in alg):
            only_undeclared += 1
        codes_in_rec = set()
        for a in alg:
            u = (a.get("unit") or "").strip().upper()
            if u:
                alg_units[u] += 1
            if a.get("id"):
                codes_in_rec.add(a["id"].strip().upper())
        for a in nalg:
            u = (a.get("unit") or "").strip().upper()
            if u:
                non_alg_units[u] += 1
            if a.get("id"):
                codes_in_rec.add(a["id"].strip().upper())
        if "X99" in codes_in_rec:
            x99_count += 1

    k1_count = 0
    k2_count = 0
    m1_resolved = 0
    kg_or_var = 0
    m3_name = 0
    content_unknown = 0
    has_alg_decl = 0
    with_subs = 0
    with_deps = 0
    rel_known = 0
    co2_known = 0

    for prod in products:
        if prod.hasAllergenDeclaration:
            has_alg_decl += 1
        if prod.substitutions:
            with_subs += 1
        if prod.deposits:
            with_deps += 1
        if prod.productId in reliability_map:
            rel_known += 1
        if prod.productId in co2_map:
            co2_known += 1
        codes_found = {
            f.conflict.code
            for f in prod.facts
            if f.conflict is not None
        } | {
            a.evidence.conflict.code
            for a in prod.allergens
            if a.evidence.conflict is not None
        }
        if "K1" in codes_found:
            k1_count += 1
        if "K2" in codes_found:
            k2_count += 1

        cpsu = prod.contentPerSalesUnit
        if cpsu is None:
            content_unknown += 1
        elif cpsu.method in ("salesUnit.KG", "variableUnit"):
            kg_or_var += 1
        elif cpsu.method and cpsu.method.startswith("unitConversions."):
            m1_resolved += 1
        elif cpsu.method == "name":
            m3_name += 1

    total = len(products)
    return CatalogStats(
        products=total,
        vendors=len(vendors),
        categories=len(categories),
        foodProducts=food_count,
        nonFoodProducts=non_food_count,
        rawFileBytes=raw_file_bytes,
        allergenUnits=dict(alg_units),
        nonAllergenUnits=dict(non_alg_units),
        noAllergenDeclaration=no_alg_decl,
        onlyUndeclaredAllergens=only_undeclared,
        hasAllergenDeclaration=has_alg_decl,
        x99Products=x99_count,
        conflicts={"K1": k1_count, "K2": k2_count},
        contentMethod1UnitConversions=m1_raw,
        contentMethod1Resolved=m1_resolved,
        contentKgOrVariableWeight=kg_or_var,
        contentSalesUnitKg=sales_unit_kg,
        contentVariableWeight=var_weight,
        contentMethod3NameEstimate=m3_name,
        contentUnknown=content_unknown,
        reliabilityKnown=rel_known,
        reliabilityUnknown=total - rel_known,
        co2Known=co2_known,
        co2Unknown=total - co2_known,
        productsWithSubstitutions=with_subs,
        productsWithDeposits=with_deps,
    )


def build_catalog_from_records(
    records: Sequence[dict],
    reliability_map: dict[str, float] | None = None,
    co2_map: dict[str, float] | None = None,
    raw_file_bytes: int | None = None,
) -> CatalogArtifact:
    """Normalise raw product dicts and CSV maps into a validated CatalogArtifact."""
    rel = reliability_map or {}
    co2 = co2_map or {}
    active_records: list[dict] = []
    products: list[Product] = []
    seen_ids: set[str] = set()

    for rec in records:
        if rec.get("deleted"):
            continue
        pid = rec.get("id")
        if not isinstance(pid, str) or not pid.strip():
            raise ValueError("Product ID must be a non-empty string.")
        pid_clean = pid.strip()
        if pid_clean in seen_ids:
            raise ValueError(f"Duplicate product ID in catalogue: {pid_clean}")
        seen_ids.add(pid_clean)
        active_records.append(rec)
        products.append(
            normalise_product(
                rec,
                reliability=rel.get(pid_clean),
                co2=co2.get(pid_clean),
            )
        )

    stats = compute_catalog_stats(active_records, products, rel, co2, raw_file_bytes=raw_file_bytes)
    return CatalogArtifact(version=VERSION, stats=stats, products=products)


def build_catalog(
    raw_dir: Path = DEFAULT_RAW_DIR,
    output_path: Path | None = DEFAULT_ARTIFACT_PATH,
    *,
    validate_schema: bool = False,
) -> CatalogArtifact:
    """Build the catalogue artifact from data/raw/ files and write to data/catalog/."""
    products_path = raw_dir / "products.json"
    if not products_path.exists():
        raise FileNotFoundError(
            f"Missing {products_path}. Run `.venv/bin/python scripts/download_valio_data.py` first."
        )
    raw_bytes = products_path.stat().st_size
    records = json.loads(products_path.read_text(encoding="utf-8-sig"))
    if not isinstance(records, list):
        raise ValueError(f"{products_path} must contain a JSON array of products.")

    schema_path = raw_dir / "product-schema.json"
    if validate_schema and schema_path.exists():
        validate_json_schema(records, json.loads(schema_path.read_text(encoding="utf-8-sig")))

    reliability = load_csv_floats(raw_dir / "delivery-reliability.csv", "productId", "deliveryReliability")
    co2 = load_csv_floats(raw_dir / "carbon-footprint.csv", "productId", "AVG kg CO2-eq/kg")

    artifact = build_catalog_from_records(records, reliability, co2, raw_file_bytes=raw_bytes)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_json = output_path.with_suffix(".json.tmp")
        tmp_json.write_bytes(artifact.model_dump_json().encode("utf-8"))
        tmp_json.replace(output_path)

        stats_path = output_path.with_name("stats.json")
        stats_path.write_text(artifact.stats.model_dump_json(indent=2) + "\n", encoding="utf-8")

        # productId -> vendorName for the readiness view (T27). The container image excludes data/raw/, so the
        # supplier names must travel with the catalogue artifact (data/catalog/, gitignored like the rest).
        vendors = {
            str(rec.get("id")).strip(): str(rec.get("vendorName")).strip()
            for rec in records
            if isinstance(rec, dict) and not rec.get("deleted") and rec.get("id") and rec.get("vendorName")
        }
        output_path.with_name("vendors.json").write_text(json.dumps(vendors, ensure_ascii=False) + "\n", encoding="utf-8")

    return artifact


def load_catalog(
    artifact_path: Path = DEFAULT_ARTIFACT_PATH,
    *,
    raw_dir: Path = DEFAULT_RAW_DIR,
) -> CatalogArtifact:
    """Load and validate the catalogue artifact in under 2 seconds via Pydantic's Rust JSON parser."""
    if not artifact_path.exists():
        return build_catalog(raw_dir=raw_dir, output_path=artifact_path)
    return CatalogArtifact.model_validate_json(artifact_path.read_bytes())


@lru_cache(maxsize=2)
def get_cached_catalog(artifact_path: Path = DEFAULT_ARTIFACT_PATH) -> CatalogArtifact:
    return load_catalog(artifact_path)
