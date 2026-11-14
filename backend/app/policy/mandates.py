"""Demo mandates M1/M2 (brief §6.2) and versioned, audited mandate changes (§6.1).

Agents only ever read a frozen `Mandate`. A change comes from a buyer admin, creates a new version,
and emits an audit entry. Nothing here is reachable from the agent channel.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.contracts import Approver, AuditEntry, Mandate, format_date, format_money

HELSINKI = ZoneInfo("Europe/Helsinki")

M1 = Mandate(
    customerId="nordic-harbour-hotels",
    version=1,
    organisationName="Nordic Harbour Hotels",
    segment="hotel chain",
    allergenMode="STANDARD",
    autoApproveLimitEur=Decimal("1500.00"),
    hardLimitEur=Decimal("5000.00"),
    autoAcceptValioSubstitutes=True,
    autoAcceptSupplierStock=False,
    minReliability=Decimal("0.80"),
    enforceCutoff=True,
    forbiddenAllergens=[],
    approver=Approver(name="Purchasing Manager", role="Purchasing Manager"),
)
M2 = Mandate(
    customerId="northbay-school-kitchen",
    version=1,
    organisationName="Northbay School Kitchen",
    segment="school kitchen",
    allergenMode="STRICT",
    autoApproveLimitEur=Decimal("500.00"),
    hardLimitEur=Decimal("3000.00"),
    autoAcceptValioSubstitutes=False,
    autoAcceptSupplierStock=False,
    minReliability=Decimal("0.80"),
    enforceCutoff=True,
    forbiddenAllergens=[],
    approver=Approver(name="Head Chef", role="Head Chef"),
)
DEMO_MANDATES = (M1, M2)



def category_allowed(mandate: Mandate, category: str) -> bool:
    """T68: empty `allowedCategories` allows any category; otherwise the product code must start with one."""
    if not mandate.allowedCategories:
        return True
    code = (category or "").strip()
    return any(code.startswith(prefix.strip()) for prefix in mandate.allowedCategories if prefix.strip())


def window_violation(mandate: Mandate, now: datetime) -> str | None:
    """T68: why the mandate does not apply at `now` (Helsinki calendar day, both ends inclusive), or None."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(HELSINKI).date()
    if mandate.validFrom and today < mandate.validFrom:
        return f"Mandate valid from {format_date(mandate.validFrom)}; today is {format_date(today)}"
    if mandate.validTo and today > mandate.validTo:
        return f"Mandate expired after {format_date(mandate.validTo)}; a buyer admin must renew it"
    return None


# Identity and version are the gateway's; autoAcceptAgentSubstitutes is always false by contract.
_IMMUTABLE = frozenset({"customerId", "version", "autoAcceptAgentSubstitutes"})


def _show(field: str, value: Any) -> str:
    if field.endswith("Eur"):
        return format_money(value)
    if isinstance(value, Approver):
        return f"{value.name} ({value.role})"
    if isinstance(value, date):
        return format_date(value)
    if value is None:
        return "none"
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "any"
    if field == "restaurantPreferences":
        text = " ".join(str(value).split())
        return f"'{text[:80]}…'" if len(text) > 80 else (f"'{text}'" if text else "none")
    return str(value)


def change_mandate(
    current: Mandate, changes: Mapping[str, Any], *, actor: str, now: datetime
) -> tuple[Mandate, AuditEntry]:
    """A buyer-admin change: returns version N+1 and its audit entry. Pure; the caller stores both."""
    if not actor.startswith("human:"):
        raise PermissionError("only a human buyer admin can change a mandate; agents can only read it")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if forbidden := _IMMUTABLE & changes.keys():
        raise ValueError(f"cannot change {sorted(forbidden)}")
    data = current.model_dump()
    if unknown := changes.keys() - data.keys():
        raise ValueError(f"unknown mandate fields {sorted(unknown)}")
    updated = Mandate.model_validate({**data, **changes, "version": current.version + 1})
    changed = sorted(f for f in changes if getattr(updated, f) != getattr(current, f))
    if not changed:
        raise ValueError("no change")
    if updated.hardLimitEur < updated.autoApproveLimitEur:
        raise ValueError("hard limit must not be below the auto-approve limit")
    summary = "; ".join(
        f"{f} {_show(f, getattr(current, f))} → {_show(f, getattr(updated, f))}" for f in changed
    )
    entry = AuditEntry(
        entryId=f"mandate-{current.customerId}-v{updated.version}",
        customerId=current.customerId,
        mandateVersion=updated.version,
        timestamp=now,
        actor=actor,
        event="MANDATE_CHANGED",
        rule="§6.1",
        summary=f"Policy changed by buyer admin: {summary}",
    )
    return updated, entry


class MandateBook:
    """In-memory, versioned mandates. `get` is the read-only view every channel may use."""

    def __init__(self, mandates: Iterable[Mandate] = DEMO_MANDATES):
        self._history: dict[str, list[Mandate]] = {}
        for mandate in mandates:
            if mandate.customerId in self._history:
                raise ValueError(f"duplicate mandate for {mandate.customerId}")
            self._history[mandate.customerId] = [mandate]

    def default(self, customer_id: str) -> Mandate:
        """The shipped mandate (version 1 as configured), the target of a reset."""
        return self._history[customer_id][0]

    def differences_from_default(self, customer_id: str) -> list[tuple[str, Any, Any]]:
        """`(field, current, default)` for every buyer-admin field that differs from the shipped mandate."""
        current, default = self.get(customer_id), self.default(customer_id)
        return [
            (name, getattr(current, name), getattr(default, name))
            for name in Mandate.model_fields
            if name not in _IMMUTABLE and getattr(current, name) != getattr(default, name)
        ]

    def reset(self, customer_id: str, *, actor: str, now: datetime) -> tuple[Mandate, AuditEntry]:
        """Restore the shipped values as a NEW version (history is never rewritten), audited as a reset."""
        default = self.default(customer_id)
        changes = {name: getattr(default, name) for name, _, _ in self.differences_from_default(customer_id)}
        if not changes:
            raise ValueError("the mandate is already at its default")
        updated, entry = change_mandate(self.get(customer_id), changes, actor=actor, now=now)
        detail = entry.summary.removeprefix("Policy changed by buyer admin: ")
        entry = entry.model_copy(update={
            "event": "MANDATE_RESET",
            "summary": f"Policy reset to default by buyer admin: {detail}",
        })
        self._history[customer_id].append(updated)
        return updated, entry

    def customers(self) -> tuple[str, ...]:
        return tuple(self._history)

    def get(self, customer_id: str) -> Mandate:
        return self._history[customer_id][-1]

    def get_version(self, customer_id: str, version: int) -> Mandate:
        for mandate in self._history[customer_id]:
            if mandate.version == version:
                return mandate
        raise KeyError(f"{customer_id} has no mandate version {version}")

    def change(
        self, customer_id: str, changes: Mapping[str, Any], *, actor: str, now: datetime
    ) -> tuple[Mandate, AuditEntry]:
        updated, entry = change_mandate(self.get(customer_id), changes, actor=actor, now=now)
        self._history[customer_id].append(updated)
        return updated, entry
