"""T23: Scenario B2 — partial-delivery proposal versions (patch P8 B2, P9 row 21, P2.2, P2.5).

Tests:
1. Eval row 21 on fixtures (reference fixtures in CI + recorded fixtures in `data/fixtures/` when present):
   - 474235 × 19 + 468465 × 60 = €505.68 (VAT €68.27, incl. VAT €573.95);
   - ASK with `[DATE_LATER, PARTIAL_AVAILABILITY, OVER_AUTO_LIMIT]`;
   - 473895 and 471108 excluded with `CUTOFF_PASSED`;
   - the approver sees "Approving means 95 kg instead of 120 kg, on Monday instead of today."
2. Public agent channel (`getOffer -> createProposal -> getProposal`) produces the partial-delivery
   proposal version directly from `offers.build_offer` without caller-supplied `partial_items` overrides.
3. End-to-end `run_agent` on Scenario B2 text ("Unexpected event: we need 120 kg of gluten-free macaroni
   and 60 litres of gluten-free oat drink today.") over both in-process and HTTP agent channels.
4. Separate proposal version & approval lifecycle:
   - single-use approval token bound to `proposalId + offerVersion + offerHash`;
   - approving the partial proposal version triggers `recheckAndConfirm` and confirms (`CONFIRMED_SIMULATED`)
     at 95 kg + 60 l = €505.68;
   - any change at re-check (e.g. stock of 474235 drops from 19 to 18 bags, or price changes) supersedes
     v1, voids v1's approval, and creates + decides a new partial proposal version v2 from scratch.
"""

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.agent.run import GatewayAgentChannel, HttpAgentChannel, run_agent
from app.api.state import GatewayState, get_state, try_state
from app.catalog.normalise import normalise_product
from app.contracts import (
    CreateProposalRequest,
    GetOfferRequest,
    OfferItem,
    Product,
    ReasonCode,
    RequestItem,
    Requirement,
    StructuredRequest,
)
from app.eval.rows import SCENARIO_ROWS
from app.eval.runner import (
    DEFAULT_FIXTURES_DIR,
    ReferenceFixtureProvider,
    build_reference_products,
    evaluate_row,
    find_latest_fixture_capture,
)
from app.gateway.approvals import PERMISSION_NOTICE, TokenAlreadyUsedError, TokenBindingMismatchError
from app.gateway.offers import build_offer, get_offer
from app.gateway.proposals import (
    ProposalGateway,
    decide_approval,
    format_approval_statement,
)
from app.main import app
from app.policy import DEMO_MANDATES, M2, MandateBook
from app.valio import RawBatch, RecordedValioProvider, normalise_batch

HEL = "+02:00"
NOW = datetime.fromisoformat(f"2026-11-13T21:52:02{HEL}")
FRIDAY_TODAY = date(2026, 11, 13)
MONDAY = date(2026, 11, 16)
UPSTREAM = "UPSTREAM-SYN"
GLUTEN_FREE = Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")

B2_REQUEST_TEXT = (
    "Unexpected event: we need 120 kg of gluten-free macaroni and 60 litres of gluten-free oat drink today."
)
EXPECTED_APPROVAL_STATEMENT = "Approving means 95 kg instead of 120 kg, on Monday instead of today."


def _build_b2_synthetic_products() -> dict[str, Product]:
    """Synthetic hand-made product records for Scenario B2 (patch P7 compliant)."""
    specs = [
        # 474235: gluten-free macaroni 5 kg bag (19 bags = 95 kg verified stock)
        {
            "id": "474235",
            "salesUnit": "ST",
            "baseUnit": "ST",
            "allowedLotSize": 1,
            "category": "21380",
            "deleted": False,
            "deposits": [],
            "substitutions": [],
            "units": [{"unitId": "ST", "sizeInBaseUnits": 1}],
            "vendorName": "Synthetic Pasta",
            "synkkaData": {
                "names": [{"language": "en", "value": "Synthetic SamMills Pasta D'oro 5kg macaroni glutenfree pasta"}],
                "unitConversions": [
                    {"synkkaPackagingType": "BASE_UNIT_OR_EACH", "netWeight": {"unit": "GRM", "value": 5000}}
                ],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "NM", "unit": "CONTAINS"}]},
                    {"name": "nonAllergen", "type": "ENUM", "values": [{"id": "AW", "unit": "FREE_FROM"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}, {"id": "VEGAN"}]},
                ],
            },
        },
        # 473895: Terminal stock with passed cut-off (12 Nov 14:00) -> CUTOFF_PASSED
        {
            "id": "473895",
            "salesUnit": "KI",
            "baseUnit": "ST",
            "allowedLotSize": 1,
            "category": "21380",
            "deleted": False,
            "deposits": [],
            "substitutions": [],
            "units": [{"unitId": "KI", "sizeInBaseUnits": 12}],
            "vendorName": "Synthetic Pasta",
            "synkkaData": {
                "names": [{"language": "en", "value": "Synthetic 12x Rummo glutenfree macaroni 400g"}],
                "classifications": [
                    {"name": "nonAllergen", "type": "ENUM", "values": [{"id": "AW", "unit": "FREE_FROM"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
        # 471108: Terminal stock with passed cut-off (12 Nov 14:00) -> CUTOFF_PASSED
        {
            "id": "471108",
            "salesUnit": "KI",
            "baseUnit": "ST",
            "allowedLotSize": 1,
            "category": "21380",
            "deleted": False,
            "deposits": [],
            "substitutions": [],
            "units": [{"unitId": "KI", "sizeInBaseUnits": 3}],
            "vendorName": "Synthetic Pasta",
            "synkkaData": {
                "names": [{"language": "en", "value": "Synthetic 3 x Rummo gluten free fusilli pasta 3kg"}],
                "classifications": [
                    {"name": "nonAllergen", "type": "ENUM", "values": [{"id": "AW", "unit": "FREE_FROM"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
        # 475795: Out of stock -> NOT_AVAILABLE
        {
            "id": "475795",
            "salesUnit": "PAK",
            "baseUnit": "PAK",
            "allowedLotSize": 1,
            "category": "21380",
            "deleted": False,
            "deposits": [],
            "substitutions": [],
            "units": [{"unitId": "PAK", "sizeInBaseUnits": 1}],
            "vendorName": "Synthetic Pasta",
            "synkkaData": {
                "names": [{"language": "en", "value": "Synthetic Semper Maccheroni gluten-free pasta 500g"}],
                "unitConversions": [
                    {"synkkaPackagingType": "BASE_UNIT_OR_EACH", "netWeight": {"unit": "GRM", "value": 500}}
                ],
                "classifications": [
                    {"name": "nonAllergen", "type": "ENUM", "values": [{"id": "AW", "unit": "FREE_FROM"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
        # 468465: gluten-free oat drink 1 l (plenty in stock)
        {
            "id": "468465",
            "salesUnit": "ST",
            "baseUnit": "ST",
            "allowedLotSize": 1,
            "category": "10402",
            "deleted": False,
            "deposits": [],
            "substitutions": [{"product": "468465", "substituteProduct": "470863"}],
            "units": [{"unitId": "ST", "sizeInBaseUnits": 1}],
            "vendorName": "Synthetic Oat",
            "synkkaData": {
                "names": [{"language": "en", "value": "Synthetic Oddlygood oat drink 1l UHT gluten free"}],
                "unitConversions": [
                    {"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}
                ],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "GO", "unit": "CONTAINS"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}, {"id": "VEGAN"}]},
                ],
            },
        },
        # 470863: Valio-listed substitute for 468465 that fails gluten-free -> SUBSTITUTE_REJECTED
        {
            "id": "470863",
            "salesUnit": "ST",
            "baseUnit": "ST",
            "allowedLotSize": 1,
            "category": "10402",
            "deleted": False,
            "deposits": [],
            "substitutions": [],
            "units": [{"unitId": "ST", "sizeInBaseUnits": 1}],
            "vendorName": "Synthetic Oat",
            "synkkaData": {
                "names": [{"language": "en", "value": "Synthetic Oatly Oat Drink Semi 1L"}],
                "unitConversions": [
                    {"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}
                ],
                "classifications": [
                    {
                        "name": "allergen",
                        "type": "ENUM",
                        "values": [{"id": "AW", "unit": "CONTAINS"}, {"id": "GO", "unit": "CONTAINS"}],
                    },
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "VEGAN"}]},
                ],
            },
        },
    ]
    return {rec["id"]: normalise_product(rec, reliability=1.0, co2=1.1) for rec in specs}


class MutableB2Provider:
    """In-memory Lookup provider for Scenario B2 whose stock and prices can be mutated before re-check."""

    def __init__(self, *, now: datetime = NOW, pasta_stock: int = 19, pasta_price: float = 22.92):
        self.now = now
        self.recorded_at = now
        self.pasta_stock = pasta_stock
        self.pasta_price = pasta_price
        self.calls: list[tuple[dict[str, list[str]], bool]] = []

    def _price_row(self, code: str) -> dict:
        prices = {
            "474235": (self.pasta_price, round(self.pasta_price / 5, 2), "KG"),
            "473895": (34.79, 7.25, "KG"),
            "471108": (28.50, 3.17, "KG"),
            "475795": (2.40, 4.80, "KG"),
            "468465": (1.17, 1.17, "L"),
            "470863": (1.55, 1.55, "L"),
        }
        val, comp, comp_u = prices.get(code, (1.00, 1.00, "L"))
        money = {
            "value": val,
            "valueWithTax": round(val * 1.135, 2),
            "currency": "EUR",
            "validFrom": f"2026-01-01T00:00:00{HEL}",
            "validTo": f"2026-12-31T23:59:59{HEL}",
        }
        comp_money = {**money, "value": comp, "valueWithTax": round(comp * 1.135, 2)}
        return {
            "productId": code,
            "quantity": 1,
            "catalogPrice": money,
            "listPrice": money,
            "comparisonPrice": comp_money,
            "comparisonUnit": comp_u,
            "tax": {"taxRate": 13.5, "taxValue": 0},
            "netPriceFound": True,
            "error": False,
        }

    def _stock_row(self, code: str) -> dict:
        if code == "474235":
            return {
                "productId": code,
                "closingTime": f"2026-11-15T17:00:00{HEL}",
                "salesBlock": False,
                "logisticModel": "Stock",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": self.pasta_stock,
                        "availableDate": MONDAY.isoformat(),
                        "salesUnit": "ST",
                    }
                ],
            }
        if code in ("473895", "471108"):
            return {
                "productId": code,
                "closingTime": f"2026-11-12T14:00:00{HEL}",
                "salesBlock": False,
                "logisticModel": "Terminal",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 999999,
                        "availableDate": MONDAY.isoformat(),
                        "salesUnit": "KI",
                    }
                ],
            }
        if code == "475795":
            return {
                "productId": code,
                "closingTime": f"2026-11-15T17:00:00{HEL}",
                "salesBlock": False,
                "logisticModel": "Stock",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 0,
                        "availableDate": None,
                        "salesUnit": "PAK",
                    }
                ],
            }
        return {
            "productId": code,
            "closingTime": f"2026-11-15T17:00:00{HEL}",
            "salesBlock": False,
            "logisticModel": "Stock",
            "scheduleLines": [
                {
                    "salesBlock": False,
                    "availableAmount": 9999999999,
                    "availableDate": MONDAY.isoformat(),
                    "salesUnit": "ST",
                }
            ],
        }

    def lookup(self, shortlists, requested_date, customer_id, *, products=None, force_refresh=False):
        self.calls.append((dict(shortlists), force_refresh))
        codes = tuple(sorted({code for grp in shortlists.values() for code in grp}))
        price_env = {
            "customerId": UPSTREAM,
            "currency": "EUR",
            "pricingDate": f"{requested_date.isoformat()}T00:00:00{HEL}",
            "products": [self._price_row(c) for c in codes],
        }
        stock_env = {
            "timeNow": self.now.isoformat(),
            "requestedDate": f"{requested_date.isoformat()}T00:00:00{HEL}",
            "products": [self._stock_row(c) for c in codes],
        }
        batch = RawBatch(
            customer_id,
            UPSTREAM,
            requested_date,
            codes,
            json.dumps(price_env).encode("utf-8"),
            json.dumps(stock_env).encode("utf-8"),
            self.now,
            self.recorded_at,
            "RECORDED",
            self.recorded_at,
        )
        return normalise_batch(batch, products)


def _b2_requests() -> tuple[GetOfferRequest, StructuredRequest]:
    offer_req = GetOfferRequest(
        customerId=M2.customerId,
        requestedDate=FRIDAY_TODAY,
        items=[
            OfferItem(itemId="I1", productId="474235", quantity=Decimal("120"), unit="kg"),
            OfferItem(itemId="I1", productId="473895", quantity=Decimal("120"), unit="kg"),
            OfferItem(itemId="I1", productId="471108", quantity=Decimal("120"), unit="kg"),
            OfferItem(itemId="I1", productId="475795", quantity=Decimal("120"), unit="kg"),
            OfferItem(itemId="I2", productId="468465", quantity=Decimal("60"), unit="l"),
        ],
    )
    struct_req = StructuredRequest(
        requestText=B2_REQUEST_TEXT,
        requestedDate=FRIDAY_TODAY,
        items=[
            RequestItem(
                itemId="I1",
                what="macaroni",
                quantity=Decimal("120"),
                unit="kg",
                requirements=[GLUTEN_FREE],
            ),
            RequestItem(
                itemId="I2",
                what="oat drink",
                quantity=Decimal("60"),
                unit="l",
                requirements=[GLUTEN_FREE],
            ),
        ],
        assumptions=[],
    )
    return offer_req, struct_req


# --------------------------------------------------------------------------- 1. Eval row 21 on fixtures


def test_eval_row_21_scenario_b2_on_reference_fixtures():
    """Eval row 21 on reference fixtures:
    - 474235 × 19 + 468465 × 60 = €505.68;
    - ASK with `[DATE_LATER, PARTIAL_AVAILABILITY, OVER_AUTO_LIMIT]`;
    - 473895 and 471108 excluded with CUTOFF_PASSED;
    - the approver sees "Approving means 95 kg instead of 120 kg, on Monday instead of today."
    """
    row = SCENARIO_ROWS["B2"]
    assert row.row_id == 21
    products = build_reference_products()
    provider = ReferenceFixtureProvider()

    result = evaluate_row(row, products=products, provider=provider)
    assert result.status == "PASSED", result.detail
    proposal = result.proposal
    assert proposal is not None

    assert proposal.decision == "ASK"
    assert proposal.state == "PENDING_APPROVAL"
    assert proposal.reasons == [
        ReasonCode.DATE_LATER,
        ReasonCode.PARTIAL_AVAILABILITY,
        ReasonCode.OVER_AUTO_LIMIT,
    ]
    assert [(ln.productId, ln.quantitySalesUnits, ln.deliveredQuantity.value, ln.lineTotalExclVat) for ln in proposal.lines] == [
        ("474235", Decimal("19"), Decimal("95"), Decimal("435.48")),
        ("468465", Decimal("60"), Decimal("60"), Decimal("70.20")),
    ]
    assert (proposal.totals.exclVat, proposal.totals.vat, proposal.totals.inclVat) == (
        Decimal("505.68"),
        Decimal("68.27"),
        Decimal("573.95"),
    )

    ex_by_id = {e.productId: e for e in proposal.excluded}
    for cut_id in ("473895", "471108"):
        assert cut_id in ex_by_id
        assert ReasonCode.CUTOFF_PASSED in ex_by_id[cut_id].reasons
    assert "475795" in ex_by_id and ReasonCode.NOT_AVAILABLE in ex_by_id["475795"].reasons

    assert format_approval_statement(proposal, mandate=M2, today=FRIDAY_TODAY) == EXPECTED_APPROVAL_STATEMENT


def test_eval_row_21_scenario_b2_on_recorded_fixtures_when_present():
    """Run Eval row 21 against real recorded fixtures in `data/fixtures/` when present on this machine."""
    capture = find_latest_fixture_capture(DEFAULT_FIXTURES_DIR)
    if capture is None:
        pytest.skip("data/fixtures/ not present on this machine")

    row = SCENARIO_ROWS["B2"]
    products = build_reference_products()
    provider = RecordedValioProvider(capture)
    result = evaluate_row(row, products=products, provider=provider)
    assert result.status == "PASSED", result.detail
    assert result.proposal is not None
    assert format_approval_statement(result.proposal, mandate=M2, today=FRIDAY_TODAY) == EXPECTED_APPROVAL_STATEMENT


# --------------------------------------------------------------------------- 2. Offers & Proposals gateway


def test_offers_and_proposals_produce_partial_delivery_version_without_manual_override():
    """`get_offer` + `create_proposal` automatically detect and bind the partial-delivery offer version."""
    products = _build_b2_synthetic_products()
    provider = MutableB2Provider()
    gateway = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer_req, struct_req = _b2_requests()

    offer_result = gateway.get_offer(offer_req)
    assert offer_result.partial_items == frozenset({"I1"})

    offer = offer_result.offer
    assert len(offer.lines) == 2
    pasta_line, oat_line = offer.lines
    assert (pasta_line.productId, pasta_line.quantitySalesUnits, pasta_line.deliveredQuantity.value) == (
        "474235",
        Decimal("19"),
        Decimal("95"),
    )
    assert ReasonCode.DATE_LATER in pasta_line.reasons
    assert ReasonCode.PARTIAL_AVAILABILITY in pasta_line.reasons
    cov_fact = next((f for f in pasta_line.facts if f.fact == "coverage.partial"), None)
    assert cov_fact is not None
    assert cov_fact.display == "Partial delivery: 95 kg of 120 kg on Mon 16 Nov"
    assert cov_fact.source.retrievedAt == NOW

    assert (oat_line.productId, oat_line.quantitySalesUnits, oat_line.deliveredQuantity.value) == (
        "468465",
        Decimal("60"),
        Decimal("60"),
    )
    assert oat_line.reasons == [ReasonCode.DATE_LATER]

    # Excluded in the offer already includes 473895 and 471108 with CUTOFF_PASSED, and 475795 with NOT_AVAILABLE
    offer_ex = {e.productId: e.reasons for e in offer.excluded}
    assert ReasonCode.CUTOFF_PASSED in offer_ex["473895"]
    assert ReasonCode.CUTOFF_PASSED in offer_ex["471108"]
    assert ReasonCode.NOT_AVAILABLE in offer_ex["475795"]

    # Call create_proposal with just CreateProposalRequest (no manual partial_items argument)
    proposal = gateway.create_proposal(
        CreateProposalRequest(
            customerId=M2.customerId,
            offerId=offer.offerId,
            offerVersion=offer.offerVersion,
            request=struct_req,
        )
    )
    assert proposal.decision == "ASK"
    assert proposal.state == "PENDING_APPROVAL"
    assert proposal.reasons == [
        ReasonCode.DATE_LATER,
        ReasonCode.PARTIAL_AVAILABILITY,
        ReasonCode.OVER_AUTO_LIMIT,
    ]
    assert proposal.totals.exclVat == Decimal("505.68")
    assert gateway.get_approval_statement(proposal.proposalId) == EXPECTED_APPROVAL_STATEMENT

    # Decision log POLICY_DECIDED entry carries the plain-language approver statement
    trail = gateway.get_audit_trail(proposal_id=proposal.proposalId)
    policy_entry = next(e for e in trail.entries if e.event == "POLICY_DECIDED")
    assert EXPECTED_APPROVAL_STATEMENT in policy_entry.summary


def test_partial_delivery_version_has_its_own_approval_and_supersedes_on_recheck_change():
    """P1.3 / P2.5 / P8 B2:
    - The partial-delivery proposal version gets its own single-use approval token.
    - If nothing changed at re-check, approving confirms 95 kg + 60 l at €505.68 (`CONFIRMED_SIMULATED`).
    - If stock drops before re-check (e.g. 474235 drops from 19 to 18 bags = 90 kg), v1 becomes `SUPERSEDED`,
      v1's approval is void, and a new partial-delivery proposal v2 (90 kg of 120 kg) is decided from scratch
      requiring its own approval.
    """
    products = _build_b2_synthetic_products()
    provider = MutableB2Provider()
    gateway = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer_req, struct_req = _b2_requests()

    # 1. Happy-path approval of partial-delivery proposal v1 -> re-check -> CONFIRMED_SIMULATED
    offer1 = gateway.get_offer(offer_req, offer_id="OF-B2-HAPPY").offer
    p1 = gateway.create_proposal(M2.customerId, offer1.offerId, offer1.offerVersion, struct_req)
    view1 = gateway.get_approver_view(p1.proposalId)
    assert view1.permissionNotice == PERMISSION_NOTICE
    assert view1.approvalStatement == EXPECTED_APPROVAL_STATEMENT
    assert format_approval_statement(view1.proposal, mandate=M2) == EXPECTED_APPROVAL_STATEMENT

    confirmed1 = decide_approval(gateway, p1.proposalId, p1.offerVersion, view1.approvalToken, "APPROVE")
    assert confirmed1.state == "CONFIRMED_SIMULATED"
    assert confirmed1.decision == "ASK"
    assert confirmed1.totals.exclVat == Decimal("505.68")
    with pytest.raises(TokenAlreadyUsedError):
        decide_approval(gateway, p1.proposalId, p1.offerVersion, view1.approvalToken, "APPROVE")

    # 2. Stock changes before re-check (19 bags -> 18 bags = 90 kg) -> SUPERSEDED, v2 created from scratch.
    # An independent order: a fresh gateway, so the €505.68 confirmed above does not count against it (T62).
    gateway = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer2 = gateway.get_offer(offer_req, offer_id="OF-B2-SUPERSEDE").offer
    v1 = gateway.create_proposal(M2.customerId, offer2.offerId, offer2.offerVersion, struct_req)
    view_v1 = gateway.get_approver_view(v1.proposalId)

    provider.pasta_stock = 18  # 18 * 5 kg = 90 kg (< 95 kg promised in v1)
    superseded = decide_approval(gateway, v1.proposalId, v1.offerVersion, view_v1.approvalToken, "APPROVE")
    assert superseded.state == "SUPERSEDED"
    assert superseded.supersededBy is not None
    assert not gateway.approvals.is_approval_valid(v1.proposalId)

    v2 = gateway.get_proposal(superseded.supersededBy)
    assert v2.offerVersion == 2
    assert v2.state == "PENDING_APPROVAL"
    assert v2.decision == "ASK"
    assert v2.reasons == [ReasonCode.DATE_LATER, ReasonCode.PARTIAL_AVAILABILITY]
    # 18 * 22.92 = 412.56 + 60 * 1.17 = 70.20 -> €482.76 (now <= €500 auto-approve limit, but still ASK for DATE_LATER + PARTIAL_AVAILABILITY)
    assert v2.totals.exclVat == Decimal("482.76")
    assert v2.lines[0].quantitySalesUnits == Decimal("18")
    assert v2.lines[0].deliveredQuantity.value == Decimal("90")
    assert (
         gateway.get_approval_statement(v2.proposalId)
        == "Approving means 90 kg instead of 120 kg, on Monday instead of today."
    )

    # Old v1 token cannot approve v2; v2 requires its own single-use approval token
    with pytest.raises(TokenBindingMismatchError):
        decide_approval(gateway, v2.proposalId, v2.offerVersion, view_v1.approvalToken, "APPROVE")
    view_v2 = gateway.get_approver_view(v2.proposalId)
    assert view_v2.approvalStatement == "Approving means 90 kg instead of 120 kg, on Monday instead of today."
    confirmed_v2 = decide_approval(gateway, v2.proposalId, v2.offerVersion, view_v2.approvalToken, "APPROVE")
    assert confirmed_v2.state == "CONFIRMED_SIMULATED"


# --------------------------------------------------------------------------- 3. End-to-end Agent Run (REST & in-process)


def test_agent_run_scenario_b2_end_to_end_over_http_and_in_process():
    """Run Scenario B2 through `run_agent` over both `GatewayAgentChannel` and `HttpAgentChannel`."""
    products = _build_b2_synthetic_products()
    provider = MutableB2Provider()
    gateway = ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    state = GatewayState(
        gateway=gateway,
        catalog=list(products.values()),
        mode="RECORDED",
        clock=lambda: NOW,
    )
    app.dependency_overrides[get_state] = lambda: state
    app.dependency_overrides[try_state] = lambda: state
    try:
        with TestClient(app) as client:
            in_proc = GatewayAgentChannel(state)
            http_chan = HttpAgentChannel(client, clock=lambda: NOW)

            res_proc = run_agent(M2.customerId, B2_REQUEST_TEXT, channel=in_proc, now=NOW)
            res_http = run_agent(M2.customerId, B2_REQUEST_TEXT, channel=http_chan, now=NOW)

            for res in (res_proc, res_http):
                assert [s.name for s in res.steps] == ["REQUEST", "LOOKUP", "ORDER", "APPROVAL"]
                assert res.steps[2].summary == "Proposal at fixed price · offer v1 · €505.68 excl. VAT"
                assert res.steps[3].summary == "Waiting for approver"
                assert res.proposal is not None
                prop = res.proposal
                assert prop.decision == "ASK"
                assert prop.state == "PENDING_APPROVAL"
                assert prop.reasons == [
                    ReasonCode.DATE_LATER,
                    ReasonCode.PARTIAL_AVAILABILITY,
                    ReasonCode.OVER_AUTO_LIMIT,
                ]
                assert (prop.totals.exclVat, prop.totals.vat, prop.totals.inclVat) == (
                    Decimal("505.68"),
                    Decimal("68.27"),
                    Decimal("573.95"),
                )
                assert [(ln.productId, ln.quantitySalesUnits, ln.deliveredQuantity.value, ln.lineTotalExclVat) for ln in prop.lines] == [
                    ("474235", Decimal("19"), Decimal("95"), Decimal("435.48")),
                    ("468465", Decimal("60"), Decimal("60"), Decimal("70.20")),
                ]
                ex_by_id = {e.productId: e for e in prop.excluded}
                assert "473895" in ex_by_id and ReasonCode.CUTOFF_PASSED in ex_by_id["473895"].reasons
                assert "471108" in ex_by_id and ReasonCode.CUTOFF_PASSED in ex_by_id["471108"].reasons
                assert format_approval_statement(prop, mandate=M2) == EXPECTED_APPROVAL_STATEMENT
                assert "approvalStatement" not in prop.model_dump_json()

            view_resp = client.get(f"/v0.1/approver/proposals/{res_http.proposal.proposalId}")
            assert view_resp.status_code == 200
            view_json = view_resp.json()
            assert view_json["approvalStatement"] == EXPECTED_APPROVAL_STATEMENT

            assert res_proc.proposal.offerHash == res_http.proposal.offerHash
    finally:
        app.dependency_overrides.clear()


def test_build_offer_records_trailing_cutoff_passed_candidates_when_first_candidate_is_partial():
    """Verify `offers.build_offer` directly: when the first candidate (474235) covers 95 of 120 kg,
    trailing candidates (473895, 471108) with passed cut-offs are evaluated and recorded in `offer.excluded`
    with `CUTOFF_PASSED`, and the resulting offer carries `partial_items == frozenset({'I1'})`.
    """
    products = _build_b2_synthetic_products()
    provider = MutableB2Provider()
    offer_req, _ = _b2_requests()

    res = get_offer(offer_req, products=products, provider=provider, mandate=M2)
    assert res.partial_items == frozenset({"I1"})
    assert [(ln.productId, ln.quantitySalesUnits, ln.lineTotalExclVat) for ln in res.offer.lines] == [
        ("474235", Decimal("19"), Decimal("435.48")),
        ("468465", Decimal("60"), Decimal("70.20")),
    ]
    assert res.offer.totals.exclVat == Decimal("505.68")

    # Verify build_offer with candidates in both orders (474235 first or 473895/471108 first)
    ex_by_id = {e.productId: e.reasons for e in res.offer.excluded}
    assert ReasonCode.CUTOFF_PASSED in ex_by_id["473895"]
    assert ReasonCode.CUTOFF_PASSED in ex_by_id["471108"]
    assert ReasonCode.NOT_AVAILABLE in ex_by_id["475795"]

    snap = provider.lookup({"I1": ["474235"], "I2": ["468465"]}, FRIDAY_TODAY, M2.customerId, products=products)
    single_res = build_offer(
        GetOfferRequest(
            customerId=M2.customerId,
            requestedDate=FRIDAY_TODAY,
            items=[
                OfferItem(itemId="I1", productId="474235", quantity=Decimal("120"), unit="kg"),
                OfferItem(itemId="I2", productId="468465", quantity=Decimal("60"), unit="l"),
            ],
        ),
        products=products,
        snapshot=snap,
        mandate=M2,
    )
    assert single_res.partial_items == frozenset({"I1"})
    assert ReasonCode.PARTIAL_AVAILABILITY in single_res.offer.lines[0].reasons
