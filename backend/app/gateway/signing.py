"""Detached Ed25519 receipt signing and RFC 8037 JWK Set publication (T67).

Derives a domain-separated Ed25519 keypair from `APPROVAL_SIGNING_KEY` (or a process-local fallback key in dev)
without ever logging secret material. Signs canonical JSON payloads for:
- `Offer` (`aimo-connect.offer.v0.1`)
- `Proposal` decision (`aimo-connect.decision.v0.1`)
- `Proposal` simulated confirmation (`aimo-connect.confirmation.v0.1`)
- Milestone `AuditEntry` records (`aimo-connect.audit-entry.v0.1`)
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_EPHEMERAL_ROOT_KEY: bytes = secrets.token_bytes(32)
_KEY_DERIVATION_DOMAIN = b"aimo-connect-ed25519-receipt-signing-v1"
SIGNED_AUDIT_EVENTS: frozenset[str] = frozenset({
    "OFFER_CREATED",
    "POLICY_DECIDED",
    "DECIDED",
    "ORDER_CONFIRMED_SIMULATED",
    "CONFIRMED_SIMULATED",
})


def default_signing_key() -> bytes:
    """Return configured `APPROVAL_SIGNING_KEY` bytes or the process-local ephemeral key."""
    configured = os.environ.get("APPROVAL_SIGNING_KEY")
    return configured.encode("utf-8") if configured else _EPHEMERAL_ROOT_KEY


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    padded = text.strip() + "=" * ((4 - len(text.strip()) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _iso_str(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _as_mapping(obj: Any) -> Mapping[str, Any]:
    if isinstance(obj, Mapping):
        return obj
    return obj.model_dump(mode="json")


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_offer_signing_payload(offer: Any) -> bytes:
    data = _as_mapping(offer)
    totals = data["totals"]
    return canonical_json_bytes({
        "type": "aimo-connect.offer.v0.1",
        "customerId": str(data["customerId"]),
        "offerId": str(data["offerId"]),
        "offerVersion": int(data["offerVersion"]),
        "offerHash": str(data["offerHash"]),
        "totals": {
            "exclVat": str(totals["exclVat"]),
            "vat": str(totals["vat"]),
            "inclVat": str(totals["inclVat"]),
            "currency": str(totals.get("currency", "EUR")),
        },
        "createdAt": _iso_str(data["createdAt"]),
        "validUntil": _iso_str(data["validUntil"]),
    })


def canonical_decision_signing_payload(proposal: Any) -> bytes:
    data = _as_mapping(proposal)
    totals = data["totals"]
    return canonical_json_bytes({
        "type": "aimo-connect.decision.v0.1",
        "proposalId": str(data["proposalId"]),
        "customerId": str(data["customerId"]),
        "mandateVersion": int(data["mandateVersion"]),
        "offerId": str(data["offerId"]),
        "offerVersion": int(data["offerVersion"]),
        "offerHash": str(data["offerHash"]),
        "decision": str(data["decision"]),
        "reasons": [str(r) for r in data.get("reasons", ())],
        "totalsExclVat": str(totals["exclVat"]),
    })


def canonical_confirmation_signing_payload(proposal: Any) -> bytes:
    data = _as_mapping(proposal)
    totals = data["totals"]
    return canonical_json_bytes({
        "type": "aimo-connect.confirmation.v0.1",
        "proposalId": str(data["proposalId"]),
        "customerId": str(data["customerId"]),
        "mandateVersion": int(data["mandateVersion"]),
        "offerId": str(data["offerId"]),
        "offerVersion": int(data["offerVersion"]),
        "offerHash": str(data["offerHash"]),
        "decision": str(data["decision"]),
        "state": "CONFIRMED_SIMULATED",
        "totalsExclVat": str(totals["exclVat"]),
        "simulationNotice": str(data.get("simulationNotice", "Simulated order — mock environment")),
    })


def canonical_audit_entry_signing_payload(entry: Any) -> bytes:
    data = _as_mapping(entry)
    event = str(data["event"])
    return canonical_json_bytes({
        "type": "aimo-connect.audit-entry.v0.1",
        "entryId": str(data["entryId"]),
        "event": event,
        "customerId": str(data["customerId"]),
        "proposalId": None if event == "OFFER_CREATED" else data.get("proposalId"),
        "offerId": data.get("offerId"),
        "offerVersion": data.get("offerVersion"),
        "offerHash": data.get("offerHash"),
        "mandateVersion": int(data["mandateVersion"]),
        "toState": data.get("toState"),
        "reasons": [str(r) for r in data.get("reasons", ())],
        "timestamp": _iso_str(data["timestamp"]),
        "summary": str(data["summary"]),
    })


class GatewaySigner:
    """Ed25519 signer and JWK Set publisher derived from a gateway root key."""

    def __init__(self, signing_key: str | bytes | None = None):
        self._explicit_key: bytes | None = (
            signing_key.encode("utf-8")
            if isinstance(signing_key, str)
            else (bytes(signing_key) if signing_key is not None else None)
        )

    def _private_key(self) -> Ed25519PrivateKey:
        raw = self._explicit_key if self._explicit_key is not None else default_signing_key()
        seed = hmac.new(raw, _KEY_DERIVATION_DOMAIN, hashlib.sha256).digest()
        return Ed25519PrivateKey.from_private_bytes(seed)

    @property
    def public_bytes(self) -> bytes:
        return self._private_key().public_key().public_bytes_raw()

    @property
    def kid(self) -> str:
        digest = hashlib.sha256(self.public_bytes).hexdigest()[:16]
        return f"ac-ed25519-{digest}"

    def jwk(self) -> dict[str, str]:
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "use": "sig",
            "alg": "EdDSA",
            "kid": self.kid,
            "x": b64url_encode(self.public_bytes),
        }

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [self.jwk()]}

    def sign_bytes(self, payload: bytes) -> str:
        sig = self._private_key().sign(payload)
        return b64url_encode(sig)

    def sign_offer(self, offer: Any) -> str:
        return self.sign_bytes(canonical_offer_signing_payload(offer))

    def sign_decision(self, proposal: Any) -> str:
        return self.sign_bytes(canonical_decision_signing_payload(proposal))

    def sign_confirmation(self, proposal: Any) -> str:
        return self.sign_bytes(canonical_confirmation_signing_payload(proposal))

    def sign_audit_entry(self, entry: Any) -> str:
        return self.sign_bytes(canonical_audit_entry_signing_payload(entry))


def verify_jwk_signature(
    jwks_or_jwk: Mapping[str, Any],
    *,
    kid: str | None,
    payload: bytes,
    signature: str | None,
) -> bool:
    """Verify a detached base64url Ed25519 signature against a JWK or JWK Set."""
    if not kid or not signature:
        return False
    keys = jwks_or_jwk.get("keys") if "keys" in jwks_or_jwk else [jwks_or_jwk]
    if not isinstance(keys, list):
        return False
    matched = next(
        (
            k
            for k in keys
            if isinstance(k, Mapping)
            and k.get("kid") == kid
            and k.get("kty") == "OKP"
            and k.get("crv") == "Ed25519"
            and isinstance(k.get("x"), str)
        ),
        None,
    )
    if matched is None:
        return False
    try:
        pub_bytes = b64url_decode(matched["x"])
        sig_bytes = b64url_decode(signature)
        if len(pub_bytes) != 32 or len(sig_bytes) != 64:
            return False
        pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub.verify(sig_bytes, payload)
        return True
    except (ValueError, InvalidSignature):
        return False


def verify_offer_signature(offer: Any, jwks_or_jwk: Mapping[str, Any]) -> bool:
    data = _as_mapping(offer)
    return verify_jwk_signature(
        jwks_or_jwk,
        kid=data.get("kid"),
        payload=canonical_offer_signing_payload(data),
        signature=data.get("signature"),
    )


def verify_decision_signature(proposal: Any, jwks_or_jwk: Mapping[str, Any]) -> bool:
    data = _as_mapping(proposal)
    return verify_jwk_signature(
        jwks_or_jwk,
        kid=data.get("kid"),
        payload=canonical_decision_signing_payload(data),
        signature=data.get("signature"),
    )


def verify_confirmation_signature(proposal: Any, jwks_or_jwk: Mapping[str, Any]) -> bool:
    data = _as_mapping(proposal)
    if data.get("state") != "CONFIRMED_SIMULATED":
        return False
    return verify_jwk_signature(
        jwks_or_jwk,
        kid=data.get("kid"),
        payload=canonical_confirmation_signing_payload(data),
        signature=data.get("confirmationSignature"),
    )


def verify_audit_entry_signature(entry: Any, jwks_or_jwk: Mapping[str, Any]) -> bool:
    data = _as_mapping(entry)
    return verify_jwk_signature(
        jwks_or_jwk,
        kid=data.get("kid"),
        payload=canonical_audit_entry_signing_payload(data),
        signature=data.get("signature"),
    )
