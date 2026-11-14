"""T27: Valio Aimo readiness view (`backend/app/gateway/insights.py` + `GET /v0.1/insights`).

Verifies that every number — catalogue summary, data gaps by supplier, blocking effects on AUTO,
live AUTO/ASK/BLOCK statistics and the fix list — is computed from data, never hardcoded.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from synthetic.catalog_records import synthetic_product_record
from test_api import api, scenario_a, scenario_b, scenario_c  # noqa: F401  (api is a pytest fixture)

from app.catalog.build import DEFAULT_ARTIFACT_PATH, build_catalog_from_records, load_catalog
from app.contracts import ReasonCode
from app.gateway.insights import (
    ReadinessInsights,
    compute_readiness_insights,
)


def _build_synthetic_catalog():
    records = [
        # 1. Supplier A: AUTO-ready food product (Method 1 content + declared allergens)
        synthetic_product_record(
            product_id="SYN-READY-1",
            vendor_name="Supplier Alpha Oy",
            names={"en": "Alpha Lactose-Free Milk 1 l"},
            unit_conversions=[
                {
                    "synkkaPackagingType": "BASE_UNIT_OR_EACH",
                    "volume": {"value": 1.0, "unit": "LTR"},
                }
            ],
            allergens=[("AM", "CONTAINS")],
            non_allergens=[("AW", "FREE_FROM")],
            claims=["FREE_FROM_LACTOSE"],
        ),
        # 2. Supplier A: Food product with NO allergen declaration -> ALLERGEN_UNKNOWN (EXCLUDE)
        synthetic_product_record(
            product_id="SYN-NO-ALG-1",
            vendor_name="Supplier Alpha Oy",
            category="14100",
            names={"en": "Alpha Gluten-Free Fusilli 500 g"},
            unit_conversions=[
                {
                    "synkkaPackagingType": "BASE_UNIT_OR_EACH",
                    "netWeight": {"value": 0.5, "unit": "KGM"},
                }
            ],
            allergens=[],
            non_allergens=[],
            claims=["FREE_FROM_GLUTEN"],
        ),
        # 3. Supplier B: Product with name-only pack content -> QUANTITY_BASIS_WEAK (ASK)
        #    and lists SYN-BAD-SUB as a Valio substitute -> SUBSTITUTE_REJECTED (EXCLUDE)
        synthetic_product_record(
            product_id="SYN-NAME-WEAK-1",
            vendor_name="Supplier Beta Ab",
            names={"en": "Beta Gluten-Free Oat Drink 1 l"},
            unit_conversions=None,
            allergens=[],
            non_allergens=[("AW", "FREE_FROM")],
            claims=["FREE_FROM_GLUTEN", "VEGAN"],
            substitutions=["SYN-BAD-SUB"],
        ),
        # 4. Supplier B: Substitute that lacks FREE_FROM_GLUTEN and declares AW CONTAINS,
        #    plus has K1 conflict (claims FREE_FROM_LACTOSE + ML CONTAINS -> K2) and unpriced deposit
        synthetic_product_record(
            product_id="SYN-BAD-SUB",
            vendor_name="Supplier Beta Ab",
            names={"en": "Beta Regular Oat Drink 1 l"},
            unit_conversions=[
                {
                    "synkkaPackagingType": "BASE_UNIT_OR_EACH",
                    "volume": {"value": 1.0, "unit": "LTR"},
                }
            ],
            allergens=[("AW", "CONTAINS"), ("ML", "CONTAINS")],
            non_allergens=[],
            claims=["FREE_FROM_LACTOSE", "VEGAN"],
            deposits=[{"material": "DEP-01", "quantity": 1}],
        ),
        # 5. Supplier C: Non-food product with completely unknown pack content -> QUANTITY_BASIS_UNKNOWN
        synthetic_product_record(
            product_id="SYN-NONFOOD-UNK",
            vendor_name="Supplier Gamma NonFood",
            category="99900",
            names={"en": "Gamma Heavy Duty Napkin Box"},
            unit_conversions=None,
            allergens=[],
            non_allergens=[],
        ),
    ]
    artifact = build_catalog_from_records(
        records,
        reliability_map={"SYN-READY-1": 0.99, "SYN-NAME-WEAK-1": 0.91},
        co2_map={"SYN-READY-1": 1.15},
    )
    return records, artifact.products


def test_every_catalog_and_supplier_number_is_computed_from_data():
    records, products = _build_synthetic_catalog()
    insights = compute_readiness_insights(products=products, raw_records=records)
    summary = insights.catalogSummary

    assert summary.totalProducts == 5
    assert summary.totalSuppliers == 3
    assert summary.foodProducts == 4
    assert summary.nonFoodProducts == 1

    # Only SYN-READY-1 has zero AUTO-blocking gaps
    assert summary.autoReadyProducts == 1
    assert summary.blockedFromAutoProducts == 4
    assert summary.autoReadyRate == Decimal("0.2000")
    assert summary.foodAutoReadyProducts == 1
    assert summary.foodBlockedFromAutoProducts == 3
    assert summary.foodAutoReadyRate == Decimal("0.2500")

    # Exact gap counts
    assert summary.missingAllergensFood == 1  # SYN-NO-ALG-1
    assert summary.contentWeakNameEstimate == 1  # SYN-NAME-WEAK-1
    assert summary.contentUnknown == 1  # SYN-NONFOOD-UNK
    assert summary.dataConflicts == 1  # SYN-BAD-SUB (K2)
    assert summary.conflictsByCode == {"K2": 1}
    assert summary.unpricedDeposits == 1  # SYN-BAD-SUB
    assert summary.unsafeSubstitutes == 1  # SYN-NAME-WEAK-1 -> SYN-BAD-SUB
    assert summary.lowReliabilityUnder95 == 1  # SYN-NAME-WEAK-1 (0.91)
    assert summary.reliabilityKnown == 2
    assert summary.co2Known == 1

    # Supplier rows sum to the exact catalogue totals
    assert sum(s.totalProducts for s in insights.suppliers) == summary.totalProducts
    assert sum(s.foodProducts for s in insights.suppliers) == summary.foodProducts
    assert sum(s.nonFoodProducts for s in insights.suppliers) == summary.nonFoodProducts
    assert sum(s.autoReadyProducts for s in insights.suppliers) == summary.autoReadyProducts
    assert sum(s.blockedFromAutoProducts for s in insights.suppliers) == summary.blockedFromAutoProducts
    assert sum(s.missingAllergensFood for s in insights.suppliers) == summary.missingAllergensFood
    assert sum(s.contentUnknown for s in insights.suppliers) == summary.contentUnknown
    assert sum(s.contentWeakNameEstimate for s in insights.suppliers) == summary.contentWeakNameEstimate
    assert sum(s.dataConflicts for s in insights.suppliers) == summary.dataConflicts
    assert sum(s.unpricedDeposits for s in insights.suppliers) == summary.unpricedDeposits
    assert sum(s.unsafeSubstitutes for s in insights.suppliers) == summary.unsafeSubstitutes

    by_sup = {s.supplierName: s for s in insights.suppliers}
    alpha = by_sup["Supplier Alpha Oy"]
    assert (alpha.totalProducts, alpha.autoReadyProducts, alpha.blockedFromAutoProducts) == (2, 1, 1)
    assert alpha.blockingReasonCodes == [ReasonCode.ALLERGEN_UNKNOWN]

    beta = by_sup["Supplier Beta Ab"]
    assert (beta.totalProducts, beta.autoReadyProducts, beta.blockedFromAutoProducts) == (2, 0, 2)
    assert set(beta.blockingReasonCodes) == {
        ReasonCode.QUANTITY_BASIS_WEAK,
        ReasonCode.DATA_CONFLICT,
        ReasonCode.DEPOSIT_COST_UNVERIFIED,
        ReasonCode.SUBSTITUTE_REJECTED,
        ReasonCode.LOW_RELIABILITY,
    }

    # Fix list includes supplier-level and product-level fixes
    fix_codes = {f.reasonCode for f in insights.fixList}
    assert {
        ReasonCode.SUBSTITUTE_REJECTED,
        ReasonCode.DATA_CONFLICT,
        ReasonCode.ALLERGEN_UNKNOWN,
    } <= fix_codes

    # Prove numbers change when the input data changes (fixing SYN-NO-ALG-1's allergen declaration)
    records_fixed = [
        {
            **r,
            "synkkaData": {
                **r["synkkaData"],
                "classifications": [
                    *r["synkkaData"]["classifications"],
                    {"name": "nonAllergen", "type": "ENUM", "values": [{"id": "AW", "unit": "FREE_FROM"}]},
                ],
            },
        }
        if r["id"] == "SYN-NO-ALG-1"
        else r
        for r in records
    ]
    fixed_products = build_catalog_from_records(
        records_fixed,
        reliability_map={"SYN-READY-1": 0.99, "SYN-NAME-WEAK-1": 0.91},
    ).products
    insights_after = compute_readiness_insights(products=fixed_products, raw_records=records_fixed)
    assert insights_after.catalogSummary.missingAllergensFood == 0
    assert insights_after.catalogSummary.autoReadyProducts == 2
    assert insights_after.catalogSummary.blockedFromAutoProducts == 3


def test_get_insights_route_updates_live_auto_ask_block_statistics(api):  # noqa: F811
    client, _, _ = api

    # Before any proposals are created
    res0 = client.get("/v0.1/insights")
    assert res0.status_code == 200, res0.text
    initial = ReadinessInsights.model_validate(res0.json())
    assert initial.decisionStats.totalProposals == 0
    assert initial.decisionStats.byDecision == {"AUTO": 0, "ASK": 0, "BLOCK": 0}
    assert initial.decisionStats.totalOrderValueExclVat == Decimal("0.00")

    # Run Scenario A (AUTO €1,010.00), Scenario B (ASK €506.16) and Scenario C (BLOCK €20,200.00)
    scenario_a(client)
    scenario_b(client)
    scenario_c(client)

    res1 = client.get("/v0.1/insights")
    assert res1.status_code == 200, res1.text
    live = ReadinessInsights.model_validate(res1.json())
    stats = live.decisionStats

    assert stats.totalProposals == 3
    assert stats.activeProposals == 3
    assert stats.byDecision == {"AUTO": 1, "ASK": 1, "BLOCK": 1}
    assert stats.byDecisionActive == {"AUTO": 1, "ASK": 1, "BLOCK": 1}
    assert stats.confirmedSimulated == 1
    assert stats.pendingApproval == 1
    assert stats.blocked == 1
    assert stats.autoRate == Decimal("0.3333")
    assert stats.askRate == Decimal("0.3333")
    assert stats.blockRate == Decimal("0.3333")
    assert stats.autoValueExclVat == Decimal("1010.00")
    assert stats.askValueExclVat == Decimal("506.16")
    assert stats.blockValueExclVat == Decimal("20200.00")
    assert stats.totalOrderValueExclVat == Decimal("21716.16")

    reason_codes = {r.code: r.occurrences for r in stats.reasonCounts}
    assert reason_codes[ReasonCode.OVER_AUTO_LIMIT] == 1
    assert reason_codes[ReasonCode.OVER_HARD_LIMIT] == 1
    assert reason_codes[ReasonCode.INSTRUCTION_IGNORED] == 1

    excluded_codes = {r.code: r.occurrences for r in stats.excludedReasonCounts}
    assert excluded_codes.get(ReasonCode.SUBSTITUTE_REJECTED, 0) >= 1


@pytest.mark.skipif(not Path(DEFAULT_ARTIFACT_PATH).is_file(), reason="Catalogue artifact not built")
def test_real_catalog_artifact_supplier_sums_match_summary():
    artifact = load_catalog(DEFAULT_ARTIFACT_PATH)
    insights = compute_readiness_insights(products=artifact.products)
    summary = insights.catalogSummary

    assert summary.totalProducts == len(artifact.products)
    assert sum(s.totalProducts for s in insights.suppliers) == summary.totalProducts
    assert sum(s.autoReadyProducts for s in insights.suppliers) == summary.autoReadyProducts
    assert sum(s.blockedFromAutoProducts for s in insights.suppliers) == summary.blockedFromAutoProducts
    assert any(f.productId == "468465" and f.reasonCode == ReasonCode.SUBSTITUTE_REJECTED for f in insights.fixList)
