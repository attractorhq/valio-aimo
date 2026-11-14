"""Evaluation runner for the 24-row evaluation suite (P5.1, P9, AC10)."""

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from app.catalog.build import DEFAULT_ARTIFACT_PATH, DEFAULT_RAW_DIR, load_catalog
from app.catalog.normalise import normalise_product
from app.catalog.search import evaluate_product_static_requirements
from app.contracts import (
    ExcludedProduct,
    GetOfferRequest,
    OfferItem,
    Product,
    Proposal,
    ReasonCode,
    StructuredRequest,
)
from app.eval.rows import EVAL_ROWS, EvalRow
from app.gateway.offers import Lookup
from app.gateway.proposals import ProposalGateway, _merge_excluded
from app.policy import DEMO_MANDATES, MandateBook
from app.valio import RawBatch, RecordedValioProvider, ValioSnapshot, normalise_batch

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURES_DIR = ROOT / "data/fixtures"
FROZEN_NOW = datetime.fromisoformat("2026-11-13T21:52:02+02:00")


@dataclass(frozen=True)
class EvalRowResult:
    row_id: int
    scenario_id: str | None
    mandate_id: str
    status: Literal["PASSED", "FAILED", "PENDING"]
    expected_decision: str | None
    actual_decision: str | None
    expected_reasons: tuple[str, ...]
    actual_reasons: tuple[str, ...]
    expected_total_excl_vat: str | None
    actual_total_excl_vat: str | None
    detail: str
    proposal: Proposal | None = None


@dataclass(frozen=True)
class EvalSuiteReport:
    source: Literal["RECORDED_FIXTURES", "REFERENCE_FIXTURES"]
    fixture_dir: str | None
    recorded_at: str | None
    total_rows: int
    active_rows: int
    passed_rows: int
    failed_rows: int
    pending_rows: int
    score_display: str
    results: tuple[EvalRowResult, ...]


class PriceOverlayProvider:
    """Wraps a Lookup provider and overrides unit prices for specific product IDs (for Eval row 23)."""

    def __init__(self, base: Lookup, price_overrides: dict[str, Decimal]):
        self.base = base
        self.price_overrides = dict(price_overrides)

    def lookup(self, shortlists, requested_date, customer_id, *, products=None, force_refresh=False) -> ValioSnapshot:
        snap = self.base.lookup(
            shortlists,
            requested_date,
            customer_id,
            products=products,
            force_refresh=force_refresh,
        )
        if not self.price_overrides:
            return snap
        from dataclasses import replace

        updated_products = dict(snap.products)
        for pid, new_price in self.price_overrides.items():
            if pid in updated_products:
                pf = updated_products[pid]
                new_pf_price = replace(pf.price, unit_price=new_price)
                updated_products[pid] = replace(pf, price=new_pf_price)
        return replace(snap, products=updated_products)


class ReferenceFixtureProvider:
    """Deterministic reference fixture provider matching the documented values in brief §8.5/§8.6 and patch P8/P9.

    Used when `data/fixtures/` is not present on a developer machine or CI; `RecordedValioProvider` is used
    automatically whenever `data/fixtures/<capture-ts>/manifest.json` exists.
    """

    def __init__(self, now: datetime = FROZEN_NOW):
        self.now = now
        self.recorded_at = now

    def lookup(self, shortlists, requested_date: date, customer_id: str, *, products=None, force_refresh=False) -> ValioSnapshot:
        del force_refresh
        codes = tuple(sorted({code for group in shortlists.values() for code in group}))
        price_rows = [self._price_row(c) for c in codes]
        stock_rows = [self._stock_row(c) for c in codes]
        price_env = {
            "customerId": customer_id,
            "currency": "EUR",
            "pricingDate": f"{requested_date.isoformat()}T00:00:00+02:00",
            "products": price_rows,
        }
        stock_env = {
            "timeNow": self.now.isoformat(),
            "requestedDate": f"{requested_date.isoformat()}T00:00:00+02:00",
            "products": stock_rows,
        }
        batch = RawBatch(
            customer_id,
            customer_id,
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

    @staticmethod
    def _price_row(code: str) -> dict:
        specs: dict[str, tuple[float, float, float]] = {
            # code: (catalogPrice, listPrice, vatRate)
            "464306": (1.01, 1.44, 13.5),
            "465039": (5.05, 6.20, 13.5),
            "474235": (22.92, 22.92, 13.5),
            "468465": (1.17, 1.50, 13.5),
            "473895": (34.79, 34.79, 13.5),
            "471108": (28.50, 28.50, 13.5),
            "475795": (2.40, 2.40, 13.5),
            "469040": (12.50, 12.50, 13.5),
            "474136": (8.40, 8.40, 13.5),
            "470699": (1.95, 1.95, 13.5),
            "460302": (21.60, 21.60, 13.5),
            "460825": (1.65, 1.65, 13.5),
            "470863": (1.55, 1.55, 13.5),
            "471881": (6.90, 6.90, 13.5),
        }
        cat_val, list_val, vat = specs.get(code, (1.00, 1.00, 13.5))
        return {
            "productId": code,
            "quantity": 1,
            "catalogPrice": {
                "value": cat_val,
                "valueWithTax": round(cat_val * (1 + vat / 100), 2),
                "currency": "EUR",
                "validFrom": "2026-01-01T00:00:00+02:00",
                "validTo": "2026-12-31T23:59:59+02:00",
            },
            "listPrice": {
                "value": list_val,
                "valueWithTax": round(list_val * (1 + vat / 100), 2),
                "currency": "EUR",
                "validFrom": "2026-01-01T00:00:00+02:00",
                "validTo": "2026-12-31T23:59:59+02:00",
            },
            "tax": {"taxRate": vat, "taxValue": round(cat_val * vat / 100, 2)},
            "netPriceFound": cat_val < list_val,
            "error": False,
        }

    @staticmethod
    def _stock_row(code: str) -> dict:
        # Stock facts per patch P8 / P9
        if code in ("464306", "468465", "460825", "470863"):
            return {
                "productId": code,
                "closingTime": "2026-11-15T17:00:00+02:00",
                "salesBlock": False,
                "logisticModel": "Stock",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 9999999999,
                        "availableDate": "2026-11-16",
                        "salesUnit": "ST",
                    }
                ],
            }
        if code == "474235":
            return {
                "productId": code,
                "closingTime": "2026-11-15T17:00:00+02:00",
                "salesBlock": False,
                "logisticModel": "Stock",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 19,
                        "availableDate": "2026-11-16",
                        "salesUnit": "ST",
                    }
                ],
            }
        if code in ("475795", "471881"):
            return {
                "productId": code,
                "closingTime": "2026-11-15T17:00:00+02:00",
                "salesBlock": False,
                "logisticModel": "Stock",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 0,
                        "availableDate": None,
                        "salesUnit": "PAK" if code == "475795" else "KI",
                    }
                ],
            }
        if code in ("465039", "473895", "471108", "469040", "474136", "460302"):
            closing = "2026-11-12T15:00:00+02:00" if code == "465039" else "2026-11-12T14:00:00+02:00"
            return {
                "productId": code,
                "closingTime": closing,
                "salesBlock": False,
                "logisticModel": "Terminal",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 999999,
                        "availableDate": "2026-11-16",
                        "salesUnit": "KI",
                    }
                ],
            }
        return {
            "productId": code,
            "closingTime": "2026-11-15T17:00:00+02:00",
            "salesBlock": False,
            "logisticModel": "Stock",
            "scheduleLines": [
                {
                    "salesBlock": False,
                    "availableAmount": 100,
                    "availableDate": "2026-11-16",
                    "salesUnit": "ST",
                }
            ],
        }


def build_reference_products() -> dict[str, Product]:
    """Load real catalogue products when present, or build synthetic reference products for the brief IDs."""
    if DEFAULT_ARTIFACT_PATH.exists() or (DEFAULT_RAW_DIR / "products.json").exists():
        return load_catalog().by_id()

    raw_specs = [
        {
            "id": "464306",
            "salesUnit": "ST",
            "baseUnit": "ST",
            "allowedLotSize": 1,
            "category": "10400",
            "deleted": False,
            "deposits": [],
            "substitutions": [{"product": "464306", "substituteProduct": "465039"}],
            "units": [{"unitId": "ST", "sizeInBaseUnits": 1}],
            "vendorName": "Synthetic Dairy",
            "synkkaData": {
                "names": [{"language": "en", "value": "Valio Arki Eila semi skimmed milk drink 1 l lactose free"}],
                "unitConversions": [{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "AM", "unit": "CONTAINS"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_LACTOSE"}]},
                ],
            },
        },
        {
            "id": "465039",
            "salesUnit": "KI",
            "baseUnit": "ST",
            "allowedLotSize": 1,
            "category": "10400",
            "deleted": False,
            "deposits": [],
            "substitutions": [],
            "units": [{"unitId": "KI", "sizeInBaseUnits": 5}],
            "vendorName": "Synthetic Dairy",
            "synkkaData": {
                "names": [{"language": "en", "value": "Valio Eila semi skimmed milk drink 1 l lactose free"}],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "AM", "unit": "CONTAINS"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_LACTOSE"}]},
                ],
            },
        },
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
                "names": [{"language": "en", "value": "SamMills Pasta D'oro 5kg macaroni glutenfree pasta"}],
                "unitConversions": [{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "netWeight": {"unit": "GRM", "value": 5000}}],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "NM", "unit": "CONTAINS"}]},
                    {"name": "nonAllergen", "type": "ENUM", "values": [{"id": "AW", "unit": "FREE_FROM"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}, {"id": "VEGAN"}]},
                ],
            },
        },
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
                "names": [{"language": "en", "value": "Oddlygood oat drink 1l UHT gluten free"}],
                "unitConversions": [{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "GO", "unit": "CONTAINS"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}, {"id": "VEGAN"}]},
                ],
            },
        },
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
                "names": [{"language": "en", "value": "Oatly Oat Drink Semi 1L"}],
                "unitConversions": [{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "AW", "unit": "CONTAINS"}, {"id": "GO", "unit": "CONTAINS"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "VEGAN"}]},
                ],
            },
        },
        {
            "id": "460825",
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
                "names": [{"language": "en", "value": "Oatly Oat Drink Organic 1L"}],
                "unitConversions": [{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "AW", "unit": "CONTAINS"}, {"id": "GO", "unit": "CONTAINS"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "VEGAN"}]},
                ],
            },
        },
        {
            "id": "471881",
            "salesUnit": "KI",
            "baseUnit": "ST",
            "allowedLotSize": 1,
            "category": "10402",
            "deleted": False,
            "deposits": [],
            "substitutions": [],
            "units": [{"unitId": "KI", "sizeInBaseUnits": 10}],
            "vendorName": "Synthetic Oat",
            "synkkaData": {
                "names": [{"language": "en", "value": "Oddlygood Barista oat drink 0,5 l UHT gluten free"}],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "AW", "unit": "CONTAINS"}, {"id": "GO", "unit": "CONTAINS"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
        {
            "id": "469040",
            "salesUnit": "KI",
            "baseUnit": "ST",
            "allowedLotSize": 1,
            "category": "10402",
            "deleted": False,
            "deposits": [],
            "substitutions": [],
            "units": [{"unitId": "KI", "sizeInBaseUnits": 10}],
            "vendorName": "Synthetic Oat",
            "synkkaData": {
                "names": [{"language": "en", "value": "10 x Elovena 1l oat drink 1,5%"}],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "GO", "unit": "CONTAINS"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
        {
            "id": "474136",
            "salesUnit": "KI",
            "baseUnit": "ST",
            "allowedLotSize": 1,
            "category": "10402",
            "deleted": False,
            "deposits": [],
            "substitutions": [],
            "units": [{"unitId": "KI", "sizeInBaseUnits": 6}],
            "vendorName": "Synthetic Oat",
            "synkkaData": {
                "names": [{"language": "en", "value": "6 x Elovena 1l fresh oat drink ESL"}],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "GO", "unit": "CONTAINS"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
        {
            "id": "470699",
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
                "names": [{"language": "en", "value": "URTEKRAM Organic Gluten-free Fusilli 250g"}],
                "unitConversions": [{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "netWeight": {"unit": "GRM", "value": 250}}],
                "classifications": [
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
        {
            "id": "460302",
            "salesUnit": "KI",
            "baseUnit": "BAG",
            "allowedLotSize": 1,
            "category": "21380",
            "deleted": False,
            "deposits": [],
            "substitutions": [],
            "units": [{"unitId": "KI", "sizeInBaseUnits": 12}],
            "vendorName": "Synthetic Pasta",
            "synkkaData": {
                "names": [{"language": "en", "value": "12x URTEKRAM organic Penne Gluten Free 250g"}],
                "classifications": [
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
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
                "names": [{"language": "en", "value": "Semper Maccheroni gluten-free pasta 500g"}],
                "unitConversions": [{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "netWeight": {"unit": "GRM", "value": 500}}],
                "classifications": [
                    {"name": "allergen", "type": "ENUM", "values": [{"id": "AY", "unit": "CONTAINS"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
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
                "names": [{"language": "en", "value": "12x Rummo glutenfree macaroni 400g"}],
                "classifications": [
                    {"name": "nonAllergen", "type": "ENUM", "values": [{"id": "AW", "unit": "FREE_FROM"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
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
                "names": [{"language": "en", "value": "3 x Rummo gluten free fusilli pasta 3kg"}],
                "classifications": [
                    {"name": "nonAllergen", "type": "ENUM", "values": [{"id": "AW", "unit": "FREE_FROM"}]},
                    {"name": "nutritionalClaim", "type": "ENUM", "values": [{"id": "FREE_FROM_GLUTEN"}]},
                ],
            },
        },
    ]
    return {rec["id"]: normalise_product(rec) for rec in raw_specs}


def find_latest_fixture_capture(fixtures_dir: Path = DEFAULT_FIXTURES_DIR) -> Path | None:
    if not fixtures_dir.exists():
        return None
    if (fixtures_dir / "manifest.json").is_file():
        return fixtures_dir
    candidates = sorted(
        (d for d in fixtures_dir.iterdir() if d.is_dir() and (d / "manifest.json").is_file()),
        key=lambda p: p.name,
    )
    return candidates[-1] if candidates else None


def execute_row_proposal(
    row: EvalRow,
    *,
    products: dict[str, Product],
    provider: Lookup,
    gateway: ProposalGateway | None = None,
) -> tuple[ProposalGateway, Proposal]:
    """Execute one EvalRow through static shortlist filtering + `ProposalGateway` (`getOffer -> createProposal`)."""
    gw = gateway or ProposalGateway(products=products, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    mandate = gw.mandates.get(row.customer_id)

    offer_items: list[OfferItem] = []
    static_excluded: list[ExcludedProduct] = []

    for item in row.items:
        codes = (row.shortlists or {}).get(item.itemId, ())
        for code in codes:
            prod = products.get(code)
            if prod is None:
                continue
            named = item.productId is not None and item.productId == code
            reasons, facts = evaluate_product_static_requirements(
                prod,
                item.requirements,
                mandate,
                named=named,
                item_id=item.itemId,
            )
            if reasons:
                static_excluded.append(
                    ExcludedProduct(
                        productId=prod.productId,
                        name=prod.name,
                        itemId=item.itemId,
                        reasons=reasons,
                        facts=facts,
                    )
                )
            else:
                offer_items.append(
                    OfferItem(
                        itemId=item.itemId,
                        productId=code,
                        quantity=item.quantity,
                        unit=item.unit,
                    )
                )

    offer_req = GetOfferRequest(
        customerId=row.customer_id,
        requestedDate=row.requested_date,
        items=offer_items,
    )
    offer_result = gw.get_offer(offer_req)

    # Also evaluate remaining shortlist candidates against the fetched snapshot so out-of-stock / passed-cutoff
    # candidates on the shortlist appear in excluded[] (Patch P8 B).
    chosen_ids = {ln.productId for ln in offer_result.offer.lines}
    already_ex_ids = {e.productId for e in offer_result.offer.excluded}
    extra_live_excluded: list[ExcludedProduct] = []
    lists_by_item = {item.itemId: [oi.productId for oi in offer_items if oi.itemId == item.itemId] for item in row.items}
    wanted_codes = {c for grp in lists_by_item.values() for c in grp}
    snap = provider.lookup(
        lists_by_item,
        row.requested_date,
        row.customer_id,
        products={c: products[c] for c in wanted_codes if c in products},
    )
    from app.gateway.offers import _line_input, size
    from app.policy import evaluate_line

    for item in row.items:
        for code in lists_by_item.get(item.itemId, ()):
            if code in chosen_ids or code in already_ex_ids:
                continue
            prod = products.get(code)
            pf = snap.products.get(code)
            if prod is None or pf is None:
                continue
            sizing = size(prod, item.quantity, item.unit)
            probe = _line_input("L-EX", item.itemId, prod, pf, sizing)
            verdict = evaluate_line(
                probe,
                item=item,
                mandate=mandate,
                requested_date=row.requested_date,
                now=snap.now,
            )
            if verdict.status == "EXCLUDED":
                extra_live_excluded.append(
                    ExcludedProduct(
                        productId=prod.productId,
                        name=prod.name,
                        itemId=item.itemId,
                        reasons=list(verdict.exclusion_codes),
                        facts=[*pf.facts, *prod.facts],
                    )
                )

    # Detect partial items automatically if delivered quantity < requested quantity
    delivered_by_item: dict[str, Decimal] = {}
    for ln in offer_result.offer.lines:
        delivered_by_item[ln.itemId] = delivered_by_item.get(ln.itemId, Decimal(0)) + ln.deliveredQuantity.value
    inferred_partial = {
        item.itemId
        for item in row.items
        if delivered_by_item.get(item.itemId, Decimal(0)) < item.quantity
    }
    partial_items = frozenset(row.partial_items | inferred_partial)

    structured_req = StructuredRequest(
        requestText=row.request_text,
        requestedDate=row.requested_date,
        items=list(row.items),
        assumptions=[],
    )
    proposal = gw.create_proposal(
        row.customer_id,
        offer_result.offer.offerId,
        offer_result.offer.offerVersion,
        structured_req,
        partial_items=partial_items,
        instruction_ignored=row.instruction_ignored,
    )
    if static_excluded or extra_live_excluded:
        # Put proposal.excluded first so Valio-substitute entries (with substituteFor set) win deduplication.
        merged = _merge_excluded(proposal.excluded, extra_live_excluded, static_excluded)
        proposal = proposal.model_copy(update={"excluded": merged})
        gw._save_proposal(proposal)
    return gw, proposal


def evaluate_row(
    row: EvalRow,
    *,
    products: dict[str, Product],
    provider: Lookup,
) -> EvalRowResult:
    if row.status != "ACTIVE":
        return EvalRowResult(
            row_id=row.row_id,
            scenario_id=row.scenario_id,
            mandate_id=row.mandate_id,
            status="PENDING",
            expected_decision=row.expected_decision,
            actual_decision=None,
            expected_reasons=tuple(str(r) for r in row.expected_reasons),
            actual_reasons=(),
            expected_total_excl_vat=format(row.expected_total_excl_vat, ".2f") if row.expected_total_excl_vat else None,
            actual_total_excl_vat=None,
            detail=row.notes or f"Pending ({row.status})",
        )

    gw, proposal = execute_row_proposal(row, products=products, provider=provider)
    mismatches: list[str] = []

    if row.special_check == "ROW_23_PRICE_CHANGE_SUPERSEDED":
        view = gw.get_approver_view(proposal.proposalId)
        gw.provider = PriceOverlayProvider(provider, {"474235": Decimal("22.93")})
        v1_after = gw.decide_approval(
            proposal.proposalId,
            proposal.offerVersion,
            view.approvalToken,
            "APPROVE",
        )
        if v1_after.state != "SUPERSEDED" or not v1_after.supersededBy:
            mismatches.append(f"v1 state={v1_after.state} supersededBy={v1_after.supersededBy}")
            v2 = v1_after
        else:
            v2 = gw.get_proposal(v1_after.supersededBy)
            if v2.offerVersion != 2 or v2.state != "PENDING_APPROVAL":
                mismatches.append(f"v2 offerVersion={v2.offerVersion} state={v2.state}")
        proposal = v2

    elif row.special_check == "ROW_22_NO_CREDENTIALS":
        view = gw.get_approver_view(proposal.proposalId)
        dumped = proposal.model_dump_json()
        lower = dumped.lower()
        if view.approvalToken in dumped or any(k in lower for k in ("approvaltoken", "approvalurl", "qrpayload")):
            mismatches.append("Agent-facing Proposal JSON leaked an approval capability field or token")

    elif row.special_check == "ROW_24_NEVER_APPROVE_ALLERGEN_UNKNOWN":
        ex_by_id = {e.productId: e for e in proposal.excluded}
        if "470699" not in ex_by_id or ReasonCode.ALLERGEN_UNKNOWN not in ex_by_id["470699"].reasons:
            mismatches.append("470699 was not excluded with ALLERGEN_UNKNOWN")
        if ReasonCode.ALLERGEN_UNKNOWN in proposal.reasons:
            mismatches.append("ALLERGEN_UNKNOWN appeared in proposal.reasons (must never be offered for approval)")

    if row.expected_decision and proposal.decision != row.expected_decision:
        mismatches.append(f"decision expected {row.expected_decision}, got {proposal.decision}")
    if tuple(proposal.reasons) != row.expected_reasons:
        mismatches.append(
            f"reasons expected {[str(r) for r in row.expected_reasons]}, got {[str(r) for r in proposal.reasons]}"
        )
    if row.expected_total_excl_vat is not None and proposal.totals.exclVat != row.expected_total_excl_vat:
        mismatches.append(
            f"total excl. VAT expected {row.expected_total_excl_vat}, got {proposal.totals.exclVat}"
        )
    if row.expected_lines:
        if len(proposal.lines) != len(row.expected_lines):
            mismatches.append(f"line count expected {len(row.expected_lines)}, got {len(proposal.lines)}")
        else:
            for exp_ln, act_ln in zip(row.expected_lines, proposal.lines, strict=True):
                if (
                    act_ln.productId != exp_ln.product_id
                    or act_ln.quantitySalesUnits != exp_ln.quantity_sales_units
                    or act_ln.lineTotalExclVat != exp_ln.line_total_excl_vat
                ):
                    mismatches.append(
                        f"line {act_ln.lineId} expected {exp_ln.product_id}×{exp_ln.quantity_sales_units}"
                        f" (€{exp_ln.line_total_excl_vat}), got {act_ln.productId}×{act_ln.quantitySalesUnits}"
                        f" (€{act_ln.lineTotalExclVat})"
                    )

    ex_ids = {e.productId for e in proposal.excluded}
    for req_ex in row.expected_excluded_ids:
        if req_ex not in ex_ids:
            mismatches.append(f"expected {req_ex} in excluded[], got {sorted(ex_ids)}")
    for sub_id in row.expected_rejected_substitutes:
        sub_entry = next(
            (e for e in proposal.excluded if e.productId == sub_id and ReasonCode.SUBSTITUTE_REJECTED in e.reasons),
            None,
        )
        if sub_entry is None:
            mismatches.append(f"expected {sub_id} with SUBSTITUTE_REJECTED in excluded[]")

    passed = len(mismatches) == 0
    return EvalRowResult(
        row_id=row.row_id,
        scenario_id=row.scenario_id,
        mandate_id=row.mandate_id,
        status="PASSED" if passed else "FAILED",
        expected_decision=row.expected_decision,
        actual_decision=proposal.decision,
        expected_reasons=tuple(str(r) for r in row.expected_reasons),
        actual_reasons=tuple(str(r) for r in proposal.reasons),
        expected_total_excl_vat=format(row.expected_total_excl_vat, ".2f") if row.expected_total_excl_vat else None,
        actual_total_excl_vat=format(proposal.totals.exclVat, ".2f"),
        detail="OK — " + row.notes if passed else "; ".join(mismatches),
        proposal=proposal,
    )


def run_eval_suite(
    *,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
    products: dict[str, Product] | None = None,
    provider: Lookup | None = None,
) -> EvalSuiteReport:
    """Run all 24 evaluation rows on recorded fixtures (or reference fixtures when data/fixtures is absent)."""
    resolved_products = products if products is not None else build_reference_products()
    capture = find_latest_fixture_capture(fixtures_dir)
    if provider is not None:
        active_provider = provider
        source: Literal["RECORDED_FIXTURES", "REFERENCE_FIXTURES"] = "RECORDED_FIXTURES"
        recorded_at = getattr(provider, "recorded_at", FROZEN_NOW).isoformat()
        capture_str = str(capture) if capture else None
    elif capture is not None:
        rec_provider = RecordedValioProvider(capture)
        active_provider = rec_provider
        source = "RECORDED_FIXTURES"
        recorded_at = rec_provider.recorded_at.isoformat()
        capture_str = str(capture)
    else:
        ref_provider = ReferenceFixtureProvider()
        active_provider = ref_provider
        source = "REFERENCE_FIXTURES"
        recorded_at = ref_provider.recorded_at.isoformat()
        capture_str = None

    results = tuple(
        evaluate_row(row, products=resolved_products, provider=active_provider)
        for row in EVAL_ROWS
    )
    total = len(results)
    active = sum(1 for r in results if r.status != "PENDING")
    passed = sum(1 for r in results if r.status == "PASSED")
    failed = sum(1 for r in results if r.status == "FAILED")
    pending = sum(1 for r in results if r.status == "PENDING")

    return EvalSuiteReport(
        source=source,
        fixture_dir=capture_str,
        recorded_at=recorded_at,
        total_rows=total,
        active_rows=active,
        failed_rows=failed,
        passed_rows=passed,
        pending_rows=pending,
        score_display=f"{passed}/{total} ({passed}/{active} active P0 rows passed; {pending} pending §13)",
        results=results,
    )


def format_eval_table(report: EvalSuiteReport) -> str:
    lines = [
        f"Evaluation Suite: {report.score_display}",
        f"Source: {report.source}" + (f" ({report.fixture_dir})" if report.fixture_dir else "") + f" · frozen now: {report.recorded_at}",
        "-" * 110,
        f"{'Row':<4} {'Scen':<4} {'Mand':<4} {'Status':<8} {'Dec':<6} {'Total':<10} {'Reasons':<36} {'Detail'}",
        "-" * 110,
    ]
    for r in report.results:
        scen = r.scenario_id or "-"
        dec = r.actual_decision or r.expected_decision or "-"
        tot = f"€{r.actual_total_excl_vat}" if r.actual_total_excl_vat else (f"€{r.expected_total_excl_vat}" if r.expected_total_excl_vat else "-")
        reasons = ",".join(r.actual_reasons or r.expected_reasons) or "[]"
        lines.append(
            f"{r.row_id:<4} {scen:<4} {r.mandate_id:<4} {r.status:<8} {dec:<6} {tot:<10} {reasons:<36} {r.detail}"
        )
    lines.append("-" * 110)
    return "\n".join(lines)
