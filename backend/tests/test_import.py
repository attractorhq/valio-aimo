import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Product


@pytest.fixture
def importer(context, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "import_products", Path(__file__).resolve().parents[2] / "scripts/import_products.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, _, factory, settings = context
    # Use a new database for import; fixture DB intentionally contains reserved demo codes.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    monkeypatch.setattr(module, "engine", engine)
    monkeypatch.setattr(module, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    yield module
    engine.dispose()


def test_import_unknowns_and_original_ids(importer, tmp_path):
    records = [
        {
            "code": "001234",
            "name": "Unverified food",
            "category": "pantry",
            "unit": "KG",
            "pack_quantity": "5",
        }
    ]
    (tmp_path / "products.json").write_text(json.dumps(records))
    result = importer.import_catalog(tmp_path)
    assert result["products"] == 1
    with importer.SessionLocal() as db:
        p = db.get(Product, "001234")
        assert p.data["allergens"] is None
        assert p.data["reliability"] is None
        assert p.data["carbon_kg_co2_per_kg"] is None
        assert p.data["dietary"] == {}


def test_import_rejects_incomplete_batch_atomically(importer, tmp_path):
    records = [
        {"code": "001", "name": "Food", "category": "pantry", "unit": "KG", "pack_quantity": "5"},
        {"code": "002", "name": "No unit"},
    ]
    (tmp_path / "products.json").write_text(json.dumps(records))
    with pytest.raises(ValueError):
        importer.import_catalog(tmp_path)
    with importer.SessionLocal() as db:
        assert list(db.scalars(select(Product))) == []


def test_real_mode_does_not_fabricate_fallback(importer, tmp_path):
    importer.get_settings().mock_mode = False
    with pytest.raises(ValueError, match="never falls back"):
        importer.import_catalog(tmp_path)
