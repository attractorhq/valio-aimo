"""Agent Trust Labels v0.1; classification and conflict detection live elsewhere."""

from typing import Literal

from pydantic import AwareDatetime

from .common import Contract, TrustLevel

type FactValue = str | bool | int | float | None | list[FactValue] | dict[str, FactValue]


class TrustSource(Contract):
    system: Literal[
        "valio-aimo.price-api", "valio-aimo.availability-api", "valio-aimo.product-data",
        "valio-aimo.substitution-rules", "valio-aimo.delivery-reliability",
        "valio-aimo.carbon-footprint", "derived", "agent",
    ]
    field: str
    retrievedAt: AwareDatetime | None


class Conflict(Contract):
    code: Literal["K1", "K2", "K3", "K4", "K5", "K6"]
    message: str


class TrustLabel(Contract):
    fact: str
    value: FactValue
    display: str
    level: TrustLevel
    source: TrustSource
    method: str | None = None
    conflict: Conflict | None = None
