import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from test_agent_run import B_TEXT, _build_synthetic_catalog

from app.api import state as state_module
from app.api.state import GatewayState, SandboxRegistry, fresh_sandbox
from app.contracts import Proposal
from app.eval.runner import FROZEN_NOW, ReferenceFixtureProvider
from app.gateway.approvals import ApprovalStore, PasskeyApprovalError, PasskeyOrigin, TokenAlreadyUsedError
from app.gateway.proposals import ProposalGateway
from app.main import app
from app.policy import DEMO_MANDATES, M1, M2, MandateBook

SITE = PasskeyOrigin("https://valio-aimo.attractorfi.workers.dev", "valio-aimo.attractorfi.workers.dev")
ROLE = (M2.customerId, M2.approver.role)
PASSKEY_NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
AGENT_HEADERS = {"Authorization": f"Bearer demo-agent-{M2.customerId}"}
ROLE_HEADERS = {"X-Demo-Approver-Role": M2.approver.role, "Origin": SITE.origin}
ENROL = f"/v0.1/approver/passkeys/{M2.customerId}"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class TestAuthenticator:
    """Construct WebAuthn inputs with a fresh synthetic ES256 key and the standard test-client fields."""

    __test__ = False

    def __init__(self):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = b"synthetic-approval-credential"

    def client_data(self, challenge: str, ceremony: str, origin: str = SITE.origin) -> bytes:
        return json.dumps({"type": ceremony, "challenge": challenge, "origin": origin, "crossOrigin": False}).encode()

    def credential(self, response: dict) -> dict:
        credential_id = b64(self.credential_id)
        return {"id": credential_id, "rawId": credential_id, "type": "public-key", "response": response}

    def enrol(self, challenge: str) -> dict:
        public = self.private_key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")})
        data = (
            hashlib.sha256(SITE.rp_id.encode()).digest() + b"\x45" + bytes(4) + bytes(16)
            + len(self.credential_id).to_bytes(2, "big") + self.credential_id + cose
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": data})
        return self.credential({
            "attestationObject": b64(attestation),
            "clientDataJSON": b64(self.client_data(challenge, "webauthn.create")),
            "transports": ["internal"],
        })

    def assert_challenge(self, challenge: str, *, counter: int = 1, verified: bool = True,
                         origin: str = SITE.origin) -> dict:
        client = self.client_data(challenge, "webauthn.get", origin)
        flags = b"\x05" if verified else b"\x01"
        data = hashlib.sha256(SITE.rp_id.encode()).digest() + flags + counter.to_bytes(4, "big")
        signature = self.private_key.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return self.credential({
            "authenticatorData": b64(data), "clientDataJSON": b64(client), "signature": b64(signature),
        })


@pytest.fixture
def approver(monkeypatch):
    products = _build_synthetic_catalog()
    gateway = ProposalGateway(
        products=products, mandates=MandateBook(DEMO_MANDATES), provider=ReferenceFixtureProvider(),
    )
    resources = GatewayState(gateway, list(products.values()), "RECORDED", clock=lambda: FROZEN_NOW)
    registry = SandboxRegistry(lambda sandbox_id: fresh_sandbox(resources, sandbox_id))
    monkeypatch.setattr(state_module, "_state", resources)
    monkeypatch.setattr(state_module, "_registry", registry)
    with TestClient(app, headers={"X-Demo-Sandbox": "approver-fixture"}) as client:
        yield client, registry


def create_ask(client) -> Proposal:
    response = client.post(
        "/v0.1/agent/run", headers=AGENT_HEADERS, json={"customerId": M2.customerId, "requestText": B_TEXT},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["proposal"]["state"] == "PENDING_APPROVAL"
    assert "approvalToken" not in response.text
    return Proposal.model_validate(payload["proposal"])


def test_enrolled_role_requires_a_verified_passkey_even_on_a_second_device(approver):
    client, registry = approver
    proposal = create_ask(client)
    base = f"/v0.1/approver/proposals/{proposal.proposalId}"
    link = client.get(base, headers=ROLE_HEADERS).json()["approvalToken"]
    decision = {"offerVersion": proposal.offerVersion, "approvalToken": link, "action": "APPROVE"}
    phone_headers = {"X-Demo-Sandbox": "phone-fixture", "Origin": SITE.origin}

    assert client.post(f"{base}/passkey/options", headers=phone_headers, json={
        "offerVersion": proposal.offerVersion, "approvalToken": link,
    }).json() is None
    wrong_role = {**ROLE_HEADERS, "X-Demo-Approver-Role": M1.approver.role}
    assert client.post(f"{ENROL}/options", headers=wrong_role).status_code == 403
    wrong_origin = {**ROLE_HEADERS, "Origin": "https://different.example"}
    assert client.post(f"{ENROL}/options", headers=wrong_origin).status_code == 403

    key = TestAuthenticator()
    options = client.post(f"{ENROL}/options", headers=ROLE_HEADERS).json()
    assert options["rp"]["id"] == SITE.rp_id
    assert options["authenticatorSelection"]["authenticatorAttachment"] == "platform"
    assert client.post(ENROL, headers=ROLE_HEADERS, json={"credential": key.enrol(options["challenge"])}).json() == {
        "enrolled": True,
    }
    assert client.get(ENROL, headers={**ROLE_HEADERS, "X-Demo-Sandbox": "phone-fixture"}).json() == {"enrolled": False}
    assert client.post(f"{ENROL}/options", headers=ROLE_HEADERS).status_code == 400

    refused = client.post(f"{base}/decision", headers=phone_headers, json=decision)
    assert refused.status_code == 400
    assert "Passkey required" in refused.json()["error"]["message"]
    assert registry.get("approver-fixture").gateway.get_proposal(proposal.proposalId).state == "PENDING_APPROVAL"

    options = client.post(f"{base}/passkey/options", headers=phone_headers, json={
        "offerVersion": proposal.offerVersion, "approvalToken": link,
    }).json()
    assert options["userVerification"] == "required"
    signed = {**decision, "passkeyAssertion": key.assert_challenge(options["challenge"])}
    approved = client.post(f"{base}/decision", headers=phone_headers, json=signed)
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "CONFIRMED_SIMULATED"
    assert client.post(f"{base}/decision", headers=phone_headers, json=signed).status_code == 409
    entries = registry.get("approver-fixture").gateway.get_audit_trail(proposal_id=proposal.proposalId).entries
    assert [entry.actor for entry in entries if entry.event == "APPROVAL_GRANTED"] == ["human:Head Chef (passkey)"]


def test_assertion_cannot_cross_offer_versions_or_skip_user_verification(approver):
    proposal = create_ask(approver[0])
    next_proposal = proposal.model_copy(update={
        "proposalId": "PR-SYNTHETIC-PASSKEY-v2", "offerVersion": 2, "offerHash": "a" * 64,
    })
    store = ApprovalStore()
    key = TestAuthenticator()
    registration = store.registration_options(ROLE, SITE, now=PASSKEY_NOW)
    store.register_passkey(ROLE, SITE, key.enrol(registration["challenge"]), now=PASSKEY_NOW)
    old_token = store.issue(proposal, M2.approver).approvalToken
    next_token = store.issue(next_proposal, M2.approver).approvalToken
    old = store.approval_options(proposal, proposal.offerVersion, old_token, SITE, now=FROZEN_NOW, passkey_now=PASSKEY_NOW)
    new = store.approval_options(next_proposal, 2, next_token, SITE, now=FROZEN_NOW, passkey_now=PASSKEY_NOW)
    assert old is not None and new is not None and old["challenge"] != new["challenge"]

    args = {"offer_version": 2, "approval_token": next_token, "action": "APPROVE", "now": FROZEN_NOW,
            "passkey_now": PASSKEY_NOW}
    with pytest.raises(PasskeyApprovalError, match="could not be verified"):
        store.verify_and_consume(next_proposal, passkey_assertion=key.assert_challenge(old["challenge"]), **args)
    assert not store.get_grant(next_token).is_used

    new = store.approval_options(next_proposal, 2, next_token, SITE, now=FROZEN_NOW, passkey_now=PASSKEY_NOW)
    with pytest.raises(PasskeyApprovalError, match="could not be verified"):
        store.verify_and_consume(next_proposal, passkey_assertion=key.assert_challenge(new["challenge"], verified=False), **args)

    new = store.approval_options(next_proposal, 2, next_token, SITE, now=FROZEN_NOW, passkey_now=PASSKEY_NOW)
    wrong_origin = key.assert_challenge(new["challenge"], origin="https://different.example")
    with pytest.raises(PasskeyApprovalError, match="could not be verified"):
        store.verify_and_consume(next_proposal, passkey_assertion=wrong_origin, **args)

    new = store.approval_options(next_proposal, 2, next_token, SITE, now=FROZEN_NOW, passkey_now=PASSKEY_NOW)
    proof = key.assert_challenge(new["challenge"])
    with pytest.raises(PasskeyApprovalError, match="expired"):
        store.verify_and_consume(next_proposal, passkey_assertion=proof, **{**args, "passkey_now": PASSKEY_NOW + timedelta(minutes=3)})
    new = store.approval_options(next_proposal, 2, next_token, SITE, now=FROZEN_NOW, passkey_now=PASSKEY_NOW)
    proof = key.assert_challenge(new["challenge"])
    assert store.verify_and_consume(next_proposal, passkey_assertion=proof, **args).verified_with_passkey
    with pytest.raises(TokenAlreadyUsedError):
        store.verify_and_consume(next_proposal, passkey_assertion=proof, **args)
