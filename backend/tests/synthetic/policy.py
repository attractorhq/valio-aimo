"""Hand-made, synthetic policy inputs. No record is copied from the dataset or from recorded API responses."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.contracts import (
    AllergenDeclaration,
    Conflict,
    Deposit,
    Product,
    Quantity,
    RequestItem,
    Requirement,
    StructuredRequest,
    TrustLabel,
    TrustSource,
)
from app.policy import ContentBasis, LineInput, StockKind

HELSINKI = timezone(timedelta(hours=2))
NOW = datetime(2026, 11, 13, 21, 50, tzinfo=HELSINKI)
MONDAY = date(2026, 11, 16)
CUTOFF_OPEN = datetime(2026, 11, 15, 17, 0, tzinfo=HELSINKI)
CUTOFF_PASSED = datetime(2026, 11, 12, 14, 0, tzinfo=HELSINKI)

GLUTEN_FREE = Requirement(kind="CLAIM", code="FREE_FROM_GLUTEN")
LACTOSE_FREE = Requirement(kind="CLAIM", code="FREE_FROM_LACTOSE")
MILK_FREE = Requirement(kind="AVOID_ALLERGEN", code="AM")


def label(fact: str, conflict: str | None = None) -> TrustLabel:
    return TrustLabel(
        fact=fact, value=None, display="synthetic", level="DECLARED",
        source=TrustSource(system="valio-aimo.product-data", field=fact, retrievedAt=None),
        conflict=Conflict(code=conflict, message="synthetic conflict") if conflict else None,
    )


def declaration(code: str, status: str) -> AllergenDeclaration:
    return AllergenDeclaration(code=code, status=status, evidence=label(f"allergen.{code}"))


def product(
    product_id: str = "SYN-1",
    *,
    allergens: dict[str, str] | None = None,
    declared: bool | None = None,
    claims: tuple[str, ...] = (),
    labels: tuple[str, ...] = (),
    is_food: bool | None = True,
    deposits: tuple[Deposit, ...] = (),
    facts: tuple[TrustLabel, ...] = (),
) -> Product:
    allergens = allergens or {}
    return Product(
        productId=product_id, name=f"Synthetic product {product_id}", category="synthetic", isFood=is_food,
        salesUnit="ST", baseUnit="ST", allowedLotSize=Decimal(1),
        hasAllergenDeclaration=bool(allergens) if declared is None else declared,
        allergens=[declaration(code, status) for code, status in allergens.items()],
        claims=list(claims), labels=list(labels), deposits=list(deposits), facts=list(facts),
    )


def item(
    item_id: str = "I1", quantity: str = "10", unit: str = "l", *, requirements=(), product_id: str | None = None
) -> RequestItem:
    return RequestItem(
        itemId=item_id, what="synthetic", quantity=Decimal(quantity), unit=unit,
        requirements=list(requirements), productId=product_id,
    )


def request(*items: RequestItem, text: str = "Synthetic request by Monday.", requested: date = MONDAY):
    return StructuredRequest(requestText=text, requestedDate=requested, items=list(items or (item(),)))


def line(
    line_id: str = "L1",
    item_id: str = "I1",
    *,
    prod: Product | None = None,
    qty: str = "10",
    delivered: str | None = None,
    unit: str = "l",
    total: str = "10.00",
    price_valid: bool = True,
    sales_blocked: bool = False,
    stock: StockKind = StockKind.PLENTY,
    stock_amount: str | None = None,
    available_date: date | None = MONDAY,
    closing_time: datetime | None = CUTOFF_OPEN,
    content: ContentBasis = ContentBasis.DECLARED,
    unit_mismatch: bool = False,
    reliability: str | None = None,
    substitution: str | None = None,
    substitute_for: str | None = None,
    product_type_differs: bool = False,
    facts: tuple[TrustLabel, ...] = (),
) -> LineInput:
    return LineInput(
        line_id=line_id, item_id=item_id, product=prod or product(),
        quantity_sales_units=Decimal(qty), delivered_quantity=Quantity(value=Decimal(delivered or qty), unit=unit),
        line_total_excl_vat=Decimal(total), price_valid=price_valid, sales_blocked=sales_blocked, stock=stock,
        stock_amount=None if stock_amount is None else Decimal(stock_amount), available_date=available_date,
        closing_time=closing_time, content_basis=content, unit_mismatch=unit_mismatch,
        reliability=None if reliability is None else Decimal(reliability), substitution=substitution,
        substitute_for=substitute_for, product_type_differs=product_type_differs, facts=facts,
    )
