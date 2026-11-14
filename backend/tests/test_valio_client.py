import json
import logging
from datetime import timedelta

import httpx
import pytest
from synthetic.valio import DAY, NOW, envelopes

from app.config import Settings
from app.valio import IntegrationError, LiveValioProvider, ValioClient
from app.valio.client import ValioRateLimiter


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def client_for(handler, **kwargs):
    settings = Settings(valio_api_key="synthetic-key", valio_customer_id="UPSTREAM-A",
                        valio_customer_ids={"buyer-a": "UPSTREAM-A", "buyer-b": "UPSTREAM-B"})
    timer = FakeTime()
    kwargs.setdefault("rate_limiter", ValioRateLimiter(monotonic=timer.monotonic, sleep=timer.sleep))
    return ValioClient(settings, transport=httpx.MockTransport(handler), clock=lambda: NOW, **kwargs)


def successful_handler(calls, mutate=None):
    def handle(request):
        payload = json.loads(request.content)
        calls.append((request, payload))
        price, stock = envelopes(payload["products"], payload["customer"], DAY.fromisoformat(payload["requestedDate"]))
        body = price if request.url.path == "/api/price" else stock
        if mutate:
            mutate(request.url.path, body)
        return httpx.Response(200, json=body)
    return handle


def test_shortlist_reads_are_batched_deduplicated_and_decimal():
    calls = []
    client = client_for(successful_handler(calls))
    result = LiveValioProvider(client).lookup({"I1": ["SYN-B", "SYN-A"], "I2": ["SYN-A"]}, DAY, "buyer-a")
    assert [request.url.path for request, _ in calls] == ["/api/price", "/api/availability"]
    assert all(payload == {"customer": "UPSTREAM-A", "requestedDate": DAY.isoformat(),
                           "products": ["SYN-A", "SYN-B"]} for _, payload in calls)
    assert all(request.headers["X-API-Key"] == "synthetic-key" for request, _ in calls)
    assert result.mode == "LIVE" and result.recorded_at is None
    assert result.now == NOW
    assert str(result.products["SYN-A"].price.unit_price) == "2.5"


def test_one_invalid_price_or_availability_does_not_fail_a_valid_neighbor():
    def mutate(path, body):
        if path == "/api/price":
            body["products"][0]["catalogPrice"]["value"] = -1
        else:
            body["products"][0]["scheduleLines"][0]["availableAmount"] = -1
    result = LiveValioProvider(client_for(successful_handler([], mutate))).lookup(
        {"I1": ["SYN-A", "SYN-B"]}, DAY, "buyer-a",
    )
    assert set(result.products["SYN-A"].reasons) == {"PRICE_UNAVAILABLE", "NOT_AVAILABLE"}
    assert result.products["SYN-B"].reasons == ()


@pytest.mark.parametrize("shortlists", [
    {}, [], {"I1": []}, {"I1": ["SYN"] * 21}, {"I1": "SYN"}, {"I1": [1]}, {"I1": [""]},
    {str(index): [f"SYN-{index}-{n}" for n in range(20)] for index in range(51)},
])
def test_shortlist_and_total_limits_before_any_network_call(shortlists):
    calls = []
    with pytest.raises(IntegrationError):
        client_for(successful_handler(calls)).fetch(shortlists, DAY, "buyer-a")
    assert calls == []


@pytest.mark.parametrize(("path", "change"), [
    (path, change)
    for path in ("/api/price", "/api/availability")
    for change in ("customer", "currency", "date", "missing", "duplicate", "unexpected", "missing_id")
    if path == "/api/price" or change not in ("customer", "currency")
])
def test_batch_identity_failures_fail_closed(change, path):
    def mutate(actual_path, body):
        if actual_path != path:
            return
        if change == "customer":
            body["customerId"] = "OTHER"
        elif change == "currency":
            body["currency"] = "USD"
        elif change == "date":
            body["pricingDate" if path == "/api/price" else "requestedDate"] = "2020-01-01T00:00:00"
        elif change == "missing":
            body["products"] = []
        elif change == "duplicate":
            body["products"] *= 2
        elif change == "unexpected":
            body["products"][0]["productId"] = "OTHER"
        else:
            body["products"][0].pop("productId")
    with pytest.raises(IntegrationError, match="binding"):
        client_for(successful_handler([], mutate)).fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")


def test_cache_ttl_customer_date_keys_and_force_refresh():
    ticks = [0.0]
    calls = []
    client = client_for(successful_handler(calls), monotonic=lambda: ticks[0])
    client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert len(calls) == 2
    ticks[0] = 44.9
    client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert len(calls) == 2
    ticks[0] = 45
    client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert len(calls) == 4
    client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a", force_refresh=True)
    assert len(calls) == 6
    client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-b")
    client.fetch({"I1": ["SYN-A"]}, DAY + timedelta(days=1), "buyer-a")
    assert len(calls) == 10


def test_cached_observation_cannot_be_mutated_by_a_caller():
    calls = []
    client = client_for(successful_handler(calls))
    snapshot = LiveValioProvider(client).lookup({"I1": ["SYN-A"]}, DAY, "buyer-a")
    snapshot.products.clear()
    assert LiveValioProvider(client).lookup({"I1": ["SYN-A"]}, DAY, "buyer-a").products
    assert len(calls) == 2


def test_cache_is_bounded():
    client = client_for(successful_handler([]), max_cache_entries=2)
    for i in range(4):
        client.fetch({"I1": [f"SYN-{i}"]}, DAY, "buyer-a")
    assert len(client._cache) == 2


@pytest.mark.parametrize("status", [500, 503])
def test_bounded_backoff_then_success(status):
    waits, calls = [], []
    attempts = [0]
    good = successful_handler(calls)
    def handle(request):
        attempts[0] += 1
        if attempts[0] <= 2:
            return httpx.Response(status, text="private vendor failure synthetic-key")
        return good(request)
    client = client_for(handle, sleep=waits.append)
    client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert attempts[0] == 4  # Three price attempts and one availability read.
    assert waits == [0.25, 0.5]


def test_retry_after_is_bounded():
    waits, calls = [], []
    attempts = [0]
    good = successful_handler(calls)
    def handle(request):
        attempts[0] += 1
        return httpx.Response(503, headers={"Retry-After": "99999"}) if attempts[0] == 1 else good(request)
    client_for(handle, sleep=waits.append).fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert waits == [5.0]


@pytest.mark.parametrize("status", [301, 307, 401, 403, 429, 500])
def test_failure_no_redirect_no_credentials_in_errors_or_logs(status, caplog):
    caplog.set_level(logging.DEBUG)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="synthetic-key private-body", headers={"Location": "https://other.invalid"})
    with pytest.raises(IntegrationError) as exc:
        client_for(handle, sleep=lambda _: None).fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert len(calls) == (3 if status == 500 else 1)
    assert "synthetic-key" not in str(exc.value) + caplog.text
    assert "private-body" not in str(exc.value) + caplog.text
    assert all(request.url.host == "junction26.valioaimo.fi" for request in calls)


def test_timeout_is_sanitised_and_no_fallback():
    def handle(request):
        raise httpx.ReadTimeout("synthetic-key private timeout", request=request)
    with pytest.raises(IntegrationError) as exc:
        client_for(handle).fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert "synthetic-key" not in str(exc.value)
    assert "fallback" in str(exc.value)


def test_missing_key_requires_no_network(monkeypatch):
    monkeypatch.delenv("VALIO_API_KEY", raising=False)
    client = ValioClient(Settings(valio_api_key=""))
    with pytest.raises(IntegrationError, match="VALIO_API_KEY"):
        client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")


@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"products": NaN}'])
def test_bad_json_is_not_cached(body):
    client = client_for(lambda request: httpx.Response(200, content=body))
    with pytest.raises(IntegrationError):
        client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert not client._cache
