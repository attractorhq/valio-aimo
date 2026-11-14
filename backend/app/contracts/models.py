"""Versioned gateway data contracts. No lookup, pricing, policy, or transition logic."""

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field

from .common import (
    Contract,
    DecisionOutcome,
    Identifier,
    Money,
    NonNegativeDecimal,
    OfferHash,
    PositiveDecimal,
    ProposalState,
    RequestUnit,
    TrustLevel,
)
from .reasons import ReasonCode, ReasonEffect
from .trust import TrustLabel


class Quantity(Contract):
    value: PositiveDecimal
    unit: RequestUnit


class ContentPerSalesUnit(Quantity):
    level: TrustLevel
    method: str | None = None


class UnitConversion(Contract):
    unitId: str
    sizeInBaseUnits: PositiveDecimal


class AllergenDeclaration(Contract):
    code: str
    status: Literal["CONTAINS", "MAY_CONTAIN", "FREE_FROM", "NOT_LISTED", "UNDECLARED", "UNKNOWN"]
    evidence: TrustLabel


class Deposit(Contract):
    productId: Identifier
    quantity: PositiveDecimal
    unitPriceExclVat: Money | None = None


class Product(Contract):
    productId: Identifier
    name: str
    names: dict[str, str] = Field(default_factory=dict)
    category: str
    isFood: bool | None = None
    salesUnit: str
    baseUnit: str
    allowedLotSize: PositiveDecimal
    units: list[UnitConversion] = Field(default_factory=list)
    contentPerSalesUnit: ContentPerSalesUnit | None = None
    variableWeight: bool = False
    hasAllergenDeclaration: bool = False
    allergens: list[AllergenDeclaration] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    countryOfOrigin: str | None = None
    ingredients: dict[str, str] = Field(default_factory=dict)
    substitutions: list[Identifier] = Field(default_factory=list)
    deposits: list[Deposit] = Field(default_factory=list)
    facts: list[TrustLabel] = Field(default_factory=list)


class Requirement(Contract):
    kind: Literal["CLAIM", "AVOID_ALLERGEN"]
    code: str = Field(min_length=1)


class Preference(Contract):
    kind: Literal["BRAND", "ORGANIC", "ORIGIN", "LABEL", "FAT_PERCENT", "PACK_SIZE", "OTHER"]
    value: str


class RequestItem(Contract):
    itemId: Identifier
    what: str = Field(min_length=1)
    quantity: PositiveDecimal
    unit: RequestUnit
    productId: Identifier | None = None
    requirements: list[Requirement] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)


class StructuredRequest(Contract):
    requestText: str = Field(max_length=4000)
    requestedDate: date
    items: list[RequestItem] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)


class Totals(Contract):
    exclVat: Money
    vat: Money
    inclVat: Money
    currency: Literal["EUR"] = "EUR"


class ExcludedProduct(Contract):
    productId: Identifier
    name: str
    itemId: Identifier | None = None
    reasons: list[ReasonCode] = Field(min_length=1)
    facts: list[TrustLabel] = Field(default_factory=list)
    substituteFor: Identifier | None = None


class OfferLine(Contract):
    lineId: Identifier
    itemId: Identifier
    productId: Identifier
    name: str
    nameFi: str | None = None
    salesUnit: str
    salesUnitLabel: str
    contentPerSalesUnit: ContentPerSalesUnit
    quantitySalesUnits: PositiveDecimal
    deliveredQuantity: Quantity
    unitPriceExclVat: NonNegativeDecimal
    lineTotalExclVat: Money
    vatRate: NonNegativeDecimal
    deliveryDate: date
    closingTime: AwareDatetime | None
    facts: list[TrustLabel]
    reasons: list[ReasonCode] = Field(default_factory=list)
    substituteFor: Identifier | None = None
    substitutionSource: Literal["VALIO", "AGENT"] | None = None


class Offer(Contract):
    offerId: Identifier
    offerVersion: int = Field(ge=1)
    offerHash: OfferHash
    customerId: Identifier
    requestedDate: date
    createdAt: AwareDatetime
    validUntil: AwareDatetime
    mode: Literal["LIVE", "RECORDED"]
    recordedAt: AwareDatetime | None = None
    lines: list[OfferLine]
    excluded: list[ExcludedProduct] = Field(default_factory=list)
    totals: Totals


class Decision(Contract):
    decision: DecisionOutcome
    reasons: list[ReasonCode]
    approverRole: str | None = None


class Proposal(Decision):
    proposalId: Identifier
    customerId: Identifier
    mandateVersion: int = Field(ge=1)
    offerId: Identifier
    offerVersion: int = Field(ge=1)
    offerHash: OfferHash
    state: ProposalState
    request: StructuredRequest
    requestedDate: date
    createdAt: AwareDatetime
    validUntil: AwareDatetime
    mode: Literal["LIVE", "RECORDED"]
    recordedAt: AwareDatetime | None = None
    lines: list[OfferLine]
    excluded: list[ExcludedProduct] = Field(default_factory=list)
    totals: Totals
    supersededBy: Identifier | None = None
    simulationNotice: Literal["Simulated order — mock environment"] = "Simulated order — mock environment"


class Approver(Contract):
    name: str
    role: str


class Mandate(Contract):
    customerId: Identifier
    version: int = Field(ge=1)
    organisationName: str
    segment: str
    allergenMode: Literal["STANDARD", "STRICT"]
    autoApproveLimitEur: Money
    hardLimitEur: Money
    autoAcceptValioSubstitutes: bool
    autoAcceptAgentSubstitutes: Literal[False] = False
    autoAcceptSupplierStock: bool
    minReliability: NonNegativeDecimal = Field(le=1)
    enforceCutoff: bool = True
    forbiddenAllergens: list[str] = Field(default_factory=list)
    approver: Approver


class AuditEntry(Contract):
    entryId: Identifier
    customerId: Identifier
    proposalId: Identifier | None = None
    offerId: Identifier | None = None
    offerVersion: int | None = Field(default=None, ge=1)
    offerHash: OfferHash | None = None
    mandateVersion: int = Field(ge=1)
    timestamp: AwareDatetime
    actor: str
    event: str
    fromState: ProposalState | None = None
    toState: ProposalState | None = None
    reasons: list[ReasonCode] = Field(default_factory=list)
    rule: str | None = None
    facts: list[TrustLabel] = Field(default_factory=list)
    summary: str
    comment: str | None = None


class ApproverView(Contract):
    """Never serialize this model into the agent channel or an agent tool result."""

    proposal: Proposal
    approver: Approver
    approvalStatement: str | None = None
    approvalToken: str = Field(min_length=1)
    approvalUrl: str | None = None
    qrPayload: str | None = None
    expiresAt: AwareDatetime
    permissionNotice: Literal["Single-use approval link"] = "Single-use approval link"


class OfferItem(Contract):
    itemId: Identifier
    productId: Identifier
    quantity: PositiveDecimal
    unit: RequestUnit


class GetOfferRequest(Contract):
    customerId: Identifier
    requestedDate: date
    items: list[OfferItem] = Field(min_length=1, max_length=1000)


class CreateProposalRequest(Contract):
    customerId: Identifier
    offerId: Identifier
    offerVersion: int = Field(ge=1)
    request: StructuredRequest


class GetProposalRequest(Contract):
    proposalId: Identifier


class GetMandateRequest(Contract):
    customerId: Identifier


class GetAuditTrailRequest(Contract):
    # XOR selection is enforced by the API layer; neither field is an approval capability.
    proposalId: Identifier | None = None
    customerId: Identifier | None = None


class AuditTrail(Contract):
    entries: list[AuditEntry]


class ReasonOccurrence(Contract):
    """Optional rendered reason for UI/logs; code lists remain the proposal wire shape."""

    code: ReasonCode
    effect: ReasonEffect
    message: str
