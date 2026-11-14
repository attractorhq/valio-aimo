"""T68: mandate scope (allowed categories) and validity window. Synthetic data only."""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.contracts import REASON_REGISTRY, ReasonCode
from app.policy import M1, M2, change_mandate, decide
from app.policy.mandates import category_allowed, window_violation
from tests.synthetic.policy import NOW, item, line, product, request

ADMIN = "human:Buyer admin"


def _product(product_id: str, category: str):
    return product(product_id).model_copy(update={"category": category})


def _scoped(**changes):
    return M1.model_copy(update=changes)


def test_new_codes_are_never_approvable():
    assert REASON_REGISTRY[ReasonCode.CATEGORY_NOT_ALLOWED].effect == "EXCLUDE"
    assert REASON_REGISTRY[ReasonCode.MANDATE_NOT_VALID].effect == "BLOCK"
    assert not REASON_REGISTRY[ReasonCode.CATEGORY_NOT_ALLOWED].approvable
    assert not REASON_REGISTRY[ReasonCode.MANDATE_NOT_VALID].approvable


def test_demo_mandates_have_no_category_limit_and_no_expiry():
    for mandate in (M1, M2):
        assert (mandate.allowedCategories, mandate.validFrom, mandate.validTo) == ([], None, None)
        assert category_allowed(mandate, "999999")
        assert window_violation(mandate, NOW) is None


def test_category_prefix_match():
    mandate = _scoped(allowedCategories=["211", "2240"])
    assert category_allowed(mandate, "211405")
    assert category_allowed(mandate, "224012")
    assert not category_allowed(mandate, "224112")
    assert not category_allowed(mandate, "")


def test_a_product_outside_the_allowed_categories_is_excluded():
    dairy, cleaning = _product("DAIRY", "211405"), _product("SOAP", "630100")
    mandate = _scoped(allowedCategories=["211"])
    d = decide(mandate=mandate, request=request(), lines=[line(prod=cleaning)], now=NOW)
    assert d.outcome == "NO_ORDER"
    ((excluded),) = d.excluded_products()
    assert excluded.productId == "SOAP" and excluded.reasons == [ReasonCode.CATEGORY_NOT_ALLOWED]
    (finding,) = [f for f in d.excluded[0].findings if f.code == "CATEGORY_NOT_ALLOWED"]
    assert finding.detail == "Category 630100 is not in the allowed list (211)"

    # The same order from an allowed category passes, and alternatives are still considered.
    d = decide(mandate=mandate, request=request(), lines=[line(prod=dairy)], now=NOW)
    assert (d.outcome, d.reasons) == ("AUTO", ())
    d = decide(mandate=mandate, request=request(item(quantity="10")),
               lines=[line("L1", prod=cleaning), line("L2", prod=dairy)], now=NOW)
    assert d.outcome == "AUTO" and [v.line.product.productId for v in d.lines] == ["DAIRY"]


@pytest.mark.parametrize(("valid_from", "valid_to", "expected"), [
    (None, date(2026, 11, 13), None),  # the last day is still valid (inclusive)
    (date(2026, 11, 13), None, None),  # the first day is valid
    (None, date(2026, 11, 12), "Mandate expired after Thu 12 Nov; a buyer admin must renew it"),
    (date(2026, 11, 14), None, "Mandate valid from Sat 14 Nov; today is Fri 13 Nov"),
])
def test_window_is_inclusive_on_the_helsinki_calendar_day(valid_from, valid_to, expected):
    assert window_violation(_scoped(validFrom=valid_from, validTo=valid_to), NOW) == expected


def test_window_uses_the_helsinki_day_not_utc():
    # 23:30 UTC on 12 Nov is already 13 Nov in Helsinki.
    late_utc = datetime(2026, 11, 12, 23, 30, tzinfo=UTC)
    assert window_violation(_scoped(validFrom=date(2026, 11, 13)), late_utc) is None


def test_an_expired_mandate_blocks_with_the_reason():
    mandate = _scoped(validTo=date(2026, 11, 12))
    d = decide(mandate=mandate, request=request(), lines=[line()], now=NOW)
    assert (d.outcome, d.reasons, d.approver_role) == ("BLOCK", (ReasonCode.MANDATE_NOT_VALID,), None)
    (finding,) = d.findings
    assert finding.rule == "O0" and "expired after Thu 12 Nov" in finding.detail


def test_window_must_be_in_order():
    with pytest.raises(ValidationError):
        M1.model_validate({**M1.model_dump(), "validFrom": "2026-11-14", "validTo": "2026-11-13"})
    with pytest.raises(ValueError):
        change_mandate(M1, {"validFrom": date(2026, 11, 14), "validTo": date(2026, 11, 13)}, actor=ADMIN, now=NOW)


def test_scope_and_window_changes_are_audited():
    updated, entry = change_mandate(
        M2, {"allowedCategories": ["211"], "validTo": date(2026, 12, 31)}, actor=ADMIN, now=NOW)
    assert updated.version == M2.version + 1
    assert entry.summary == ("Policy changed by buyer admin: allowedCategories any → 211; "
                             "validTo none → Thu 31 Dec")
    renewed, entry = change_mandate(updated, {"validTo": None}, actor=ADMIN, now=NOW)
    assert renewed.validTo is None and entry.summary.endswith("validTo Thu 31 Dec → none")
