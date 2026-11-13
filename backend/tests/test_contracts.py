"""Contract-only tests on hand-made synthetic UI examples; no event data or live calls."""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from app.contracts import (
    REASON_REGISTRY,
    ApproverView,
    AuditEntry,
    AuditTrail,
    ContentPerSalesUnit,
    CreateProposalRequest,
    Decision,
    GetOfferRequest,
    Mandate,
    Offer,
    Product,
    Proposal,
    ProposalState,
    ReasonCode,
    TrustLabel,
    format_date,
    format_money,
    format_time,
)
from app.contracts.export import AGENT_MODELS, schemas, typescript

ROOT = Path(__file__).resolve().parents[2]
MOCKS = ROOT / "frontend/src/mocks"


def mock(name):
    return json.loads((MOCKS / name).read_text())


def assert_schema(model, value):
    schema = schemas()[model.__name__]
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


@pytest.mark.parametrize(("filename", "model"), [
    ("scenario-a.json", Proposal), ("scenario-b.json", Proposal),
    ("mandate-m1.json", Mandate), ("mandate-m2.json", Mandate),
])
def test_synthetic_mocks_validate_and_round_trip(filename, model):
    example = mock(filename)
    result = model.model_validate(example)
    assert model.model_validate_json(result.model_dump_json()) == result
    assert_schema(model, example)
    assert_schema(model, result.model_dump(mode="json"))


def test_scenario_b_shape_single_reason_and_exact_totals():
    proposal = Proposal.model_validate(mock("scenario-b.json"))
    assert proposal.decision == "ASK"
    assert proposal.state == "PENDING_APPROVAL"
    assert proposal.reasons == [ReasonCode.OVER_AUTO_LIMIT]
    assert [line.quantitySalesUnits for line in proposal.lines] == [Decimal(18), Decimal(80)]
    assert [line.deliveredQuantity.value for line in proposal.lines] == [Decimal(90), Decimal(80)]
    assert [line.lineTotalExclVat for line in proposal.lines] == [Decimal("412.56"), Decimal("93.60")]
    assert sum(line.lineTotalExclVat for line in proposal.lines) == Decimal("506.16")
    assert proposal.totals.model_dump(mode="json") == {
        "exclVat": "506.16", "vat": "68.33", "inclVat": "574.49", "currency": "EUR",
    }
    assert any(ReasonCode.SUBSTITUTE_REJECTED in item.reasons for item in proposal.excluded)
    assert all(not line.reasons for line in proposal.lines)
    mandate = Mandate.model_validate(mock("mandate-m2.json"))
    assert mandate.allergenMode == "STRICT"
    assert mandate.autoApproveLimitEur == Decimal("500.00")
    assert proposal.customerId == mandate.customerId
    assert proposal.mandateVersion == mandate.version


def test_scenario_a_shape():
    proposal = Proposal.model_validate(mock("scenario-a.json"))
    assert (proposal.decision, proposal.state, proposal.reasons) == ("AUTO", "CONFIRMED_SIMULATED", [])
    assert proposal.lines[0].quantitySalesUnits == 1000
    assert proposal.totals.exclVat == Decimal("1010.00")
    assert proposal.simulationNotice == "Simulated order — mock environment"


@pytest.mark.parametrize("state", get_args(ProposalState))
def test_every_p1_state_is_representable_without_running_transitions(state):
    data = mock("scenario-b.json")
    data["state"] = state
    assert Proposal.model_validate(data).state == state


def test_offer_and_agent_operation_bindings_validate():
    proposal = Proposal.model_validate(mock("scenario-b.json"))
    offer = Offer.model_validate({
        name: value for name, value in proposal.model_dump().items() if name in Offer.model_fields
    })
    assert offer.offerHash == proposal.offerHash
    assert_schema(Offer, offer.model_dump(mode="json"))
    create = CreateProposalRequest(
        customerId=proposal.customerId, offerId=proposal.offerId,
        offerVersion=proposal.offerVersion, request=proposal.request,
    )
    assert_schema(CreateProposalRequest, create.model_dump(mode="json"))
    inquiry = GetOfferRequest(
        customerId=proposal.customerId, requestedDate=proposal.requestedDate,
        items=[{"itemId": "I1", "productId": "MOCK-PASTA", "quantity": "90", "unit": "kg"}],
    )
    assert_schema(GetOfferRequest, inquiry.model_dump(mode="json"))
    assert_schema(Decision, {"decision": "ASK", "reasons": ["OVER_AUTO_LIMIT"]})


def test_normalised_product_and_unknown_fact():
    unknown = TrustLabel(
        fact="content.perSalesUnit", value=None, display="Unknown", level="UNKNOWN",
        source={"system": "valio-aimo.product-data", "field": "unitConversions", "retrievedAt": None},
    )
    product = Product(
        productId="MOCK-UNKNOWN", name="Synthetic unknown pack", category="synthetic",
        salesUnit="ST", baseUnit="ST", allowedLotSize="1", facts=[unknown],
    )
    assert product.contentPerSalesUnit is None
    assert_schema(Product, product.model_dump(mode="json"))
    variable_content = ContentPerSalesUnit(value="1", unit="kg", level="DECLARED", method="variableUnit")
    assert_schema(ContentPerSalesUnit, variable_content.model_dump(mode="json"))


def test_registry_is_complete_and_effects_are_explicit():
    assert set(REASON_REGISTRY) == set(ReasonCode)
    for code, reason in REASON_REGISTRY.items():
        assert code == reason.code
        assert reason.effect in reason.allowedEffects
        assert reason.approvable == ("ASK" in reason.allowedEffects)
        assert reason.chipText and reason.template
        assert_schema(type(reason), reason.model_dump(mode="json"))
    assert REASON_REGISTRY[ReasonCode.VALIO_SUBSTITUTE].allowedEffects == ("ASK", "INFO")
    assert REASON_REGISTRY[ReasonCode.ALLERGEN_CONTAINS].allowedEffects == ("EXCLUDE", "BLOCK")


@pytest.mark.parametrize("code", [
    "CLAIM_MISSING", "ALLERGEN_CONTAINS", "ALLERGEN_MAY_CONTAIN", "ALLERGEN_UNKNOWN",
    "ALLERGEN_IMPLICIT", "DATA_CONFLICT", "CUTOFF_PASSED", "UNIT_MISMATCH",
    "NOT_AVAILABLE", "SALES_BLOCKED", "PRICE_UNAVAILABLE", "OVER_HARD_LIMIT",
])
def test_every_p2_3_reason_is_not_approvable(code):
    reason = REASON_REGISTRY[code]
    assert not reason.approvable
    assert "ASK" not in reason.allowedEffects
    assert reason.effect == ("BLOCK" if code == "OVER_HARD_LIMIT" else "EXCLUDE")


def test_legacy_findings_and_cutoff_copy():
    deposit = REASON_REGISTRY[ReasonCode.DEPOSIT_COST_UNVERIFIED]
    assert deposit.effect == "EXCLUDE" and not deposit.approvable
    variable = REASON_REGISTRY[ReasonCode.VARIABLE_WEIGHT_UNVERIFIED]
    assert variable.effect == "INFO" and not variable.approvable
    assert "1 kg per sales unit" in variable.template
    assert REASON_REGISTRY[ReasonCode.CUTOFF_PASSED].template == (
        "Order cut-off was {closingTime}, so {availableDate} delivery can't be promised. Re-quoting."
    )


def property_names(node):
    if isinstance(node, dict):
        yield from node.get("properties", {})
        for value in node.values():
            yield from property_names(value)
    elif isinstance(node, list):
        for value in node:
            yield from property_names(value)


@pytest.mark.parametrize("model", AGENT_MODELS)
def test_no_agent_facing_schema_contains_token_url_or_qr_fields(model):
    # Walk every inline definition too: a safe root cannot hide ApproverView in a nested model.
    for mode in ("validation", "serialization"):
        schema = model.model_json_schema(mode=mode)
        names = list(property_names(schema))
        assert not any(part in name.lower() for name in names for part in ("token", "url", "qr"))
        assert "ApproverView" not in schema.get("$defs", {})


@pytest.mark.parametrize("field", ["approvalToken", "approvalUrl", "qrPayload"])
def test_capability_fields_rejected_in_agent_response_and_input(field):
    data = mock("scenario-b.json")
    data[field] = "synthetic-capability"
    with pytest.raises(ValidationError, match="Extra inputs"):
        Proposal.model_validate(data)
    create = {
        "customerId": "mock-school", "offerId": "MOCK-O-B", "offerVersion": 1,
        "request": data["request"], field: "synthetic-capability",
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        CreateProposalRequest.model_validate(create)
    nested = mock("scenario-b.json")
    nested["lines"][0][field] = "synthetic-capability"
    with pytest.raises(ValidationError, match="Extra inputs"):
        Proposal.model_validate(nested)


def test_approver_view_is_the_only_credential_bearing_model():
    proposal = Proposal.model_validate(mock("scenario-b.json"))
    view = ApproverView(
        proposal=proposal, approver={"name": "Synthetic Head Chef", "role": "Head Chef (demo)"},
        approvalToken="synthetic-single-use-permission", expiresAt=proposal.validUntil,
    )
    assert_schema(ApproverView, view.model_dump(mode="json"))
    assert view.permissionNotice == "Demo approval link — single use"
    assert "approvalToken" not in proposal.model_dump_json()
    models_with_credentials = {
        name for name, schema in schemas().items()
        if any(part in field.lower() for field in property_names(schema) for part in ("token", "url", "qr"))
    }
    assert models_with_credentials == {"ApproverView"}


def test_audit_carries_version_and_facts_not_capabilities():
    proposal = Proposal.model_validate(mock("scenario-b.json"))
    entry = AuditEntry(
        entryId="MOCK-E1", customerId=proposal.customerId, proposalId=proposal.proposalId,
        offerId=proposal.offerId, offerVersion=proposal.offerVersion, offerHash=proposal.offerHash,
        mandateVersion=proposal.mandateVersion, timestamp=proposal.createdAt,
        actor="gateway:policy", event="POLICY_EVALUATED", fromState="PROPOSED",
        toState="PENDING_APPROVAL", reasons=proposal.reasons, rule="O3",
        facts=proposal.lines[0].facts, summary="Net total is above the automatic limit.",
    )
    trail = AuditTrail(entries=[entry])
    assert_schema(AuditTrail, trail.model_dump(mode="json"))


@pytest.mark.parametrize(("field", "value"), [
    ("offerVersion", 0), ("mandateVersion", 0), ("offerHash", "not-a-digest"),
    ("state", "COMMITTED"), ("decision", "LLM_APPROVED"),
    ("createdAt", "2026-11-13T21:50:00"),
])
def test_invalid_versions_enums_hashes_and_naive_times_are_rejected(field, value):
    data = mock("scenario-b.json")
    data[field] = value
    with pytest.raises(ValidationError):
        Proposal.model_validate(data)


@pytest.mark.parametrize("value", ["-0.01", "1.001", "NaN", "Infinity"])
def test_invalid_money_is_rejected(value):
    data = mock("scenario-b.json")
    data["totals"]["exclVat"] = value
    with pytest.raises(ValidationError):
        Proposal.model_validate(data)


def test_numeric_brief_inputs_emit_exact_decimal_strings():
    data = mock("scenario-b.json")
    data["totals"] = {"exclVat": 506.16, "vat": 68.33, "inclVat": 574.49, "currency": "EUR"}
    result = Proposal.model_validate(data)
    assert result.totals.exclVat == Decimal("506.16")
    assert result.model_dump(mode="json")["totals"]["exclVat"] == "506.16"


def test_exact_locale_independent_formatters():
    assert format_money(Decimal("1010")) == "€1,010.00"
    assert format_money(Decimal("506.16")) == "€506.16"
    assert format_date(date(2026, 11, 16)) == "Mon 16 Nov"
    assert format_date(date(2026, 1, 1)) == "Thu 1 Jan"
    assert format_time(datetime.fromisoformat("2026-11-15T17:00:00+02:00")) == "Sun 15 Nov 17:00"


def test_checked_in_schemas_and_types_are_current():
    exported = schemas()
    assert {path.stem for path in (ROOT / "spec/schemas").glob("*.json")} == set(exported)
    for name, schema in exported.items():
        Draft202012Validator.check_schema(schema)
        assert schema["x-contract-version"] == "0.1"
        assert json.loads((ROOT / f"spec/schemas/{name}.json").read_text()) == schema
    assert (ROOT / "frontend/src/lib/contracts.ts").read_text() == typescript(exported)
