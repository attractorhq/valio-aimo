"""Synthetic transports only: supplier cooldown, cross-client/thread pacing and API propagation."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email.utils import format_datetime
from threading import Event, Lock

import httpx
import pytest
from fastapi import FastAPI
from synthetic.demo_client import DemoTestClient as TestClient
from synthetic.valio import DAY, NOW
from test_valio_client import FakeTime, client_for, successful_handler

from app.api.errors import register_error_handlers
from app.valio.client import ValioRateLimiter
from app.valio.errors import IntegrationError, ValioRateLimited


@pytest.mark.parametrize(("header", "seconds"), [
    (None, 30), ("1", 30), ("45", 45), ("99999", 99999), ("30.1", 31),
    ("invalid", 30), ("NaN", 30), ("inf", 30), ("-5", 30),
    (format_datetime(NOW + timedelta(seconds=60)), 60),
    (format_datetime(NOW - timedelta(seconds=60)), 30),
])
def test_429_calls_upstream_once_then_fails_fast_and_recovers(header, seconds):
    timer = FakeTime()
    limiter = ValioRateLimiter(monotonic=timer.monotonic, sleep=timer.sleep)
    calls = []
    attempts = []
    good = successful_handler(calls)

    def handle(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={} if header is None else {"Retry-After": header},
                                  text="private vendor failure synthetic-key")
        return good(request)

    client = client_for(handle, rate_limiter=limiter, sleep=lambda _: pytest.fail("429 must not retry"))
    other = client_for(handle, rate_limiter=limiter)
    with pytest.raises(ValioRateLimited) as exc:
        client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert isinstance(exc.value, IntegrationError)
    assert exc.value.retry_after == seconds
    assert len(attempts) == 1
    blocked_at = timer.now
    waits = list(timer.waits)

    for source in (client, other):
        with pytest.raises(ValioRateLimited, match=f"Valio API rate limit: retry in {seconds} s"):
            source.fetch({"I1": ["SYN-A"]}, DAY, "buyer-b", force_refresh=True)
    assert len(attempts) == 1 and timer.now == blocked_at and timer.waits == waits

    duration = max(30, float(header)) if header and header.replace(".", "", 1).isdigit() else seconds
    timer.now = blocked_at + duration - 0.5
    with pytest.raises(ValioRateLimited) as exc:
        other.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert exc.value.retry_after == 1
    timer.now = blocked_at + duration
    assert other.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a").mode == "LIVE"
    assert len(attempts) == 3  # Initial 429, then recovered price + availability.


def test_cooldown_also_blocks_cached_reads_and_cannot_be_shortened():
    timer = FakeTime()
    limiter = ValioRateLimiter(monotonic=timer.monotonic, sleep=timer.sleep)
    calls = []
    client = client_for(successful_handler(calls), rate_limiter=limiter)
    client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    with pytest.raises(ValioRateLimited):
        limiter.block(60)
    timer.now += 1
    with pytest.raises(ValioRateLimited) as exc:
        limiter.block(30)
    assert exc.value.retry_after == 59
    with pytest.raises(ValioRateLimited):
        client.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")
    assert len(calls) == 2


def test_twenty_requests_across_clients_and_threads_take_at_least_four_seconds(monkeypatch):
    from app.valio import client as client_module

    limiter = ValioRateLimiter()
    monkeypatch.setattr(client_module, "_RATE_LIMITER", limiter)
    call_times = []
    lock = Lock()
    good = successful_handler([])

    def handle(request):
        with lock:
            call_times.append(time.monotonic())
        return good(request)

    clients = [client_for(handle, rate_limiter=None) for _ in range(4)]
    assert all(client.rate_limiter is limiter for client in clients)
    started = limiter._next_request_at - 0.2
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda i: clients[i % 4]._post(
            "/api/price" if i % 2 else "/api/availability", "UPSTREAM-A", DAY, ("SYN-A",), force_refresh=True,
        ), range(20)))
    assert len(call_times) == 20
    assert call_times[-1] - started >= 4.0
    assert all(b - a >= 0.19 for a, b in zip(call_times, call_times[1:]))


def test_rest_rate_limit_response_has_503_code_and_retry_after():
    app = FastAPI()
    register_error_handlers(app)
    timer = FakeTime()
    limiter = ValioRateLimiter(monotonic=timer.monotonic, sleep=timer.sleep)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429)

    supplier = client_for(handle, rate_limiter=limiter)

    @app.get("/read")
    def read():
        return supplier.fetch({"I1": ["SYN-A"]}, DAY, "buyer-a")

    with TestClient(app) as client:
        for _ in range(2):
            response = client.get("/read")
            assert response.status_code == 503
            assert response.headers["Retry-After"] == "30"
            assert response.json() == {"error": {
                "code": "VALIO_RATE_LIMITED", "message": "Valio API rate limit: retry in 30 s",
                "details": {"retryAfterSeconds": 30},
            }}
    assert len(calls) == 1


def test_thread_waiting_for_token_rechecks_new_cooldown():
    timer = FakeTime()
    waiting, release = Event(), Event()

    def sleep(seconds):
        waiting.set()
        assert release.wait(timeout=2)
        timer.sleep(seconds)

    limiter = ValioRateLimiter(monotonic=timer.monotonic, sleep=sleep)
    calls = []
    client = client_for(successful_handler(calls), rate_limiter=limiter)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.fetch, {"I1": ["SYN-A"]}, DAY, "buyer-a")
        try:
            assert waiting.wait(timeout=2)
            with pytest.raises(ValioRateLimited):
                limiter.block(30)
        finally:
            release.set()
        with pytest.raises(ValioRateLimited):
            future.result(timeout=2)
    assert calls == []
