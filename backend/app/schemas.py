from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3, allow_inf_nan=False)]
Unit = Literal["L", "KG", "EA"]
Status = Literal["AUTO_APPROVED", "NEEDS_APPROVAL", "REJECTED", "APPROVED", "COMMITTED"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductOut(StrictModel):
    code: str
    name: str
    category: str
    unit: Unit
    pack_quantity: Quantity
    dietary: dict[str, bool | None] = Field(default_factory=dict)
    allergens: list[str] | None = None
    nutrition: dict | None = None
    reliability: float | None = Field(default=None, ge=0, le=1)
    carbon_kg_co2_per_kg: float | None = Field(default=None, ge=0)
    carbon_scope: Literal["category_average"] | None = None
    source: str

    @field_validator("allergens")
    @classmethod
    def canonical_allergens(cls, values):
        if values is None:
            return None
        if any(not value.strip() for value in values):
            raise ValueError("Allergen tokens cannot be empty.")
        return sorted({value.strip().casefold() for value in values})


class LineItem(StrictModel):
    product_code: str = Field(min_length=1, max_length=100)
    quantity: Quantity
    unit: Unit
    substitute_for: str | None = Field(default=None, max_length=100)


class OfferRequest(StrictModel):
    items: list[LineItem] = Field(min_length=1, max_length=30)
    delivery_date: date


class ProposalRequest(OfferRequest):
    request: str = Field(default="Structured purchase request", max_length=4000)
    budget: Money | None = None
    excluded_allergens: list[str] = Field(default_factory=list, max_length=30)
    required_dietary: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("excluded_allergens", "required_dietary")
    @classmethod
    def canonical_constraints(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError("Constraint tokens cannot be empty.")
        return sorted({value.strip().casefold() for value in values})


class OfferLine(StrictModel):
    product_code: str
    product_name: str
    quantity: Quantity
    unit: Unit
    pack_quantity: Quantity
    unit_price: Money
    line_total: Money
    available: bool
    available_quantity: Annotated[Decimal, Field(ge=0)]
    earliest_delivery: date
    source: str
    verified_at: datetime


class OfferOut(StrictModel):
    items: list[OfferLine]
    delivery_date: date
    total: Money
    currency: Literal["EUR"] = "EUR"
    price_basis: str = "Illustrative net prices, excluding VAT; no delivery fee in simulator."
    mock_data: bool
    disclaimer: str = "Point-in-time check only; stock is not reserved."


class Reason(StrictModel):
    code: str
    message: str


class OrderOut(StrictModel):
    id: str
    proposal_id: str
    simulated: bool
    source: str
    total: Money
    currency: Literal["EUR"] = "EUR"
    created_at: datetime


class ProposalOut(StrictModel):
    id: str
    buyer_id: str
    buyer_name: str
    status: Status
    request: ProposalRequest
    offer: OfferOut
    reasons: list[Reason]
    policy_version: int
    approved_by: str | None = None
    order: OrderOut | None = None
    created_at: datetime
    simulated_commit: bool = True


class ReviewRequest(StrictModel):
    note: str = Field(min_length=3, max_length=1000)


class PolicyOut(StrictModel):
    buyer_id: str
    name: str
    version: int
    max_auto_total: Money
    allowed_categories: list[str]
    allow_equivalent_substitutions: bool
    equivalent_substitutions: dict[str, list[str]]
    excluded_allergens: list[str]
    unusual_quantity: Quantity
    confirmed_availability_required: bool = True


class PolicyUpdate(StrictModel):
    max_auto_total: Money


class AuditOut(StrictModel):
    id: int
    proposal_id: str | None
    buyer_id: str
    actor: str
    event: str
    summary: str
    data: dict
    created_at: datetime


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatOut(StrictModel):
    message: str
    mode: Literal["ai", "guided"]
    tool_results: list[dict] = Field(default_factory=list)
    proposal: ProposalOut | None = None
