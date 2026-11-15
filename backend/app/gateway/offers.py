"""getOffer: a live (or recorded) offer for the shortlist (brief §7.4–7.7, patch P1.2, P1.5).

- One batched availability call and one batched price call per evaluation (`get_offer`).
- Sizing (§7.5): sales units = ceil(needed ÷ content per sales unit), in allowedLotSize multiples.
- Allocation (§7.7): candidates are taken in the agent's order; a candidate with verified stock below the
  remaining need is used up to its stock, then the next candidate fills the rest. Stock of a product that
  appears under several items is one pool, never counted twice.
- Totals in Decimal EUR excl. VAT; VAT only from the price API, rounded once per VAT rate.
- offerHash (P1.2) covers customerId, requestedDate, mode, ordered lines (unit prices, VAT,
  quantities, delivery dates, cut-offs and trust labels), totals, ordered excluded product/item ids,
  and offerId when no confirmable lines remain. Read timestamps and expiry are omitted; identical
  snapshots with the same inputs hash identically.
- validUntil = min(createdAt + 15 min, earliest price validTo of the lines).

Line eligibility reuses the policy line rules (L1–L14) with no request requirements: hard requirements and
the AUTO/ASK/BLOCK decision belong to createProposal. Pure apart from `get_offer`'s single lookup.
"""

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Protocol

from app.catalog.normalise import allergen_declaration, apply_price_ratio_content
from app.contracts import (
    Conflict,
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
    Requirement,
    Totals,
    TrustLabel,
    TrustSource,
    format_date,
)
from app.policy import ContentBasis, LineInput, LineVerdict, StockKind, evaluate_line
from app.policy.requirements import K6_MESSAGE, allergen_status, claim_conflicts, hard_requirements
from app.trust.labels import SOURCE_CLAIM, allergen_name, claim_label, packaging_label
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


def _format_qty_comma(value: Decimal) -> str:
    norm = value.normalize()
    return f"{int(norm):,}" if norm == norm.to_integral() else format(norm, ",f")


def _partial_stock_pack_explanation(
    product: Product,
    sizing: Sizing,
    units: Decimal,
    free: Decimal,
) -> str | None:
    """Explain why the offered quantity is below the shown stock when floored to whole packs/lots (T51)."""
    if units >= free or sizing.content is None or sizing.content.value <= 0:
        return None
    unit = sizing.delivered.unit
    deliv_str = f"{_format_qty_amount(sizing.delivered.value)} {unit}"
    lot = product.allowedLotSize
    cpsu_val = sizing.content.value
    if lot > 1:
        pack_size = lot * cpsu_val
        num_packs = units / lot
        stock_in_unit = free * cpsu_val
        return (
            f"{deliv_str} = {_format_qty_amount(num_packs)} × {_format_qty_amount(pack_size)} {unit} packs; "
            f"{_format_qty_amount(stock_in_unit)} {unit} in stock"
        )
    if cpsu_val != 1:
        return (
            f"{deliv_str} = {_format_qty_amount(units)} × {_format_qty_amount(cpsu_val)} {unit} packs; "
            f"{_format_qty_amount(free)} in stock"
        )
    return (
        f"{deliv_str} = {_format_qty_amount(units)} × 1 {unit} packs; "
        f"{_format_qty_amount(free)} {unit} in stock"
    )


def _stock_value_str(val: Decimal) -> str:
    return _format_qty_amount(val)


def format_held_stock_note(
    *,
    left_qty: Decimal,
    raw_qty: Decimal,
    held_qty: Decimal,
    unit: str,
    proposal_ids: Sequence[str] = (),
) -> str:
    """T102: '35 kg left for you: 95 kg in stock, 60 kg already in your earlier order today (PR-…)'."""
    left_s = f"{_format_qty_comma(left_qty)} {unit}"
    raw_s = f"{_format_qty_comma(raw_qty)} {unit}"
    held_s = f"{_format_qty_comma(held_qty)} {unit}"
    unique_ids = list(dict.fromkeys(pid for pid in proposal_ids if pid))
    if len(unique_ids) == 1:
        order_part = f"your earlier order today ({unique_ids[0]})"
    elif len(unique_ids) > 1:
        order_part = f"your earlier orders today ({', '.join(unique_ids)})"
    else:
        order_part = "your earlier orders today"
    return f"{left_s} left for you: {raw_s} in stock, {held_s} already in {order_part}"


def _with_held_stock_facts(
    facts: Sequence[TrustLabel],
    *,
    held_sales: Decimal,
    raw_sales: Decimal,
    free_sales: Decimal,
    content_val: Decimal,
    unit: str,
    proposal_ids: Sequence[str] = (),
    retrieved_at: datetime | None = None,
) -> list[TrustLabel]:
    """T102: adjust `stock.quantity` to what remains for this buyer and emit `stock.heldByYourOrders` next to it."""
    left_phys = free_sales * content_val
    raw_phys = raw_sales * content_val
    held_phys = held_sales * content_val
    unique_ids = list(dict.fromkeys(pid for pid in proposal_ids if pid))
    note = format_held_stock_note(
        left_qty=left_phys,
        raw_qty=raw_phys,
        held_qty=held_phys,
        unit=unit,
        proposal_ids=unique_ids,
    )
    held_label = TrustLabel(
        fact="stock.heldByYourOrders",
        value={
            "rawStockSalesUnits": _stock_value_str(raw_sales),
            "heldSalesUnits": _stock_value_str(held_sales),
            "availableSalesUnits": _stock_value_str(free_sales),
            "rawStockQuantity": f"{_format_qty_comma(raw_phys)} {unit}",
            "heldQuantity": f"{_format_qty_comma(held_phys)} {unit}",
            "availableQuantity": f"{_format_qty_comma(left_phys)} {unit}",
            "unit": unit,
            "proposalIds": unique_ids,
        },
        display=note,
        level="VERIFIED",
        source=TrustSource(
            system="derived",
            field="session.committedOrders",
            retrievedAt=retrieved_at,
        ),
        method="session stock deduction (T97/T102)",
        conflict=None,
    )
    out: list[TrustLabel] = []
    inserted = False
    for f in facts:
        if f.fact == "stock.quantity":
            out.append(
                f.model_copy(
                    update={
                        "value": _stock_value_str(free_sales),
                        "display": note,
                        "method": "session-adjusted stock (T97/T102)",
                    }
                )
            )
            out.append(held_label)
            inserted = True
        elif f.fact == "stock.heldByYourOrders":
            continue
        else:
            out.append(f)
    if not inserted:
        out.append(held_label)
    return out


def _with_stock_display(facts: Sequence[TrustLabel], display: str) -> list[TrustLabel]:
    return [
        f.model_copy(update={"display": display}) if f.fact == "stock.quantity" else f
        for f in facts
    ]


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


# --------------------------------------------------------------------------- requirement evidence (T41)

_ALLERGEN_STATUS_TEXT = {
    "CONTAINS": ("Contains {name}", "DECLARED"),
    "MAY_CONTAIN": ("May contain {name}", "DECLARED"),
    "FREE_FROM": ("Free from {name}", "DECLARED"),
    "NOT_LISTED": ("Not listed ({name})", "DECLARED"),
    "UNKNOWN": ("Unknown", "UNKNOWN"),
}


def _allergen_requirement_fact(product: Product, code: str, conflicts: frozenset[str]) -> TrustLabel:
    status = allergen_status(product, code)  # exactly what rule L4 decides on
    evidence = allergen_declaration(product, code).evidence  # where it is declared: source, field, method
    if not (evidence.value == status or (status == "UNKNOWN" and evidence.level == "UNKNOWN")):
        # The catalogue helper reads the declaration differently: the policy's reading wins, since it decided.
        text, level = _ALLERGEN_STATUS_TEXT[status]
        evidence = evidence.model_copy(update={
            "value": None if status == "UNKNOWN" else status,
            "display": text.format(name=allergen_name(code)),
            "level": level,
            "method": "policy:L4",
        })
    if code == "AM" and "K6" in conflicts and evidence.conflict is None:
        evidence = evidence.model_copy(update={"conflict": Conflict(code="K6", message=K6_MESSAGE)})
    return evidence.model_copy(update={"fact": f"allergen.{code}"})


def _claim_requirement_fact(product: Product, code: str) -> TrustLabel:
    if code in product.claims:
        return next((f for f in product.facts if f.fact == f"claim.{code}"), None) or claim_label(code)
    if code in product.labels:  # rule L4 accepts a packaging label for the claim
        label = next((f for f in product.facts if f.fact == f"label.{code}"), None) or packaging_label(code)
        return label.model_copy(update={"fact": f"claim.{code}", "value": True, "method": f"label:{code}"})
    return TrustLabel(fact=f"claim.{code}", value=False, display="Not claimed by the supplier", level="UNKNOWN",
                      source=SOURCE_CLAIM, method=None, conflict=None)


def requirement_facts(product: Product, requirements: Sequence[Requirement], mandate: Mandate) -> list[TrustLabel]:
    """One TrustLabel per hard requirement (the item's plus the mandate's forbidden allergens).

    Built from the same evaluation the policy runs in rule L4 (`allergen_status`, claims or labels), so the
    trust card, the decision text and the decision log show the facts the decision actually used.
    """
    conflicts = claim_conflicts(product)
    return [
        _claim_requirement_fact(product, r.code) if r.kind == "CLAIM"
        else _allergen_requirement_fact(product, r.code, conflicts)
        for r in hard_requirements(requirements, mandate)
    ]


def with_requirement_facts(
    facts: Sequence[TrustLabel], product: Product, requirements: Sequence[Requirement], mandate: Mandate
) -> list[TrustLabel]:
    """Requirement facts first, replacing any fact with the same key, so each key appears once."""
    evaluated = requirement_facts(product, requirements, mandate)
    keys = {f.fact for f in evaluated}
    return [*evaluated, *(f for f in facts if f.fact not in keys)]


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
    if not offer_fields["lines"]:
        payload["offerId"] = offer_fields["offerId"]
        payload["excluded"] = [
            {"productId": entry["productId"], "itemId": entry["itemId"]}
            for entry in offer_fields["excluded"]
        ]
    if lookup_date := offer_fields.get("lookupDate"):
        payload["lookupDate"] = lookup_date
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
    committed_units: Mapping[tuple[str, date], Decimal] | None = None,
    committed_orders: Mapping[tuple[str, date], Sequence[tuple[str, Decimal]]] | None = None,
) -> OfferResult:
    """Size, allocate and price the shortlist from one snapshot. Deterministic for the same inputs.

    `committed_units` (T97) are the sales units this customer already has in committed orders, per product and
    delivery date. They come off the stock pool first, so a second order the same day never quotes stock the
    buyer's own earlier order already holds.
    """
    if snapshot.customer_id != request.customerId or mandate.customerId != request.customerId:
        raise ValueError("snapshot, mandate and request must belong to the same customer")
    if snapshot.requested_date != (request.lookupDate or request.requestedDate):
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
            stock_key = (product.productId, facts.availability.delivery_date)
            order_refs = list((committed_orders or {}).get(stock_key, ()))
            held = (committed_units or {}).get(
                stock_key,
                sum((qty for _, qty in order_refs), Decimal(0)),
            )
            held_pids = [pid for pid, _ in order_refs]
            if held > 0 and product.productId not in used:
                used[product.productId] = held
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
            pack_explanation: str | None = None
            free_before_line: Decimal | None = None
            if probe.stock is StockKind.EXACT:
                free = max(probe.stock_amount - used.get(product.productId, Decimal(0)), Decimal(0))
                free_before_line = free
                if free < units:
                    units = _floor_to_lot(free, product.allowedLotSize)
                    if units <= 0:
                        ex_facts = [*facts.facts, *product.facts]
                        if held > 0 and sizing.content is not None:
                            ex_facts = _with_held_stock_facts(
                                ex_facts,
                                held_sales=held,
                                raw_sales=probe.stock_amount,
                                free_sales=free,
                                content_val=sizing.content.value,
                                unit=sizing.content.unit,
                                proposal_ids=held_pids,
                                retrieved_at=snapshot.captured_at,
                            )
                        excluded.append(ExcludedProduct(
                            productId=product.productId, name=product.name, itemId=item_id,
                            reasons=[ReasonCode.NOT_AVAILABLE], facts=ex_facts,
                        ))
                        continue
                    sizing = Sizing(units, Quantity(value=units * sizing.content.value, unit=unit),
                                    sizing.content, sizing.basis, sizing.unit_mismatch)
                    if held <= 0:
                        pack_explanation = _partial_stock_pack_explanation(product, sizing, units, free)
            final = _line_input(line_id, item_id, product, facts, sizing)
            if final.stock is StockKind.EXACT:
                # The pool left for this line after earlier items used the same product.
                final = replace(final, stock_amount=probe.stock_amount - used.get(product.productId, Decimal(0)))
                if pack_explanation is not None:
                    final = replace(final, facts=tuple(_with_stock_display(final.facts, pack_explanation)))
            verdict = evaluate_line(final, item=_neutral_item(item_id, remaining, unit), mandate=mandate,
                                    requested_date=request.requestedDate, now=now)
            used[product.productId] = used.get(product.productId, Decimal(0)) + units
            remaining -= sizing.delivered.value
            if price.valid_to is not None:
                valid_tos.append(price.valid_to)
            item_line_indices.append(len(lines))
            line_facts = [*facts.facts, *product.facts]
            if pack_explanation is not None:
                line_facts = _with_stock_display(line_facts, pack_explanation)
            if held > 0 and probe.stock is StockKind.EXACT and sizing.content is not None:
                line_facts = _with_held_stock_facts(
                    line_facts,
                    held_sales=held,
                    raw_sales=probe.stock_amount,
                    free_sales=free_before_line if free_before_line is not None else max(probe.stock_amount - held, Decimal(0)),
                    content_val=sizing.content.value,
                    unit=sizing.content.unit,
                    proposal_ids=held_pids,
                    retrieved_at=snapshot.captured_at,
                )
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
                facts=line_facts,
                reasons=[ReasonCode(r) for r in verdict.reasons],
            ))
            verdicts.append(verdict)

        if item_line_indices and remaining > 0:
            partial_item_ids.add(item_id)
            delivered_total = sum((lines[idx].deliveredQuantity.value for idx in item_line_indices), Decimal(0))
            shortfall = requested_qty - delivered_total
            deliv_date = lines[item_line_indices[0]].deliveryDate
            date_str = format_date(deliv_date) if deliv_date is not None else format_date(request.requestedDate)
            shortfall_clause = (
                f"{_format_qty_comma(delivered_total)} {unit} now; "
                f"{_format_qty_comma(shortfall)} {unit} cannot be met by {date_str}"
            )
            cov_label = _partial_coverage_label(
                Quantity(value=delivered_total, unit=unit),
                Quantity(value=requested_qty, unit=unit),
                deliv_date,
                retrieved_at=snapshot.captured_at,
            )
            for idx in item_line_indices:
                ln = lines[idx]
                updated_reasons = [ReasonCode(r) for r in ln.reasons]
                if ReasonCode.PARTIAL_AVAILABILITY not in updated_reasons:
                    updated_reasons.append(ReasonCode.PARTIAL_AVAILABILITY)
                updated_facts = [
                    f.model_copy(update={"display": f"{f.display} · {shortfall_clause}"})
                    if (
                        f.fact == "stock.quantity"
                        and f.display
                        and shortfall_clause not in f.display
                        and not any(other.fact == "stock.heldByYourOrders" for other in ln.facts)
                    )
                    else f
                    for f in ln.facts
                ]
                lines[idx] = ln.model_copy(update={
                    "reasons": updated_reasons,
                    "facts": [*updated_facts, cov_label],
                })
                vd = verdicts[idx]
                vd_facts = tuple(
                    f.model_copy(update={"display": f"{f.display} · {shortfall_clause}"})
                    if f.fact == "stock.quantity" and f.display and shortfall_clause not in f.display
                    else f
                    for f in vd.line.facts
                )
                verdicts[idx] = replace(
                    vd,
                    line=replace(vd.line, facts=(*vd_facts, cov_label)),
                )

    created = snapshot.captured_at
    valid_until = min([created + OFFER_VALIDITY, *valid_tos])
    totals = _totals(lines)
    fields = {
        "offerId": offer_id or "",
        "customerId": request.customerId,
        "requestedDate": request.requestedDate.isoformat(),
        "lookupDate": request.lookupDate.isoformat() if request.lookupDate else None,
        "mode": snapshot.mode,
        "lines": [line.model_dump(mode="json") for line in lines],
        "totals": totals.model_dump(mode="json"),
        "excluded": [entry.model_dump(mode="json") for entry in excluded],
    }
    if offer_id is None:
        # Derive a repeatable identity from the snapshot first, then commit that identity into the
        # final hash. This avoids a circular offerId/hash definition and preserves offline replay.
        fields["offerId"] = f"OF-{created.date().isoformat()}-{_hash_payload(fields)[:12]}"
    offer_hash = _hash_payload(fields)
    offer = Offer(
        offerId=fields["offerId"],
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
    committed_units: Mapping[tuple[str, date], Decimal] | None = None,
    committed_orders: Mapping[tuple[str, date], Sequence[tuple[str, Decimal]]] | None = None,
) -> OfferResult:
    """One batched price + one batched availability read for the whole shortlist, then `build_offer`."""
    lists = shortlists(request)
    wanted = {code for codes in lists.values() for code in codes}
    missing = sorted(code for code in wanted if code not in products)
    if missing:
        raise ValueError(f"unknown product ids: {', '.join(missing)}")
    snapshot = provider.lookup(lists, request.lookupDate or request.requestedDate, request.customerId,
                               products={code: products[code] for code in wanted}, force_refresh=force_refresh)
    return build_offer(
        request,
        products=products,
        snapshot=snapshot,
        mandate=mandate,
        offer_id=offer_id,
        offer_version=offer_version,
        committed_units=committed_units,
        committed_orders=committed_orders,
    )
