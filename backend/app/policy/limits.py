"""T62: the limit sentence, true for a single order and for a delivery day split into several orders."""

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol

from app.contracts import ReasonCode, format_date, format_money


class _Dated(Protocol):
    deliveryDate: date


def delivery_dates(lines: Iterable[_Dated], fallback: date) -> tuple[date, ...]:
    """The delivery dates an order's lines arrive on, earliest first; `fallback` (the requested date) only when
    there are no lines. The split-order guard is keyed on these, never on the requested date: the live API can
    deliver orders requested for different days on the same day."""
    return tuple(sorted({line.deliveryDate for line in lines})) or (fallback,)


def format_days(days: Sequence[date]) -> str:
    """'Mon 16 Nov', 'Mon 16 Nov and Tue 17 Nov', 'Mon 16 Nov, Tue 17 Nov and Wed 18 Nov'."""
    names = [format_date(d) for d in sorted(set(days))]
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def limit_sentence(code: ReasonCode, total: Decimal, limit: Decimal, days: Sequence[date]) -> str:
    """Why OVER_AUTO_LIMIT / OVER_HARD_LIMIT fired, without a full stop.

    The limits apply to this order plus what the customer already has committed for delivery on the same day
    (`decide(committed_excl_vat=…)`). When this order alone is within the limit, the committed orders are what
    pushed the delivery day over it, and the sentence says so instead of claiming the order alone is too large.
    """
    kind = "hard limit" if code == ReasonCode.OVER_HARD_LIMIT else "auto-approve limit"
    if total > limit:
        return f"{format_money(total)} excl. VAT is above your {format_money(limit)} {kind}"
    return (
        f"{format_money(total)} excl. VAT plus what is already ordered or awaiting approval for {format_days(days)} "
        f"is above your {format_money(limit)} {kind}"
    )
