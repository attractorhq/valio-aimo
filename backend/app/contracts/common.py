"""Shared v0.1 wire primitives. Decimal values serialize as strings, never floats."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

VERSION = "0.1"


def _format_decimal_plain(value: Decimal) -> str:
    """Format a Decimal in plain fixed-point notation without scientific exponents or trailing '.0'."""
    norm = value.normalize()
    return str(int(norm)) if norm == norm.to_integral() else format(norm, "f")


PlainDecimal = Annotated[
    Decimal,
    PlainSerializer(_format_decimal_plain, return_type=str, when_used="json"),
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        changed = False
        for field_info in cls.model_fields.values():
            if field_info.annotation is Decimal:
                field_info.annotation = PlainDecimal
                changed = True
            elif field_info.annotation == (Decimal | None):
                field_info.annotation = PlainDecimal | None
                changed = True
        if changed:
            cls.model_rebuild(force=True)
        super().__pydantic_init_subclass__(**kwargs)


Money = Annotated[
    Decimal,
    Field(ge=0, max_digits=16, decimal_places=2, allow_inf_nan=False),
    PlainSerializer(lambda value: format(value, ".2f"), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": r"^\d+\.\d{2}$"}, mode="serialization"),
]
NonNegativeDecimal = Annotated[
    Decimal,
    Field(ge=0, allow_inf_nan=False),
    PlainSerializer(_format_decimal_plain, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": r"^\d+(\.\d+)?$"}, mode="serialization"),
]
PositiveDecimal = Annotated[NonNegativeDecimal, Field(gt=0)]
Identifier = Annotated[str, Field(min_length=1, max_length=100)]
OfferHash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
RequestUnit = Literal["l", "kg", "pcs", "cases"]
TrustLevel = Literal["VERIFIED", "DECLARED", "ESTIMATE", "UNKNOWN"]
DecisionOutcome = Literal["AUTO", "ASK", "BLOCK"]
ProposalState = Literal[
    "OFFERED", "PROPOSED", "AUTO_APPROVED", "PENDING_APPROVAL", "APPROVED",
    "RECHECKED", "CONFIRMED_SIMULATED", "BLOCKED", "REJECTED", "EXPIRED", "SUPERSEDED",
]
ApprovalAction = Literal["APPROVE", "REJECT"]


def format_money(value: Decimal) -> str:
    return f"€{value:,.2f}"


def format_date(value: date) -> str:
    # Explicit English names: the wire/UI contract must not depend on process locale.
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{weekdays[value.weekday()]} {value.day} {months[value.month - 1]}"


def format_time(value: datetime) -> str:
    return f"{format_date(value)} {value:%H:%M}"
