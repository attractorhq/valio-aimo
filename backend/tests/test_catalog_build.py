"""Tests for catalogue artifact build, statistics computation, and load time."""

import json
import time
from pathlib import Path

import pytest

from app.catalog.build import (
    DEFAULT_RAW_DIR,
    build_catalog,
    build_catalog_from_records,
    load_catalog,
)
from tests.synthetic import synthetic_product_record


def test_synthetic_catalog_build_and_load_round_trip(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    records = [
        synthetic_product_record(
            product_id="SYN-1",
            category="10400",
            allergens=[("AM", "CONTAINS")],
            claims=["FREE_FROM_LACTOSE"],
            unit_conversions=[
                {"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1.0}}
            ],
            vendor_name="Vendor A",
        ),
        synthetic_product_record(
            product_id="SYN-2",
            category="21380",
            allergens=[],
            non_allergens=[],
            claims=["FREE_FROM_GLUTEN"],
            names={"en": "Synthetic Macaroni 5 kg"},
            vendor_name="Vendor B",
        ),
        synthetic_product_record(
            product_id="SYN-3",
            category="64000",
            allergens=[("X99", "CONTAINS")],
            non_allergens=[],
            names={"en": "Synthetic Plastic Cup"},
            vendor_name="Vendor B",
        ),
    ]
    (raw_dir / "products.json").write_text(json.dumps(records), encoding="utf-8")
    (raw_dir / "delivery-reliability.csv").write_text(
        "productId,deliveryReliability\nSYN-1,1.0\nSYN-2,0.95\n",
        encoding="utf-8",
    )
    (raw_dir / "carbon-footprint.csv").write_text(
        "productId,AVG kg CO2-eq/kg\nSYN-1,1.1\n",
        encoding="utf-8",
    )

    out_path = tmp_path / "catalog" / "catalog.json"
    artifact = build_catalog(raw_dir=raw_dir, output_path=out_path)
    assert out_path.exists()
    assert out_path.with_name("stats.json").exists()

    assert artifact.stats.products == 3
    assert artifact.stats.vendors == 2
    assert artifact.stats.foodProducts == 2
    assert artifact.stats.nonFoodProducts == 1
    assert artifact.stats.noAllergenDeclaration == 1
    assert artifact.stats.x99Products == 1
    assert artifact.stats.reliabilityKnown == 2
    assert artifact.stats.reliabilityUnknown == 1
    assert artifact.stats.co2Known == 1
    assert artifact.stats.co2Unknown == 2

    loaded = load_catalog(out_path)
    assert loaded == artifact
    assert set(loaded.by_id()) == {"SYN-1", "SYN-2", "SYN-3"}


def test_real_dataset_statistics_and_performance_when_present():
    from app.catalog.build import DEFAULT_ARTIFACT_PATH

    if not (DEFAULT_RAW_DIR / "products.json").exists():
        pytest.skip("data/raw/products.json not present on this machine")

    if not DEFAULT_ARTIFACT_PATH.exists():
        t0 = time.perf_counter()
        build_catalog(raw_dir=DEFAULT_RAW_DIR, output_path=DEFAULT_ARTIFACT_PATH)
        build_s = time.perf_counter() - t0
        assert build_s < 60.0

    t1 = time.perf_counter()
    loaded = load_catalog(DEFAULT_ARTIFACT_PATH)
    load_s = time.perf_counter() - t1
    assert load_s < 2.0
    assert len(loaded.products) == 17546

    # Verify dataset statistics match docs/PLAN.md §1
    stats = loaded.stats
    assert stats.products == 17546
    assert stats.vendors == 648
    assert stats.categories == 349
    assert stats.allergenUnits == {"CONTAINS": 15610, "MAY_CONTAIN": 9956, "UNDECLARED": 303}
    assert stats.nonAllergenUnits == {"FREE_FROM": 68597}
    assert stats.noAllergenDeclaration == 8110
    assert stats.x99Products == 195
    assert stats.conflicts == {"K1": 11, "K2": 4}
    assert stats.contentMethod1UnitConversions == 8215
    assert stats.productsWithDeposits == 506


def test_duplicate_product_id_is_rejected():
    records = [
        synthetic_product_record(product_id="SYN-DUP"),
        synthetic_product_record(product_id="SYN-DUP"),
    ]
    with pytest.raises(ValueError, match="Duplicate product ID"):
        build_catalog_from_records(records)
