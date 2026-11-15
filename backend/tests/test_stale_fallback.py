"""T71: when Valio's API fails, the last successful read (≤ 15 min old) answers, labelled ESTIMATE / "stale
fallback". Such an offer is never AUTO, and the re-check before confirmation never confirms from the cache.
Synthetic hand-made records only (patch P7)."""

import json
from datetime import timedelta

import pytest
from test_proposals_gateway import HEL, NOW, PRODUCTS, UPSTREAM, MutableSyntheticProvider, request_a

from app.contracts import ReasonCode
from app.gateway.proposals import ProposalGateway
from app.policy import DEMO_MANDATES, M1, MandateBook
from app.valio import IntegrationError, LiveValioProvider
from app.valio.client import RawBatch, product_ids
from app.valio.errors import ValioRateLimited


class FlakyClient:
    """Stands in for `ValioClient.fetch`: answers from synthetic rows until `fail` is set."""

    def __init__(self):
        self.rows = MutableSyntheticProvider()
        self.fail: Exception | None = None
        self.calls: list[bool] = []

    def fetch(self, shortlists, requested_date, customer_id, *, force_refresh=False):
        self.calls.append(force_refresh)
        if self.fail is not None:
            raise self.fail
        codes = product_ids(shortlists)
        price = {"customerId": UPSTREAM, "currency": "EUR", "pricingDate": f"{requested_date}T00:00:00{HEL}",
                 "products": [self.rows.prices[c] for c in codes]}
        stock = {"timeNow": NOW.isoformat(), "requestedDate": f"{requested_date}T00:00:00{HEL}",
                 "products": [self.rows.stocks[c] for c in codes]}
        return RawBatch(customer_id, UPSTREAM, requested_date, codes, json.dumps(price).encode(),
                        json.dumps(stock).encode(), NOW, NOW)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def _provider(**kw):
    client, clock = FlakyClient(), Clock()
    return LiveValioProvider(client, stale_fallback=True, clock=clock, **kw), client, clock


SHORTLIST = {"I1": ["SYN-MILK"]}


def test_a_failed_read_is_answered_from_a_fresh_copy_labelled_as_an_estimate():
    provider, client, clock = _provider()
    live = provider.lookup(SHORTLIST, NOW.date() + timedelta(days=3), M1.customerId)
    assert {f.level for f in live.products["SYN-MILK"].facts} - {"UNKNOWN"} <= {"VERIFIED", "DECLARED"}

    client.fail = IntegrationError("Valio /api/price returned HTTP 503; no fallback used.")
    clock.now = NOW + timedelta(minutes=10)
    stale = provider.lookup(SHORTLIST, NOW.date() + timedelta(days=3), M1.customerId)
    facts = stale.products["SYN-MILK"].facts
    assert {f.level for f in facts} <= {"ESTIMATE", "UNKNOWN"}
    assert all(f.method == "stale fallback · Valio API, cached at 21:52" for f in facts)
    assert all(f.source.retrievedAt == NOW for f in facts)
    # Same price and stock as the live read: only the labels changed.
    assert stale.products["SYN-MILK"].price.unit_price == live.products["SYN-MILK"].price.unit_price


@pytest.mark.parametrize("error", [IntegrationError("timeout"), ValioRateLimited(30)])
def test_no_copy_too_old_a_copy_or_a_forced_refresh_keeps_the_error(error):
    provider, client, clock = _provider()
    day = NOW.date() + timedelta(days=3)
    client.fail = error
    with pytest.raises(type(error)):
        provider.lookup(SHORTLIST, day, M1.customerId)  # nothing cached yet
    client.fail = None
    provider.lookup(SHORTLIST, day, M1.customerId)
    client.fail = error
    with pytest.raises(type(error)):
        provider.lookup(SHORTLIST, day, M1.customerId, force_refresh=True)  # the re-check never falls back
    clock.now = NOW + timedelta(minutes=16)
    with pytest.raises(type(error)):
        provider.lookup(SHORTLIST, day, M1.customerId)  # older than 15 minutes
    with pytest.raises(type(error)):
        provider.lookup({"I1": ["SYN-MILK", "SYN-OAT"]}, day, M1.customerId)  # one product never read


def test_without_the_option_a_failure_is_never_hidden():
    client, clock = FlakyClient(), Clock()
    provider = LiveValioProvider(client, clock=clock)
    day = NOW.date() + timedelta(days=3)
    provider.lookup(SHORTLIST, day, M1.customerId)
    client.fail = IntegrationError("down")
    with pytest.raises(IntegrationError):
        provider.lookup(SHORTLIST, day, M1.customerId)


def test_a_stale_offer_is_never_auto_and_is_never_confirmed_from_the_cache():
    provider, client, clock = _provider()
    gateway = ProposalGateway(products=PRODUCTS, mandates=MandateBook(DEMO_MANDATES), provider=provider)
    offer_req, request = request_a()

    live = gateway.get_offer(offer_req, offer_id="OF-LIVE").offer
    first = gateway.create_proposal(M1.customerId, live.offerId, live.offerVersion, request, now=NOW,
                                    auto_confirm=False)
    assert first.decision == "AUTO"  # the official example, read live

    client.fail = IntegrationError("Valio /api/availability returned HTTP 503; no fallback used.")
    clock.now = NOW + timedelta(minutes=5)
    stale_offer = gateway.get_offer(offer_req, offer_id="OF-STALE").offer
    proposal = gateway.create_proposal(M1.customerId, stale_offer.offerId, stale_offer.offerVersion, request,
                                       now=NOW)
    assert proposal.decision == "ASK" and proposal.state == "PENDING_APPROVAL"
    assert ReasonCode.STALE_DATA in proposal.reasons

    view = gateway.get_approver_view(proposal.proposalId)
    with pytest.raises(IntegrationError):
        gateway.decide_approval(proposal.proposalId, proposal.offerVersion, view.approvalToken, "APPROVE", now=NOW)
    assert gateway.get_proposal(proposal.proposalId).state != "CONFIRMED_SIMULATED"
    assert client.calls[-1] is True  # the re-check asked for a live read
