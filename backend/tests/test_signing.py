"""T67: Ed25519 signed offers, decisions and confirmations with published JWK Set, discovery,
hash-chained audit exports, offline stdlib `scripts/verify-audit`, and `scripts/second-client` verification."""

import csv
import importlib.machinery
import importlib.util
import io
import json
import logging
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from test_agent_run import NOW, SyntheticAgentProvider, _build_synthetic_catalog
from test_api import APPROVER, api, scenario_a, scenario_b  # noqa: F401 (pytest fixture)

from app.api import state as state_module
from app.api.state import GatewayState, SandboxRegistry, fresh_sandbox
from app.gateway.proposals import ProposalGateway
from app.gateway.signing import (
    GatewaySigner,
    verify_audit_entry_signature,
    verify_confirmation_signature,
    verify_decision_signature,
    verify_offer_signature,
)
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
VERIFY_AUDIT_SCRIPT = ROOT / "scripts/verify-audit"
SECOND_CLIENT_SCRIPT = ROOT / "scripts/second-client"


def _load_script_module(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("pub_hex", "msg_hex", "sig_hex"),
    [
        # RFC 8032 §7.1 TEST 1 (empty message)
        (
            "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
            "",
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
        ),
        # RFC 8032 §7.1 TEST 2 (1-byte 0x72)
        (
            "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
            "72",
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
        ),
        # RFC 8032 §7.1 TEST 3 (2-byte 0xaf82)
        (
            "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
            "af82",
            "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
            "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
        ),
    ],
)
def test_stdlib_ed25519_verifier_passes_rfc8032_test_vectors(pub_hex, msg_hex, sig_hex):
    mod = _load_script_module("verify_audit_mod", VERIFY_AUDIT_SCRIPT)
    pub = bytes.fromhex(pub_hex)
    msg = bytes.fromhex(msg_hex)
    sig = bytes.fromhex(sig_hex)
    assert mod.ed25519_verify(pub, sig, msg) is True
    assert mod.ed25519_verify(pub, sig, msg + b"x") is False
    tampered_sig = bytearray(sig)
    tampered_sig[0] ^= 0x01
    assert mod.ed25519_verify(pub, bytes(tampered_sig), msg) is False


def test_discovery_and_jwks_endpoints_publish_ed25519_key_without_leaking_secret(api, caplog, monkeypatch):  # noqa: F811
    client, _, state = api
    secret = "super-secret-signing-material-t67"
    monkeypatch.setenv("APPROVAL_SIGNING_KEY", secret)
    state.gateway.signer = GatewaySigner(secret)
    caplog.set_level(logging.DEBUG)

    disc_res = client.client.get("/.well-known/aimo-connect.json")
    assert disc_res.status_code == 200, disc_res.text
    disc = disc_res.json()
    assert disc["openapi"].endswith("/openapi.json")
    assert disc["mcp"].endswith("/mcp")
    assert disc["spec"].endswith("/spec")
    assert disc["jwks"].endswith("/.well-known/aimo-connect-keys.json")
    assert disc["signingAlgorithm"] == "EdDSA"

    jwks_res = client.client.get("/.well-known/aimo-connect-keys.json")
    assert jwks_res.status_code == 200, jwks_res.text
    jwks = jwks_res.json()
    (jwk,) = jwks["keys"]
    assert jwk["kty"] == "OKP"
    assert jwk["crv"] == "Ed25519"
    assert jwk["alg"] == "EdDSA"
    assert jwk["use"] == "sig"
    assert jwk["kid"] == disc["kid"]
    assert secret not in jwks_res.text
    assert secret not in disc_res.text
    assert secret not in caplog.text


def test_offer_decision_and_confirmation_signatures_verify_and_reject_tampering_or_wrong_key(api):  # noqa: F811
    client, _, _ = api
    jwks = client.client.get("/.well-known/aimo-connect-keys.json").json()
    other_jwks = GatewaySigner("different-signing-key-material").jwks()

    # 1. Scenario B: Offer signature + pending ASK decision signature
    offer_b, created_b = scenario_b(client)
    prop_b = created_b.json()
    assert offer_b["kid"] and offer_b["signature"]
    assert verify_offer_signature(offer_b, jwks) is True
    # Tampered offer total fails verification
    tampered_offer = {**offer_b, "totals": {**offer_b["totals"], "exclVat": "406.16"}}
    assert verify_offer_signature(tampered_offer, jwks) is False
    # Different key fails verification
    assert verify_offer_signature(offer_b, other_jwks) is False

    # Pending ASK proposal has decision signature, no confirmationSignature yet
    assert prop_b["kid"] == offer_b["kid"]
    assert prop_b["signature"] and prop_b["confirmationSignature"] is None
    assert verify_decision_signature(prop_b, jwks) is True
    assert verify_decision_signature({**prop_b, "decision": "AUTO"}, jwks) is False
    assert verify_decision_signature(prop_b, other_jwks) is False

    # Approve Scenario B -> confirmed proposal now carries confirmationSignature too
    view = client.client.get(f"{APPROVER}/proposals/{prop_b['proposalId']}").json()
    confirmed_b = client.client.post(
        f"{APPROVER}/proposals/{prop_b['proposalId']}/decision",
        json={"offerVersion": prop_b["offerVersion"], "approvalToken": view["approvalToken"], "action": "APPROVE"},
    ).json()
    assert confirmed_b["state"] == "CONFIRMED_SIMULATED"
    assert confirmed_b["confirmationSignature"]
    assert verify_decision_signature(confirmed_b, jwks) is True
    assert verify_confirmation_signature(confirmed_b, jwks) is True
    assert verify_confirmation_signature(
        {**confirmed_b, "totals": {**confirmed_b["totals"], "exclVat": "1.00"}}, jwks
    ) is False
    assert verify_confirmation_signature(confirmed_b, other_jwks) is False

    # 2. Scenario A: AUTO proposal confirms immediately with both decision and confirmation signatures
    _, created_a = scenario_a(client)
    prop_a = created_a.json()
    assert prop_a["state"] == "CONFIRMED_SIMULATED"
    assert verify_decision_signature(prop_a, jwks) is True
    assert verify_confirmation_signature(prop_a, jwks) is True


def test_audit_exports_include_hashes_and_offline_verify_audit_detects_tampering(api, tmp_path):  # noqa: F811
    client, _, _ = api
    _, created_a = scenario_a(client)
    proposal_id = created_a.json()["proposalId"]
    jwks = client.client.get("/.well-known/aimo-connect-keys.json").json()

    # JSON export includes prevHash, hash, and signed milestone receipts
    json_res = client.client.get("/v0.1/audit/export", params={"proposalId": proposal_id, "format": "json"})
    assert json_res.status_code == 200
    exported_doc = json_res.json()
    entries = exported_doc["entries"]
    assert len(entries) >= 4
    assert entries[0]["prevHash"] == "0" * 64
    assert all(e["prevHash"] and e["hash"] for e in entries)
    signed_entries = [e for e in entries if e["signature"]]
    assert len(signed_entries) >= 3  # OFFER_CREATED, POLICY_DECIDED, ORDER_CONFIRMED_SIMULATED
    assert all(verify_audit_entry_signature(e, jwks) for e in signed_entries)

    # CSV export also includes prevHash, hash, kid, signature columns
    csv_res = client.client.get("/v0.1/audit/export", params={"proposalId": proposal_id, "format": "csv"})
    assert csv_res.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_res.content.decode("utf-8-sig"))))
    assert [r["hash"] for r in rows] == [e["hash"] for e in entries]
    assert rows[0]["prevHash"] == "0" * 64

    export_file = tmp_path / "export.json"
    jwks_file = tmp_path / "jwks.json"
    wrong_jwks_file = tmp_path / "wrong-jwks.json"
    export_file.write_text(json.dumps(exported_doc, indent=2), encoding="utf-8")
    jwks_file.write_text(json.dumps(jwks, indent=2), encoding="utf-8")
    wrong_jwks_file.write_text(json.dumps(GatewaySigner("wrong-key").jwks(), indent=2), encoding="utf-8")

    # Run scripts/verify-audit with stdlib-only environment (no PYTHONPATH)
    clean_env = {"PATH": "/usr/bin:/bin"}
    ok_offline = subprocess.run(
        [sys.executable, str(VERIFY_AUDIT_SCRIPT), str(export_file)],
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok_offline.returncode == 0, ok_offline.stderr
    assert "verified" in ok_offline.stdout.lower()

    ok_signed = subprocess.run(
        [sys.executable, str(VERIFY_AUDIT_SCRIPT), str(export_file), "--keys", str(jwks_file)],
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok_signed.returncode == 0, ok_signed.stderr
    assert "signature(s) verified" in ok_signed.stdout

    # Wrong key fails signature verification
    bad_key_run = subprocess.run(
        [sys.executable, str(VERIFY_AUDIT_SCRIPT), str(export_file), "--keys", str(wrong_jwks_file)],
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad_key_run.returncode != 0
    assert "invalid Ed25519 signature" in bad_key_run.stderr

    # Tampering a single entry in the exported file fails offline chain verification
    tampered_doc = json.loads(export_file.read_text(encoding="utf-8"))
    tampered_doc["entries"][1]["summary"] = "Tampered summary text"
    tampered_file = tmp_path / "tampered.json"
    tampered_file.write_text(json.dumps(tampered_doc, indent=2), encoding="utf-8")
    tampered_run = subprocess.run(
        [sys.executable, str(VERIFY_AUDIT_SCRIPT), str(tampered_file)],
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tampered_run.returncode != 0
    assert tampered_doc["entries"][1]["entryId"] in tampered_run.stderr


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_second_client_verifies_offer_signature_against_published_key(monkeypatch):
    products = _build_synthetic_catalog()
    resources = GatewayState(
        ProposalGateway(products=products, provider=SyntheticAgentProvider(), signing_key="t67-test-key"),
        list(products.values()),
        "RECORDED",
        clock=lambda: NOW,
    )
    monkeypatch.setenv("APPROVAL_SIGNING_KEY", "t67-test-key")
    registry = SandboxRegistry(lambda sandbox_id: fresh_sandbox(resources, sandbox_id))
    monkeypatch.setattr(state_module, "_state", resources)
    monkeypatch.setattr(state_module, "_registry", registry)
    app.dependency_overrides.clear()

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started

        proc = subprocess.run(
            [sys.executable, str(SECOND_CLIENT_SCRIPT), f"http://127.0.0.1:{port}"],
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert "signature verified" in proc.stdout
        assert "Decision:     ASK" in proc.stdout
        assert "€506.16 excl. VAT" in proc.stdout
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        app.dependency_overrides.clear()
