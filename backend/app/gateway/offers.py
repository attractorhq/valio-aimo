"""getOffer: a live (or recorded) offer for the shortlist (brief §7.4–7.7, patch P1.2, P1.5).

- One batched availability call and one batched price call per evaluation (`get_offer`).
- Sizing (§7.5): sales units = ceil(needed ÷ content per sales unit), in allowedLotSize multiples.
- Allocation (§7.7): candidates are taken in the agent's order; a candidate with verified stock below the
  remaining need is used up to its stock, then the next candidate fills the rest. Stock of a product that
  appears under several items is one pool, never counted twice.
- Totals in Decimal EUR excl. VAT; VAT only from the price API, rounded once per VAT rate.
- offerHash (P1.2) covers lines, unit prices, VAT rates, quantities, delivery dates, cut-offs and trust
  labels, so any change in those changes the hash.
- validUntil = min(createdAt + 15 min, earliest price validTo of the lines).

Line eligibility reuses the policy line rules (L1–L14) with no request requirements: hard requirements and
the AUTO/ASK/BLOCK decision belong to createProposal. Pure apart from `get_offer`'s single lookup.
"""

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Protocol

from app.catalog.normalise import apply_price_ratio_content
from app.contracts import (
    ContentPerSalesUnit,
    ExcludedProduct,
    GetOfferRequest,
    Mandate,
    Offer,
    OfferItem,
    OfferLine,
    Product,
    Quantity,
    ReasonCode,
    RequestItem,
    Totals,
    TrustLabel,
    TrustSource,
    format_date,
)
from app.policy import ContentBasis, LineInput, LineVerdict, StockKind, evaluate_line
from app.valio import ProductFacts, ValioSnapshot

OFFER_VALIDITY = timedelta(minutes=15)
CENT = Decimal("0.01")
PIECE_UNITS = frozenset({"ST", "PCS", "PC", "H87", "PR", "PTN", "EA"})
WEIGHT_UNITS = frozenset({"KG", "KGM"})
VOLUME_UNITS = frozenset({"L", "LTR"})
_SALES_UNIT_LABELS = {**{u: "pc" for u in PIECE_UNITS}, **{u: "kg" for u in WEIGHT_UNITS},
                      **{u: "l" for u in VOLUME_UNITS}}


class Lookup(Protocol):
    def lookup(self, shortlists, requested_date, customer_id, *, products=None, force_refresh=False) -> ValioSnapshot:
        ...


@dataclass(frozen=True)
class Sizing:
    quantity_sales_units: Decimal
    delivered: Quantity
    content: ContentPerSalesUnit | None
    basis: ContentBasis
    unit_mismatch: bool


def content_basis(content: ContentPerSalesUnit | None) -> ContentBasis:
    """§5.2 methods: 1 unit conversions (DECLARED), 2 price ratio, per-kg sales units, 3 name only."""
    if content is None:
        return ContentBasis.UNKNOWN
    method = content.method or ""
    if method.startswith("unitConversions.") or method == "salesUnit":
        return ContentBasis.DECLARED
    if method == "catalogPrice.value / comparisonPrice.value":
        return ContentBasis.PRICE_RATIO
    if method in ("salesUnit.KG", "variableUnit"):
        return ContentBasis.PER_KG
    if method == "name":
        return ContentBasis.NAME
    return ContentBasis.DECLARED if content.level == "DECLARED" else ContentBasis.UNKNOWN


def _ceil_to_lot(units: Decimal, lot: Decimal) -> Decimal:
    whole = units.to_integral_value(rounding=ROUND_CEILING)
    return (whole / lot).to_integral_value(rounding=ROUND_CEILING) * lot


def _floor_to_lot(units: Decimal, lot: Decimal) -> Decimal:
    whole = units.to_integral_value(rounding=ROUND_FLOOR)
    return (whole / lot).to_integral_value(rounding=ROUND_FLOOR) * lot


def size(product: Product, needed: Decimal, unit: str) -> Sizing:
    """How many sales units deliver at least `needed` of `unit` (§7.5). Never guesses a pack content."""
    lot = product.allowedLotSize
    sales_unit = product.salesUnit.upper()
    content = product.contentPerSalesUnit
    if unit == "pcs" and sales_unit in PIECE_UNITS and (content is None or content.unit == "pcs"):
        content = ContentPerSalesUnit(value=Decimal(1), unit="pcs", level="DECLARED", method="salesUnit")
    elif unit == "cases" and sales_unit not in PIECE_UNITS | WEIGHT_UNITS | VOLUME_UNITS:
        # The buyer counts the supplier's own case-type sales unit; one case = one sales unit.
        content = ContentPerSalesUnit(value=Decimal(1), unit="cases", level="DECLARED", method="salesUnit")
    basis = content_basis(content)
    if content is None or basis is ContentBasis.UNKNOWN:
        return Sizing(lot, Quantity(value=needed, unit=unit), content, ContentBasis.UNKNOWN, False)
    if content.unit != unit:
        return Sizing(lot, Quantity(value=needed, unit=unit), content, basis, True)
    units = max(_ceil_to_lot(needed / content.value, lot), lot)
    return Sizing(units, Quantity(value=units * content.value, unit=unit), content, basis, False)


def _stock_kind(facts: ProductFacts) -> StockKind:
    availability = facts.availability
    if ReasonCode.NOT_AVAILABLE in availability.reasons and availability.stock_tier != "UNAVAILABLE":
        return StockKind.NOT_DELIVERABLE  # unknown id, no cut-off, unknown logistics model
    return {
        "EXACT": StockKind.EXACT, "PLENTY": StockKind.PLENTY, "SUPPLIER": StockKind.SUPPLIER,
        "UNAVAILABLE": StockKind.OUT,
    }.get(availability.stock_tier, StockKind.NOT_DELIVERABLE)


def _reliability(product: Product) -> Decimal | None:
    for label in product.facts:
        if label.fact == "reliability" and label.value is not None:
            try:
                return Decimal(str(label.value))
            except ArithmeticError:
                return None
    return None


def _line_total(units: Decimal, unit_price: Decimal | None) -> Decimal:
    return (units * (unit_price or Decimal(0))).quantize(CENT, rounding=ROUND_HALF_UP)


def _line_input(line_id: str, item_id: str, product: Product, facts: ProductFacts, sizing: Sizing) -> LineInput:
    price, availability = facts.price, facts.availability
    stock = _stock_kind(facts)
    return LineInput(
        line_id=line_id,
        item_id=item_id,
        product=product,
        quantity_sales_units=sizing.quantity_sales_units,
        delivered_quantity=sizing.delivered,
        line_total_excl_vat=_line_total(sizing.quantity_sales_units, price.unit_price),
        price_valid=price.unit_price is not None and price.vat_rate is not None,
        sales_blocked=bool(availability.sales_block),
        stock=stock,
        stock_amount=availability.available_quantity if stock is StockKind.EXACT else None,
        available_date=availability.delivery_date,
        closing_time=availability.closing_time,
        content_basis=sizing.basis,
        unit_mismatch=sizing.unit_mismatch or ReasonCode.UNIT_MISMATCH in availability.reasons,
        reliability=_reliability(product),
        facts=facts.facts,
    )


@dataclass(frozen=True)
class OfferResult:
    offer: Offer
    verdicts: tuple[LineVerdict, ...]  # the evaluated offer lines, in line order (createProposal re-decides)
    partial_items: frozenset[str] = frozenset()  # items explicitly offered in part (patch P2.2/P2.5, P8 B2)


def _format_qty_amount(value: Decimal) -> str:
    norm = value.normalize()
    return str(int(norm)) if norm == norm.to_integral() else format(norm, "f")


def _partial_coverage_label(
    delivered: Quantity,
    requested: Quantity,
    delivery_date,
    *,
    retrieved_at: datetime | None = None,
) -> TrustLabel:
    deliv_str = f"{_format_qty_amount(delivered.value)} {delivered.unit}"
    req_str = f"{_format_qty_amount(requested.value)} {requested.unit}"
    date_part = f" on {format_date(delivery_date)}" if delivery_date is not None else ""
    return TrustLabel(
        fact="coverage.partial",
        value=f"{_format_qty_amount(delivered.value)}/{_format_qty_amount(requested.value)} {requested.unit}",
        display=f"Partial delivery: {deliv_str} of {req_str}{date_part}",
        level="VERIFIED",
        source=TrustSource(
            system="valio-aimo.availability-api",
            field="scheduleLines[0].availableAmount",
            retrievedAt=retrieved_at,
        ),
        method="partial-delivery allocation (P2.5, P8 B2)",
        conflict=None,
    )


def _neutral_item(item_id: str, needed: Decimal, unit: str) -> RequestItem:
    # Offer eligibility only: hard requirements are checked against the structured request at createProposal.
    return RequestItem(itemId=item_id, what="offer item", quantity=needed, unit=unit)


def _hash_payload(offer_fields: dict) -> str:
    def strip_volatile(label: dict) -> dict:
        # retrievedAt changes on every read without any change in the fact itself.
        source = {k: v for k, v in (label.get("source") or {}).items() if k != "retrievedAt"}
        return {**label, "source": source}

    payload = {
        "customerId": offer_fields["customerId"],
        "requestedDate": offer_fields["requestedDate"],
        "mode": offer_fields["mode"],
        "lines": [
            {**{k: v for k, v in line.items() if k != "facts"}, "facts": [strip_volatile(f) for f in line["facts"]]}
            for line in offer_fields["lines"]
        ],
        "totals": offer_fields["totals"],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _totals(lines: Sequence[OfferLine]) -> Totals:
    excl = sum((line.lineTotalExclVat for line in lines), Decimal(0))
    by_rate: dict[Decimal, Decimal] = {}
    for line in lines:
        by_rate[line.vatRate] = by_rate.get(line.vatRate, Decimal(0)) + line.lineTotalExclVat
    vat = sum(((base * rate / 100).quantize(CENT, rounding=ROUND_HALF_UP) for rate, base in by_rate.items()),
              Decimal(0))
    return Totals(exclVat=excl.quantize(CENT), vat=vat.quantize(CENT), inclVat=(excl + vat).quantize(CENT))


def build_offer(
    request: GetOfferRequest,
    *,
    products: Mapping[str, Product],
    snapshot: ValioSnapshot,
    mandate: Mandate,
    offer_id: str | None = None,
    offer_version: int = 1,
) -> OfferResult:
    """Size, allocate and price the shortlist from one snapshot. Deterministic for the same inputs."""
    if snapshot.customer_id != request.customerId or mandate.customerId != request.customerId:
        raise ValueError("snapshot, mandate and request must belong to the same customer")
    if snapshot.requested_date != request.requestedDate:
        raise ValueError("snapshot was fetched for a different requested date")

    grouped: OrderedDict[str, list[OfferItem]] = OrderedDict()
    for entry in request.items:
        grouped.setdefault(entry.itemId, []).append(entry)

    used: dict[str, Decimal] = {}  # sales units already allocated per product (one stock pool)
    lines: list[OfferLine] = []
    verdicts: list[LineVerdict] = []
    excluded: list[ExcludedProduct] = []
    valid_tos: list[datetime] = []
    partial_item_ids: set[str] = set()
    now = snapshot.now

    for item_id, candidates in grouped.items():
        units_of = {c.unit for c in candidates}
        quantities = {c.quantity for c in candidates}
        if len(units_of) != 1 or len(quantities) != 1:
            raise ValueError(f"item {item_id}: every candidate must carry the same quantity and unit")
        unit, requested_qty = candidates[0].unit, candidates[0].quantity
        remaining = requested_qty
        item_line_indices: list[int] = []
        for candidate in candidates:
            product = products.get(candidate.productId)
            facts = snapshot.products.get(candidate.productId)
            if product is None or facts is None:
                raise ValueError(f"product {candidate.productId} is not in the catalogue or the snapshot")
            price = facts.price
            product = apply_price_ratio_content(product, price.unit_price, price.comparison_price,
                                                price.comparison_unit)
            sizing = size(product, remaining if remaining > 0 else requested_qty, unit)
            line_id = f"L{len(lines) + 1}"
            probe = _line_input(line_id, item_id, product, facts, sizing)
            verdict = evaluate_line(probe, item=_neutral_item(item_id, remaining if remaining > 0 else requested_qty, unit),
                                    mandate=mandate, requested_date=request.requestedDate, now=now)
            if verdict.status == "EXCLUDED":
                excluded.append(ExcludedProduct(
                    productId=product.productId, name=product.name, itemId=item_id,
                    reasons=[ReasonCode(r) for r in verdict.exclusion_codes], facts=[*facts.facts, *product.facts],
                ))
                continue

            if remaining <= 0:
                continue

            units = sizing.quantity_sales_units
            if probe.stock is StockKind.EXACT:
                free = max(probe.stock_amount - used.get(product.productId, Decimal(0)), Decimal(0))
                if free < units:
                    units = _floor_to_lot(free, product.allowedLotSize)
                    if units <= 0:
                        excluded.append(ExcludedProduct(
                            productId=product.productId, name=product.name, itemId=item_id,
                            reasons=[ReasonCode.NOT_AVAILABLE], facts=[*facts.facts, *product.facts],
                        ))
                        continue
                    sizing = Sizing(units, Quantity(value=units * sizing.content.value, unit=unit),
                                    sizing.content, sizing.basis, sizing.unit_mismatch)
            final = _line_input(line_id, item_id, product, facts, sizing)
            if final.stock is StockKind.EXACT:
                # The pool left for this line after earlier items used the same product.
                final = replace(final, stock_amount=probe.stock_amount - used.get(product.productId, Decimal(0)))
            verdict = evaluate_line(final, item=_neutral_item(item_id, remaining, unit), mandate=mandate,
                                    requested_date=request.requestedDate, now=now)
            used[product.productId] = used.get(product.productId, Decimal(0)) + units
            remaining -= sizing.delivered.value
            if price.valid_to is not None:
                valid_tos.append(price.valid_to)
            item_line_indices.append(len(lines))
            lines.append(OfferLine(
                lineId=line_id,
                itemId=item_id,
                productId=product.productId,
                name=product.name,
                nameFi=product.names.get("fi"),
                salesUnit=product.salesUnit,
                salesUnitLabel=_SALES_UNIT_LABELS.get(product.salesUnit.upper(), product.salesUnit.lower()),
                contentPerSalesUnit=sizing.content,
                quantitySalesUnits=units,
                deliveredQuantity=sizing.delivered,
                unitPriceExclVat=price.unit_price,
                lineTotalExclVat=final.line_total_excl_vat,
                vatRate=price.vat_rate,
                deliveryDate=facts.availability.delivery_date,
                closingTime=facts.availability.closing_time,
                facts=[*facts.facts, *product.facts],
                reasons=[ReasonCode(r) for r in verdict.reasons],
            ))
            verdicts.append(verdict)

        if item_line_indices and remaining > 0:
            partial_item_ids.add(item_id)
            delivered_total = sum((lines[idx].deliveredQuantity.value for idx in item_line_indices), Decimal(0))
            cov_label = _partial_coverage_label(
                Quantity(value=delivered_total, unit=unit),
                Quantity(value=requested_qty, unit=unit),
                lines[item_line_indices[0]].deliveryDate,
                retrieved_at=snapshot.captured_at,
            )
            for idx in item_line_indices:
                ln = lines[idx]
                updated_reasons = [ReasonCode(r) for r in ln.reasons]
                if ReasonCode.PARTIAL_AVAILABILITY not in updated_reasons:
                    updated_reasons.append(ReasonCode.PARTIAL_AVAILABILITY)
                lines[idx] = ln.model_copy(update={
                    "reasons": updated_reasons,
                    "facts": [*ln.facts, cov_label],
                })
                vd = verdicts[idx]
                verdicts[idx] = replace(
                    vd,
                    line=replace(vd.line, facts=(*vd.line.facts, cov_label)),
                )

    created = snapshot.captured_at
    valid_until = min([created + OFFER_VALIDITY, *valid_tos])
    totals = _totals(lines)
    fields = {
        "customerId": request.customerId,
        "requestedDate": request.requestedDate.isoformat(),
        "mode": snapshot.mode,
        "lines": [line.model_dump(mode="json") for line in lines],
        "totals": totals.model_dump(mode="json"),
    }
    offer_hash = _hash_payload(fields)
    offer = Offer(
        offerId=offer_id or f"OF-{created.date().isoformat()}-{offer_hash[:12]}",
        offerVersion=offer_version,
        offerHash=offer_hash,
        customerId=request.customerId,
        requestedDate=request.requestedDate,
        createdAt=created,
        validUntil=valid_until,
        mode=snapshot.mode,
        recordedAt=snapshot.recorded_at,
        lines=lines,
        excluded=excluded,
        totals=totals,
    )
    return OfferResult(offer=offer, verdicts=tuple(verdicts), partial_items=frozenset(partial_item_ids))


def shortlists(request: GetOfferRequest) -> dict[str, list[str]]:
    grouped: OrderedDict[str, list[str]] = OrderedDict()
    for entry in request.items:
        codes = grouped.setdefault(entry.itemId, [])
        if entry.productId not in codes:
            codes.append(entry.productId)
    return dict(grouped)


def get_offer(
    request: GetOfferRequest,
    *,
    products: Mapping[str, Product],
    provider: Lookup,
    mandate: Mandate,
    offer_id: str | None = None,
    offer_version: int = 1,
    force_refresh: bool = False,
) -> OfferResult:
    """One batched price + one batched availability read for the whole shortlist, then `build_offer`."""
    lists = shortlists(request)
    wanted = {code for codes in lists.values() for code in codes}
    missing = sorted(code for code in wanted if code not in products)
    if missing:
        raise ValueError(f"unknown product ids: {', '.join(missing)}")
    snapshot = provider.lookup(lists, request.requestedDate, request.customerId,
                               products={code: products[code] for code in wanted}, force_refresh=force_refresh)
    return build_offer(request, products=products, snapshot=snapshot, mandate=mandate,
                       offer_id=offer_id, offer_version=offer_version)
