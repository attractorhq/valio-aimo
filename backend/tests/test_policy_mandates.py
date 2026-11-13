"""Mandates M1/M2 (brief §6.2), versioned and read-only for agents; a change is an audit event (§6.1)."""

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import Approver
from app.policy import DEMO_MANDATES, M1, M2, MandateBook, change_mandate
from tests.synthetic.policy import NOW

ADMIN = "human:Buyer admin (demo)"


def test_demo_mandates_match_the_brief():
    assert (M1.organisationName, M1.allergenMode, M1.autoApproveLimitEur, M1.hardLimitEur) == (
        "Nordic Harbour Hotels (demo)", "STANDARD", Decimal("1500.00"), Decimal("5000.00"))
    assert (M1.autoAcceptValioSubstitutes, M1.autoAcceptSupplierStock, M1.approver.role) == (
        True, False, "Purchasing Manager (demo)")
    assert (M2.organisationName, M2.allergenMode, M2.autoApproveLimitEur, M2.hardLimitEur) == (
        "Northbay School Kitchen (demo)", "STRICT", Decimal("500.00"), Decimal("3000.00"))
    assert (M2.autoAcceptValioSubstitutes, M2.autoAcceptSupplierStock, M2.approver.role) == (
        False, False, "Head Chef (demo)")
    for mandate in DEMO_MANDATES:
        assert mandate.version == 1 and mandate.minReliability == Decimal("0.80") and mandate.enforceCutoff
        assert mandate.autoAcceptAgentSubstitutes is False and mandate.forbiddenAllergens == []


def test_mandates_are_frozen_for_readers():
    with pytest.raises(ValidationError):
        M2.autoApproveLimitEur = Decimal("100000.00")


def test_change_creates_a_new_version_and_an_audit_entry():
    updated, entry = change_mandate(M2, {"autoApproveLimitEur": "750.00", "allergenMode": "STRICT"},
                                    actor=ADMIN, now=NOW)
    assert updated.version == 2 and updated.autoApproveLimitEur == Decimal("750.00")
    assert M2.version == 1 and M2.autoApproveLimitEur == Decimal("500.00")
    assert entry.model_dump() | {"timestamp": None} == {
        "entryId": "mandate-northbay-school-kitchen-v2", "customerId": "northbay-school-kitchen",
        "proposalId": None, "offerId": None, "offerVersion": None, "offerHash": None, "mandateVersion": 2,
        "timestamp": None, "actor": ADMIN, "event": "MANDATE_CHANGED", "fromState": None, "toState": None,
        "reasons": [], "rule": "§6.1", "facts": [], "comment": None,
        "summary": "Policy changed by buyer admin: autoApproveLimitEur €500.00 → €750.00",
    }


def test_change_summary_formats_non_money_fields():
    _, entry = change_mandate(
        M1, {"minReliability": "0.9", "approver": Approver(name="Ann (demo)", role="Buyer (demo)")},
        actor=ADMIN, now=NOW,
    )
    assert entry.summary == ("Policy changed by buyer admin: approver Purchasing Manager (demo) "
                             "(Purchasing Manager (demo)) → Ann (demo) (Buyer (demo)); minReliability 0.80 → 0.9")


@pytest.mark.parametrize(
    ("changes", "actor", "error"),
    [
        ({"autoApproveLimitEur": "1.00"}, "agent:purchasing", PermissionError),
        ({"autoApproveLimitEur": "1.00"}, "system", PermissionError),
        ({"version": 7}, ADMIN, ValueError),
        ({"customerId": "other"}, ADMIN, ValueError),
        ({"autoAcceptAgentSubstitutes": True}, ADMIN, ValueError),
        ({"budget": "1"}, ADMIN, ValueError),
        ({"autoApproveLimitEur": "500"}, ADMIN, ValueError),  # no actual change
        ({"autoApproveLimitEur": "3000.01"}, ADMIN, ValueError),  # above the hard limit
        ({"minReliability": "1.5"}, ADMIN, ValidationError),
    ],
)
def test_invalid_changes_are_refused(changes, actor, error):
    with pytest.raises(error):
        change_mandate(M2, changes, actor=actor, now=NOW)


def test_change_needs_an_aware_time():
    with pytest.raises(ValueError, match="aware"):
        change_mandate(M2, {"autoApproveLimitEur": "600.00"}, actor=ADMIN, now=datetime(2026, 11, 13))


def test_mandate_book_keeps_history():
    book = MandateBook()
    assert book.customers() == ("nordic-harbour-hotels", "northbay-school-kitchen")
    assert book.get("northbay-school-kitchen") is M2
    updated, entry = book.change("northbay-school-kitchen", {"allergenMode": "STANDARD"}, actor=ADMIN, now=NOW)
    assert book.get("northbay-school-kitchen") is updated and entry.mandateVersion == 2
    assert book.get_version("northbay-school-kitchen", 1) is M2
    assert book.get_version("northbay-school-kitchen", 2) is updated
    with pytest.raises(KeyError):
        book.get_version("northbay-school-kitchen", 3)
    with pytest.raises(KeyError):
        book.get("unknown-customer")
    with pytest.raises(ValueError, match="duplicate"):
        MandateBook([M1, M1])
