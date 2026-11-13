import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from synthetic.valio import DAY, NOW
from test_valio_client import client_for, successful_handler

from app.valio import IntegrationError, LiveValioProvider, RecordedValioProvider
from app.valio.record import BRIEF_PRODUCT_IDS, brief_product_ids, record_fixtures


@pytest.fixture
def capture(tmp_path):
    calls = []
    client = client_for(successful_handler(calls))
    target = record_fixtures(client, {"I1": ["SYN-A", "SYN-B"]}, ["buyer-a", "buyer-b"], [DAY], tmp_path)
    return target, calls


def test_recorder_creates_private_capture_raw_files_and_manifest(capture):
    target, calls = capture
    assert len(calls) == 4  # One price and availability batch per customer.
    assert target.is_dir() and target.name.endswith("Z")
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["version"] == 1
    assert len(manifest["batches"]) == 2
    assert {entry["customerId"] for entry in manifest["batches"]} == {"buyer-a", "buyer-b"}
    assert manifest["batches"][0]["productIds"] == ["SYN-A", "SYN-B"]
    for path in target.iterdir():
        assert path.stat().st_mode & 0o077 == 0
        assert "synthetic-key" not in path.read_text()
        assert "X-API-Key" not in path.read_text()
    assert target.stat().st_mode & 0o077 == 0


def test_replay_is_offline_frozen_and_projects_just_the_shortlist(capture, monkeypatch):
    target, _ = capture
    def no_network(*args, **kwargs):
        raise AssertionError("Recorded provider must never construct a network client")
    monkeypatch.setattr(httpx, "Client", no_network)
    provider = RecordedValioProvider(target)
    first = provider.lookup({"I1": ["SYN-B"]}, DAY, "buyer-a")
    second = provider.lookup({"I1": ["SYN-B"]}, DAY, "buyer-a", force_refresh=True)
    assert first == second
    assert first.mode == "RECORDED"
    assert first.recorded_at == provider.recorded_at
    assert first.now == NOW  # Not the process clock, capture finish time or requested delivery date.
    assert set(first.products) == {"SYN-B"}
    assert all(fact.source.retrievedAt == NOW for fact in first.products["SYN-B"].facts)


def test_live_and_recorded_evidence_have_identical_facts(capture):
    target, _ = capture
    live = LiveValioProvider(client_for(successful_handler([]))).lookup({"I1": ["SYN-A"]}, DAY, "buyer-a")
    recorded = RecordedValioProvider(target).lookup({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert live.products == recorded.products
    assert live.now == recorded.now


@pytest.mark.parametrize(("customer", "day", "ids"), [
    ("absent-buyer", DAY, ["SYN-A"]), ("buyer-a", DAY + timedelta(days=1), ["SYN-A"]),
    ("buyer-a", DAY, ["SYN-UNKNOWN"]),
])
def test_no_matching_recording_fails_without_live_fallback(capture, customer, day, ids):
    with pytest.raises(IntegrationError, match="No recorded batch"):
        RecordedValioProvider(capture[0]).lookup({"I1": ids}, day, customer)


@pytest.mark.parametrize("change", ["customer", "currency", "date", "id", "missing_file", "traversal", "invalid_file"])
def test_recorded_binding_and_paths_fail_closed(capture, change):
    target, _ = capture
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["batches"][0]
    price_path = target / entry["priceFile"]
    price = json.loads(price_path.read_text())
    if change == "customer":
        price["customerId"] = "OTHER"
    elif change == "currency":
        price["currency"] = "USD"
    elif change == "date":
        price["pricingDate"] = "2000-01-01T00:00:00+02:00"
    elif change == "id":
        price["products"][0]["productId"] = "OTHER"
    elif change == "missing_file":
        entry["priceFile"] = "missing.json"
    elif change == "traversal":
        entry["priceFile"] = "../private.json"
    elif change == "invalid_file":
        price_path.write_text("not-json")
    if change != "invalid_file":
        price_path.write_text(json.dumps(price))
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrationError):
        RecordedValioProvider(target).lookup({"I1": ["SYN-A"]}, DAY, "buyer-a")


def test_replay_reads_changed_private_price_for_recheck_without_advancing_clock(capture):
    target, _ = capture
    provider = RecordedValioProvider(target)
    before = provider.lookup({"I1": ["SYN-A"]}, DAY, "buyer-a")
    manifest = json.loads((target / "manifest.json").read_text())
    path = target / manifest["batches"][0]["priceFile"]
    body = json.loads(path.read_text())
    body["products"][0]["catalogPrice"]["value"] = 2.51
    path.write_text(json.dumps(body))
    after = provider.lookup({"I1": ["SYN-A"]}, DAY, "buyer-a", force_refresh=True)
    assert after.products["SYN-A"].price.unit_price - before.products["SYN-A"].price.unit_price == Decimal("0.01")
    assert before.now == after.now == NOW


@pytest.mark.parametrize("manifest", [None, {}, {"version": 99, "batches": [], "capturedAt": NOW.isoformat()}])
def test_missing_or_invalid_manifest_has_safe_error(tmp_path, manifest):
    if manifest is not None:
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IntegrationError, match="capture is missing or invalid"):
        RecordedValioProvider(tmp_path)


def test_recorder_does_not_write_partial_capture_on_read_failure(tmp_path):
    def fail(request):
        return httpx.Response(401, text="synthetic-key")
    with pytest.raises(IntegrationError):
        record_fixtures(client_for(fail), {"I1": ["SYN-A"]}, ["buyer-a"], [DAY], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_all_documented_demo_ids_are_in_default_recording_shortlist():
    root = Path(__file__).resolve().parents[2]
    ids = brief_product_ids(root)
    assert set(BRIEF_PRODUCT_IDS).issubset(ids)
    assert "999999" not in ids
    assert len(ids) <= 20


def test_recorder_discovers_new_brief_ids_without_copying_data(tmp_path):
    docs = tmp_path / "docs/brief"
    docs.mkdir(parents=True)
    (docs / "brief.txt").write_text("Synthetic product id 123456, and stock sentinel 999999.")
    assert "123456" in brief_product_ids(tmp_path)
