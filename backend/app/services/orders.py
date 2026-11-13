from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import Actor, authorize_buyer, require_buyer, require_manager
from app.db.models import AuditEvent, Order, Policy, Proposal
from app.integrations.mock_valio import helsinki_today
from app.integrations.provider import IntegrationError
from app.schemas import (
    OfferLine,
    OfferOut,
    OfferRequest,
    OrderOut,
    PolicyOut,
    ProposalOut,
    ProposalRequest,
    Reason,
)
from app.services.catalog import product_details
from app.services.policy import evaluate


def audit(db, actor, buyer_id, event, summary, data=None, proposal_id=None):
    db.add(
        AuditEvent(
            actor=actor.id,
            buyer_id=buyer_id,
            event=event,
            summary=summary,
            data=data or {},
            proposal_id=proposal_id,
        )
    )


def get_policy(db: Session, buyer_id: str) -> PolicyOut:
    policy = db.get(Policy, buyer_id)
    if not policy:
        raise HTTPException(400, "Unknown buyer policy.")
    return PolicyOut(buyer_id=buyer_id, name=policy.name, version=policy.version, **policy.data)


def check_offer(db: Session, provider, request: OfferRequest) -> OfferOut:
    if request.delivery_date < helsinki_today():
        raise HTTPException(400, "Delivery date is in the past in Europe/Helsinki.")
    codes = [i.product_code for i in request.items]
    if len(set(codes)) != len(codes):
        raise HTTPException(400, "Combine duplicate product codes into a single line.")
    products = {code: product_details(db, code) for code in codes}
    for line in request.items:
        product = products[line.product_code]
        if line.unit != product.unit:
            raise HTTPException(400, f"{product.code}: expected {product.unit}; no implicit unit conversion.")
        if line.quantity % product.pack_quantity != 0:
            raise HTTPException(
                400, f"{product.code}: quantity must be a multiple of {product.pack_quantity} {product.unit}."
            )
        if line.substitute_for:
            product_details(db, line.substitute_for)
    try:
        prices = provider.get_prices(codes)
        stock = provider.check_availability(codes, request.delivery_date)
        lines = []
        for line in request.items:
            p, price, available = (
                products[line.product_code],
                prices[line.product_code],
                stock[line.product_code],
            )
            if price.currency != "EUR" or price.unit_price < 0:
                raise IntegrationError("Provider returned unsupported price terms.")
            lines.append(
                OfferLine(
                    product_code=p.code,
                    product_name=p.name,
                    quantity=line.quantity,
                    unit=line.unit,
                    pack_quantity=p.pack_quantity,
                    unit_price=price.unit_price,
                    line_total=(line.quantity * price.unit_price).quantize(Decimal("0.01")),
                    available=available.available_quantity >= line.quantity
                    and available.earliest_delivery <= request.delivery_date,
                    available_quantity=available.available_quantity,
                    earliest_delivery=available.earliest_delivery,
                    source=f"{price.source}; {available.source}",
                    verified_at=min(price.verified_at, available.verified_at),
                )
            )
        return OfferOut(
            items=lines,
            delivery_date=request.delivery_date,
            total=sum((line.line_total for line in lines), Decimal("0.00")),
            mock_data=provider.mock,
        )
    except (IntegrationError, KeyError) as exc:
        raise HTTPException(
            502, str(exc) if isinstance(exc, IntegrationError) else "Incomplete provider response."
        ) from exc


def get_proposal(db, actor, proposal_id):
    p = db.get(Proposal, proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found.")
    authorize_buyer(actor, p.buyer_id)
    return p


def serialize(p: Proposal) -> ProposalOut:
    return ProposalOut(
        id=p.id,
        buyer_id=p.buyer_id,
        status=p.status,
        created_at=p.created_at,
        **p.data,
    )


def create_proposal(db, provider, actor: Actor, request: ProposalRequest):
    require_buyer(actor)
    policy = get_policy(db, actor.buyer_id)
    offer = check_offer(db, provider, request)
    products = {i.product_code: product_details(db, i.product_code) for i in request.items}
    status, reasons = evaluate(policy, request, offer, products)
    p = Proposal(
        buyer_id=actor.buyer_id,
        status=status,
        data={
            "buyer_name": policy.name,
            "request": request.model_dump(mode="json"),
            "offer": offer.model_dump(mode="json"),
            "reasons": [r.model_dump() for r in reasons],
            "policy_version": policy.version,
            "approved_by": None,
            "order": None,
        },
    )
    db.add(p)
    db.flush()
    audit(db, actor, p.buyer_id, "REQUEST_RECEIVED", request.request, request.model_dump(mode="json"), p.id)
    audit(
        db,
        actor,
        p.buyer_id,
        "OFFER_VERIFIED",
        "Fixed price and delivery-date stock checked; not reserved.",
        offer.model_dump(mode="json"),
        p.id,
    )
    audit(
        db,
        actor,
        p.buyer_id,
        "POLICY_EVALUATED",
        f"{status}: " + "; ".join(r.message for r in reasons),
        {"policy": policy.model_dump(mode="json"), "reasons": [r.model_dump() for r in reasons]},
        p.id,
    )
    db.commit()
    return serialize(p)


def save_transition(db, p, status, data):
    # Compare-and-swap protects all transitions, including simultaneous reviews/commits.
    changed = db.execute(
        update(Proposal)
        .where(
            Proposal.id == p.id,
            Proposal.revision == p.revision,
        )
        .values(status=status, data=data, revision=p.revision + 1),
        execution_options={"synchronize_session": False},
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Proposal changed concurrently; reload and retry.")
    db.expire(p)


def terms(offer: OfferOut):
    # Stock fluctuation within sufficient availability is not a price/terms change.
    return (
        [(i.product_code, i.quantity, i.unit, i.unit_price) for i in offer.items],
        offer.total,
        offer.currency,
    )


def revalidate(db, provider, p, actor):
    request = ProposalRequest.model_validate(p.data["request"])
    policy = get_policy(db, p.buyer_id)
    offer = check_offer(db, provider, request)
    products = {i.product_code: product_details(db, i.product_code) for i in request.items}
    status, reasons = evaluate(policy, request, offer, products)
    audit(
        db,
        actor,
        p.buyer_id,
        "OFFER_REVALIDATED",
        "Current price, stock and policy rechecked.",
        {"offer": offer.model_dump(mode="json"), "policy": policy.model_dump(mode="json")},
        p.id,
    )
    return policy, offer, status, reasons


def review(db, provider, actor, proposal_id, approve, note):
    require_manager(actor)
    p = get_proposal(db, actor, proposal_id)
    if p.status != "NEEDS_APPROVAL":
        raise HTTPException(409, "Only pending proposals can be reviewed.")
    data = dict(p.data)
    if approve:
        policy, offer, status, reasons = revalidate(db, provider, p, actor)
        # A manager cannot sign terms they have not yet seen.
        changed_terms = terms(offer) != terms(OfferOut.model_validate(p.data["offer"]))
        policy_changed = policy.version != p.data["policy_version"]
        prior_exceptions = {(r["code"], r["message"]) for r in p.data["reasons"]}
        new_risks = status == "NEEDS_APPROVAL" and any(
            (r.code, r.message) not in prior_exceptions for r in reasons
        )
        data.update(
            offer=offer.model_dump(mode="json"),
            policy_version=policy.version,
            reasons=[r.model_dump() for r in reasons],
        )
        if status == "REJECTED" or changed_terms or policy_changed or new_risks:
            if changed_terms:
                data["reasons"].append(
                    Reason(
                        code="PRICE_CHANGED", message="Prices changed; review refreshed terms."
                    ).model_dump()
                )
            if policy_changed:
                data["reasons"].append(
                    Reason(
                        code="POLICY_CHANGED", message="Policy changed; review its current version."
                    ).model_dump()
                )
            save_transition(db, p, "REJECTED" if status == "REJECTED" else "NEEDS_APPROVAL", data)
            audit(
                db,
                actor,
                p.buyer_id,
                "REVIEW_BLOCKED",
                "Terms changed or hard constraints failed; approval not recorded.",
                {},
                p.id,
            )
            db.commit()
            raise HTTPException(409, "Review blocked. Reload refreshed terms and reasons.")
        data["approved_by"] = actor.id
        status = "APPROVED"
    else:
        status = "REJECTED"
        data["reasons"] = data["reasons"] + [Reason(code="HUMAN_REJECTED", message=note).model_dump()]
    save_transition(db, p, status, data)
    audit(
        db,
        actor,
        p.buyer_id,
        "HUMAN_APPROVED" if approve else "HUMAN_REJECTED",
        note,
        {"reviewed_offer": data["offer"], "policy_version": data["policy_version"]},
        p.id,
    )
    db.commit()
    return serialize(p)


def commit_order(db, provider, actor, proposal_id, idempotency_key):
    p = get_proposal(db, actor, proposal_id)
    existing = db.scalar(
        select(Order).where(Order.buyer_id == p.buyer_id, Order.idempotency_key == idempotency_key)
    )
    if existing:
        if existing.proposal_id != p.id:
            raise HTTPException(409, "Idempotency key was already used for another proposal.")
        return serialize(p)
    if p.status not in ("AUTO_APPROVED", "APPROVED"):
        raise HTTPException(409, "Commit requires an authorized, uncommitted proposal.")
    policy, offer, decision, reasons = revalidate(db, provider, p, actor)
    changed_terms = terms(offer) != terms(OfferOut.model_validate(p.data["offer"]))
    policy_changed = policy.version != p.data["policy_version"]
    old_exceptions = {(r["code"], r["message"]) for r in p.data["reasons"]}
    new_risks = decision == "NEEDS_APPROVAL" and any(
        (r.code, r.message) not in old_exceptions for r in reasons
    )
    data = dict(p.data)
    data.update(offer=offer.model_dump(mode="json"), policy_version=policy.version)
    if (
        decision == "REJECTED"
        or changed_terms
        or policy_changed
        or new_risks
        or (decision == "NEEDS_APPROVAL" and p.status != "APPROVED")
    ):
        if changed_terms:
            reasons.append(
                Reason(code="PRICE_CHANGED", message="Price changed after authorization; review new terms.")
            )
        if policy_changed:
            reasons.append(
                Reason(
                    code="POLICY_CHANGED", message="Buyer policy changed; a new human approval is required."
                )
            )
        data.update(reasons=[r.model_dump() for r in reasons], approved_by=None)
        save_transition(db, p, "REJECTED" if decision == "REJECTED" else "NEEDS_APPROVAL", data)
        audit(
            db,
            actor,
            p.buyer_id,
            "COMMIT_BLOCKED",
            "Revalidation invalidated authorization.",
            {"reasons": data["reasons"]},
            p.id,
        )
        db.commit()
        raise HTTPException(
            409, "Authorization invalidated; inspect refreshed proposal and review if permitted."
        )
    # Bind the final transaction to the policy version we just evaluated.
    guard = db.execute(
        update(Policy)
        .where(Policy.buyer_id == p.buyer_id, Policy.version == policy.version)
        .values(version=policy.version),
        execution_options={"synchronize_session": False},
    )
    if guard.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Policy changed concurrently; retry revalidation.")
    order = Order(proposal_id=p.id, buyer_id=p.buyer_id, idempotency_key=idempotency_key, data={})
    db.add(order)
    try:
        db.flush()
        result = OrderOut(
            id=order.id,
            proposal_id=p.id,
            simulated=True,
            source="Local order simulator — not sent to Valio",
            total=offer.total,
            created_at=order.created_at,
        )
        order.data = result.model_dump(mode="json")
        data["order"] = result.model_dump(mode="json")
        save_transition(db, p, "COMMITTED", data)
        audit(
            db,
            actor,
            p.buyer_id,
            "ORDER_SIMULATED",
            "Committed to local simulator. No order sent to Valio.",
            order.data,
            p.id,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        # A racing identical commit is a successful idempotent replay.
        existing = db.scalar(
            select(Order).where(Order.buyer_id == p.buyer_id, Order.idempotency_key == idempotency_key)
        )
        if not existing or existing.proposal_id != p.id:
            raise HTTPException(409, "Concurrent commit; reload proposal.") from None
        db.refresh(p)
    return serialize(p)
