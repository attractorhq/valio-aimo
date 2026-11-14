"""T62: the limit sentence, true for a single order and for a day split into several orders."""

from datetime import date
from decimal import Decimal

from app.contracts import ReasonCode, format_date, format_money


def limit_sentence(code: ReasonCode, total: Decimal, limit: Decimal, requested_date: date) -> str:
    """Why OVER_AUTO_LIMIT / OVER_HARD_LIMIT fired, without a full stop.

    The limits apply to this order plus what the customer already has committed for the same delivery date
    (`decide(committed_excl_vat=…)`). When this order alone is within the limit, the committed orders are
    what pushed the day over it, and the sentence says so instead of claiming the order alone is too large.
    """
    kind = "hard limit" if code == ReasonCode.OVER_HARD_LIMIT else "auto-approve limit"
    if total > limit:
        return f"{format_money(total)} excl. VAT is above your {format_money(limit)} {kind}"
    return (
        f"{format_money(total)} excl. VAT plus the orders already committed for {format_date(requested_date)} "
        f"is above your {format_money(limit)} {kind}"
    )
