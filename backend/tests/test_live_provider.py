import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from conftest import BUYER, commit, propose

from app.config import Settings
from app.db.models import Product
from app.integrations.provider import IntegrationError
from app.integrations.real_valio import RealValioProvider
from app.integrations.valio_catalog import normalize_product
from app.main import get_provider


@pytest.fixture
def vendor(context):
    client = context[0]
    day = client.get("/api/v1/context", headers=BUYER).json()["next_monday"]
    time = datetime.now(UTC)
    price = {
        "customerId": "CUSTOMER-A",
        "currency": "EUR",
        "pricingDate": f"{day}T00:00:00+0200",
        "products": [
            {
                "productId": "000777",
                "quantity": 1.0,
                "netPriceFound": False,
                "error": False,
                "catalogPrice": {
                    "value": 4.99,
                    "valueWithTax": 5.66,
                    "validFrom": "2020-01-01T00:00:00+0200",
                    "validTo": "9999-12-31T00:00:00+0200",
                    "currency": "EUR",
                },
            }
        ],
    }
    stock = {
        "timeNow": time.isoformat(),
        "requestedDate": f"{day}T00:00:00+0200",
        "products": [
            {
                "productId": "000777",
                "closingTime": (time + timedelta(days=1)).isoformat(),
                "salesBlock": False,
                "logisticModel": "Stock",
                "scheduleLines": [
                    {
                        "salesBlock": False,
                        "availableAmount": 10,
                        "availableDate": day,
                        "salesUnit": "KI",
                    }
                ],
            }
        ],
    }
    settings = Settings(valio_api_key="test-placeholder", valio_customer_ids={"restaurant-a": "CUSTOMER-A"})
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        assert request.headers["X-API-Key"] == "test-placeholder"
        assert body == {"customer": "CUSTOMER-A", "requestedDate": day, "products": ["000777"]}
        return httpx.Response(200, json=price if request.url.path == "/api/price" else stock)

    provider = RealValioProvider(settings, transport=httpx.MockTransport(handle))
    return provider, day, price, stock, calls


def product_record():
    return {
        "id": "000777",
        "salesUnit": "KI",
        "baseUnit": "ST",
        "allowedLotSize": 1,
        "category": "dairy",
        "units": [{"unitId": "KI", "sizeInBaseUnits": 12}],
        "deposits": [],
        "synkkaData": {
            "names": [{"language": "en", "value": "Fixture milk"}],
            "variableUnit": False,
            "unitConversions": [
                {"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": 1}}
            ],
            "classifications": [
                {"name": "nutritionalClaim", "values": [{"id": "FREE_FROM_LACTOSE"}]},
                {"name": "allergen", "values": [{"id": "AM", "unit": "CONTAINS"}]},
            ],
        },
    }


def test_live_request_contract_dates_customer_and_decimal(vendor):
    provider, day, _, _, calls = vendor
    parsed_date = datetime.fromisoformat(day).date()
    prices = provider.get_prices(["000777"], parsed_date, "restaurant-a")
    stock = provider.check_availability(["000777"], parsed_date, "restaurant-a")
    assert prices["000777"].unit_price == Decimal("4.99")
    assert stock["000777"].available_quantity == Decimal(10)
    assert stock["000777"].status == "CONFIRMED"
    assert [c[0] for c in calls] == ["/api/price", "/api/availability"]


@pytest.mark.parametrize(
    "change", ["terminal", "cutoff", "blocked", "line_blocked", "empty", "late", "unknown_cutoff"]
)
def test_availability_never_invents_confirmed_stock(vendor, change):
    provider, day, _, response, _ = vendor
    p = response["products"][0]
    expected = "UNAVAILABLE"
    if change == "terminal":
        p["logisticModel"] = "Terminal"
        p["scheduleLines"][0]["availableAmount"] = 999999
        expected = "UNKNOWN"
    elif change == "cutoff":
        p["closingTime"] = "2020-01-01T12:00:00+0200"
    elif change == "blocked":
        p["salesBlock"] = True
    elif change == "line_blocked":
        p["scheduleLines"][0]["salesBlock"] = True
    elif change == "empty":
        p["scheduleLines"] = []
    elif change == "late":
        p["scheduleLines"][0]["availableDate"] = "2099-12-31"
    else:
        p["closingTime"] = None
        expected = "UNKNOWN"
    checked = provider.check_availability(["000777"], datetime.fromisoformat(day).date(), "restaurant-a")[
        "000777"
    ]
    assert checked.status == expected
    if change == "terminal":
        assert checked.available_quantity is None


@pytest.mark.parametrize(
    "change", ["error", "missing", "duplicate", "currency", "customer", "date", "validity", "negative"]
)
def test_invalid_or_unpriced_responses_fail_closed(vendor, change):
    provider, day, response, _, _ = vendor
    if change == "error":
        response["products"][0]["error"] = True
    elif change == "missing":
        response["products"] = []
    elif change == "duplicate":
        response["products"].append(deepcopy(response["products"][0]))
    elif change == "currency":
        response["currency"] = "USD"
    elif change == "customer":
        response["customerId"] = "another-customer"
    elif change == "date":
        response["pricingDate"] = "2020-01-01T00:00:00+0200"
    elif change == "validity":
        response["products"][0]["catalogPrice"]["validTo"] = "2020-01-01T00:00:00+0200"
    else:
        response["products"][0]["catalogPrice"]["value"] = -1
    with pytest.raises(IntegrationError):
        provider.get_prices(["000777"], datetime.fromisoformat(day).date(), "restaurant-a")


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_provider_failures_do_not_leak_secrets_or_fall_back(vendor, status):
    provider, day, _, _, _ = vendor
    provider.transport = httpx.MockTransport(
        lambda request: httpx.Response(status, text="private vendor error test-placeholder")
    )
    with pytest.raises(IntegrationError) as exc:
        provider.get_prices(["000777"], datetime.fromisoformat(day).date(), "restaurant-a")
    assert str(status) in str(exc.value)
    assert "test-placeholder" not in str(exc.value)


def test_explicit_unit_conversion_and_sales_price_totals(context, vendor, payload):
    client, _, factory, _ = context
    provider = vendor[0]
    product = normalize_product(product_record())
    assert product.unit == "L" and product.quantity_per_sales_unit == 12
    assert product.pack_quantity == 12
    with factory() as db:
        db.add(
            Product(
                code=product.code,
                name=product.name,
                category=product.category,
                data=product.model_dump(mode="json"),
            )
        )
        db.commit()
    from app.main import app

    app.dependency_overrides[get_provider] = lambda: provider
    payload["items"] = [{"product_code": product.code, "quantity": "24", "unit": "L"}]
    proposal = propose(client, payload)
    checked = proposal["offer"]["items"][0]
    assert proposal["offer"]["total"] == "9.98"
    assert checked["sales_quantity"] == "2"
    assert checked["sales_unit"] == "KI"
    assert checked["available_quantity"] == "120"
    assert commit(client, proposal["id"]).status_code == 200
    # Same price/date contract but stock now unconfirmed must invalidate an authorization.
    p = propose(client, payload)
    vendor[3]["products"][0]["logisticModel"] = "Terminal"
    assert commit(client, p["id"]).status_code == 409


def test_unsupported_conversion_keeps_original_unit():
    record = product_record()
    record["synkkaData"]["unitConversions"] = [
        {"synkkaPackagingType": "CASE", "grossWeight": {"unit": "GRM", "value": 12000}}
    ]
    product = normalize_product(record)
    assert product.unit == "KI" and product.quantity_per_sales_unit == 1


def test_partial_allergen_data_does_not_imply_safety(context, payload):
    client, _, factory, _ = context
    from app.schemas import OfferOut, ProposalRequest
    from app.services.orders import get_policy
    from app.services.policy import evaluate

    p = normalize_product(product_record())
    assert p.allergens == ["milk"]
    assert p.free_from_allergens == []
    offer = client.post(
        "/api/v1/check-offer",
        headers=BUYER,
        json={"items": payload["items"], "delivery_date": payload["delivery_date"]},
    ).json()
    p = p.model_copy(update={"code": payload["items"][0]["product_code"]})
    payload["excluded_allergens"] = ["nuts"]
    with factory() as db:
        decision, reasons = evaluate(
            get_policy(db, "restaurant-a"),
            ProposalRequest.model_validate(payload),
            OfferOut.model_validate(offer),
            {p.code: p},
        )
    assert decision == "NEEDS_APPROVAL"
    assert "ALLERGEN_DATA_UNKNOWN" in {r.code for r in reasons}


@pytest.mark.parametrize(
    ("code", "label"),
    [("ML", "lactose"), ("UM", "molluscs"), ("UW", "wheat"), ("AW", "gluten"), ("AM", "milk")],
)
def test_gs1_allergen_codes_map_to_the_right_allergen(code, label):
    # Regression: ML was mapped to molluscs and UM was unmapped, so a mollusc restriction missed mussels.
    record = product_record()
    record["synkkaData"]["classifications"][1]["values"] = [{"id": code, "unit": "CONTAINS"}]
    assert label in normalize_product(record).allergens
