"""T70: WebAuthn passkey enrollment and assertion verification on the approver channel."""

import hashlib
import json
import struct
from base64 import urlsafe_b64encode

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from test_api import api as api  # noqa: F401
from test_proposals_gateway import request_b

from app.gateway.approvals import (
    PasskeyRequiredError,
    PasskeyVerificationError,
    proposal_passkey_challenge,
    proposal_passkey_challenge_b64,
)
from app.policy import M2


def _b64u(data: bytes) -> str:
    return urlsafe_b64encode(data).decode("ascii").rstrip("=")


class SyntheticPasskeyAuthenticator:
    """Synthetic ES256 (P-256) WebAuthn platform authenticator for deterministic unit tests."""

    def __init__(self, credential_id: bytes = b"syn-passkey-head-chef-1", rp_id: str = "localhost"):
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = credential_id
        self.rp_id = rp_id
        self.rp_id_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()
        self.sign_count = 0

    def cose_public_key(self) -> bytes:
        pub = self.private_key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: pub.x.to_bytes(32, "big"),
                -3: pub.y.to_bytes(32, "big"),
            }
        )

    def build_registration_credential(self, challenge_b64: str, origin: str = "https://localhost") -> dict:
        att_auth_data = (
            self.rp_id_hash
            + bytes([0x45])  # UP (0x01) | UV (0x04) | AT (0x40)
            + struct.pack(">I", 0)
            + (b"\x00" * 16)
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self.cose_public_key()
        )
        att_obj = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": att_auth_data})
        client_data = json.dumps(
            {
                "type": "webauthn.create",
                "challenge": challenge_b64,
                "origin": origin,
                "crossOrigin": False,
            }
        ).encode("utf-8")
        cid = _b64u(self.credential_id)
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "response": {
                "clientDataJSON": _b64u(client_data),
                "attestationObject": _b64u(att_obj),
            },
        }

    def build_assertion(self, challenge_b64: str, origin: str = "https://localhost") -> dict:
        self.sign_count += 1
        auth_data = self.rp_id_hash + bytes([0x05]) + struct.pack(">I", self.sign_count)
        client_data = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": challenge_b64,
                "origin": origin,
                "crossOrigin": False,
            }
        ).encode("utf-8")
        client_data_hash = hashlib.sha256(client_data).digest()
        signature = self.private_key.sign(auth_data + client_data_hash, ec.ECDSA(hashes.SHA256()))
        cid = _b64u(self.credential_id)
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "response": {
                "authenticatorData": _b64u(auth_data),
                "clientDataJSON": _b64u(client_data),
                "signature": _b64u(signature),
                "userHandle": None,
            },
        }


def _create_pending_proposal(client):
    offer_req, req = request_b()
    offer = client.post("/v0.1/offers", json=offer_req.model_dump(mode="json")).json()
    prop = client.post(
        "/v0.1/proposals",
        json={
            "customerId": M2.customerId,
            "offerId": offer["offerId"],
            "offerVersion": offer["offerVersion"],
            "request": req.model_dump(mode="json"),
        },
    ).json()
    assert prop["state"] == "PENDING_APPROVAL"
    return prop


def test_no_passkey_keeps_link_based_approval_flow_unchanged(api):  # noqa: F811
    client, _provider, _state = api
    prop = _create_pending_proposal(client)
    pid = prop["proposalId"]

    view = client.get(f"/v0.1/approver/proposals/{pid}").json()
    res = client.post(
        f"/v0.1/approver/proposals/{pid}/decision",
        json={
            "offerVersion": prop["offerVersion"],
            "approvalToken": view["approvalToken"],
            "action": "APPROVE",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "CONFIRMED_SIMULATED"

    trail = client.get("/v0.1/audit", params={"proposalId": pid}).json()["entries"]
    approved_entry = next(e for e in trail if e["event"] == "APPROVAL_GRANTED")
    assert approved_entry["actor"] == "human:Head Chef (link)"


def test_passkey_enrolment_refuses_link_alone_rejects_wrong_version_and_verifies_right_challenge(
    api,  # noqa: F811
):
    client, _provider, state = api
    prop = _create_pending_proposal(client)
    pid = prop["proposalId"]
    view = client.get(f"/v0.1/approver/proposals/{pid}").json()
    token = view["approvalToken"]

    # 1. Enrol a passkey for Head Chef via the phone token (cross-device sandbox routing)
    authenticator = SyntheticPasskeyAuthenticator()
    status_before = client.get(
        "/v0.1/approver/passkey/status",
        headers={"X-Approval-Token": token},
    ).json()
    assert status_before == {"role": "Head Chef", "enrolled": False, "credentialCount": 0}

    opt_res = client.post(
        "/v0.1/approver/passkey/register/options",
        headers={"X-Approval-Token": token},
        json={"rpId": "localhost"},
    )
    assert opt_res.status_code == 200, opt_res.text
    reg_options = opt_res.json()["options"]

    reg_cred = authenticator.build_registration_credential(reg_options["challenge"])
    ver_res = client.post(
        "/v0.1/approver/passkey/register/verify",
        headers={"X-Approval-Token": token},
        json={"rpId": "localhost", "credential": reg_cred},
    )
    assert ver_res.status_code == 200, ver_res.text
    assert ver_res.json() == {"role": "Head Chef", "enrolled": True, "credentialCount": 1}

    # 2. Link alone without WebAuthn assertion is refused with "Passkey required"
    no_passkey_res = client.post(
        f"/v0.1/approver/proposals/{pid}/decision",
        json={
            "offerVersion": prop["offerVersion"],
            "approvalToken": token,
            "action": "APPROVE",
        },
    )
    assert no_passkey_res.status_code == 400
    assert "passkey required" in no_passkey_res.json()["error"]["message"].lower()

    # Direct store call also raises PasskeyRequiredError
    proposal_obj = state.gateway.get_proposal(pid)
    raw_token = state.gateway.approvals.grants_for_proposal(pid)[0].token
    with pytest.raises(PasskeyRequiredError, match="Passkey required"):
        state.gateway.approvals.verify_and_consume(
            proposal_obj,
            offer_version=proposal_obj.offerVersion,
            approval_token=raw_token,
            action="APPROVE",
            now=proposal_obj.createdAt,
        )

    # 3. Assertion for another proposal version's challenge fails verification
    wrong_version_challenge = proposal_passkey_challenge_b64(
        pid,
        prop["offerVersion"] + 1,
        prop["offerHash"],
    )
    wrong_assertion = authenticator.build_assertion(wrong_version_challenge)
    wrong_res = client.post(
        f"/v0.1/approver/proposals/{pid}/decision",
        json={
            "offerVersion": prop["offerVersion"],
            "approvalToken": token,
            "action": "APPROVE",
            "passkeyAssertion": wrong_assertion,
        },
    )
    assert wrong_res.status_code == 400
    assert "passkey" in wrong_res.json()["error"]["message"].lower()

    with pytest.raises(PasskeyVerificationError):
        state.gateway.approvals.verify_and_consume(
            proposal_obj,
            offer_version=proposal_obj.offerVersion,
            approval_token=raw_token,
            action="APPROVE",
            now=proposal_obj.createdAt,
            passkey_assertion=wrong_assertion,
        )

    # 4. Right challenge (derived from proposalId + offerVersion + offerHash) verifies and logs passkey actor
    auth_opts_res = client.get(
        f"/v0.1/approver/proposals/{pid}/passkey-options",
        headers={"X-Approval-Token": token},
    )
    assert auth_opts_res.status_code == 200
    auth_opts = auth_opts_res.json()
    assert auth_opts["enrolled"] is True
    expected_challenge_b64 = proposal_passkey_challenge_b64(pid, prop["offerVersion"], prop["offerHash"])
    assert auth_opts["options"]["challenge"] == expected_challenge_b64
    assert proposal_passkey_challenge(pid, prop["offerVersion"], prop["offerHash"]) == hashlib.sha256(
        f"{pid}:{prop['offerVersion']}:{prop['offerHash']}".encode()
    ).digest()

    valid_assertion = authenticator.build_assertion(expected_challenge_b64)
    ok_res = client.post(
        f"/v0.1/approver/proposals/{pid}/decision",
        json={
            "offerVersion": prop["offerVersion"],
            "approvalToken": token,
            "action": "APPROVE",
            "passkeyAssertion": valid_assertion,
        },
    )
    assert ok_res.status_code == 200, ok_res.text
    assert ok_res.json()["state"] == "CONFIRMED_SIMULATED"

    # 5. Audit trail records `human:Head Chef (passkey)` and never leaks tokens/links to agent channel
    trail = client.get("/v0.1/audit", params={"proposalId": pid}).json()["entries"]
    approved_entry = next(e for e in trail if e["event"] == "APPROVAL_GRANTED")
    assert approved_entry["actor"] == "human:Head Chef (passkey)"

    agent_prop_raw = client.get(f"/v0.1/proposals/{pid}").text
    assert token not in agent_prop_raw
    assert "approvalToken" not in agent_prop_raw
