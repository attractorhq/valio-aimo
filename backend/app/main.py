from contextlib import asynccontextmanager
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent.runner import run_agent
from app.auth import Actor, get_actor, require_buyer, require_manager
from app.config import get_settings
from app.db.models import AuditEvent, Base, Policy, Proposal
from app.db.seed import seed
from app.db.session import SessionLocal, engine, get_db
from app.integrations.mock_valio import MockValioProvider, helsinki_today
from app.integrations.real_valio import RealValioProvider
from app.schemas import (
    AuditOut,
    ChatOut,
    ChatRequest,
    OfferOut,
    OfferRequest,
    PolicyOut,
    PolicyUpdate,
    ProductOut,
    ProposalOut,
    ProposalRequest,
    ReviewRequest,
)
from app.services import catalog, orders


@lru_cache
def get_provider():
    return MockValioProvider() if get_settings().mock_mode else RealValioProvider()


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        # Live mode must never seed synthetic product facts.
        if not get_settings().mock_mode:
            from app.db.models import Product

            if db.scalar(select(Product).where(Product.code.like("DEMO-%"))):
                raise RuntimeError(
                    "Live-data mode requires a separate database without DEMO fixture products."
                )
        seed(db, include_products=get_settings().mock_mode)
    yield


app = FastAPI(
    title="Aimo · Trusted Trade Gateway",
    version="0.1.0",
    lifespan=lifespan,
    description="Open buyer-agent API. Demo bearer authentication is NOT production security. "
    "Inquiries, proposals, human approvals and commits are distinct operations. "
    "All commits currently use a LOCAL SIMULATOR; no orders are sent to Valio. "
    "Money is returned as decimal strings in EUR. Quantities use explicit L/KG/EA base units.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[s.strip() for s in get_settings().frontend_origins.split(",") if s.strip()],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)
DB = Annotated[Session, Depends(get_db)]
Identity = Annotated[Actor, Depends(get_actor)]
Provider = Annotated[object, Depends(get_provider)]


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        status_code=400,
        content={
            "detail": "Request validation failed",
            "errors": [
                {"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()
            ],
        },
    )


@app.get("/health", tags=["Operations"])
def health(db: DB, provider: Provider):
    db.execute(select(1))
    return {
        "status": "ready",
        "mock_data": provider.mock,
        "simulated_orders": True,
        "live_adapter_ready": False,
        "authentication": "public demo identities — not production",
    }


@app.get("/api/v1/context", tags=["Discovery"])
def context(actor: Identity):
    today = helsinki_today()
    return {
        "today": today,
        "next_monday": today + timedelta(days=(7 - today.weekday()) % 7 or 7),
        "timezone": "Europe/Helsinki",
        "mock_data": get_provider().mock,
        "simulated_orders": True,
        "ai_configured": bool(get_settings().deepseek_api_key),
        "identity": {"id": actor.id, "role": actor.role, "buyer_id": actor.buyer_id},
    }


@app.get("/api/v1/products", response_model=list[ProductOut], tags=["Catalog"])
def products(
    db: DB,
    actor: Identity,
    provider: Provider,
    query: str = Query("", max_length=200),
    category: str | None = None,
    dietary: str | None = None,
    exclude_allergen: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    found = catalog.search(db, provider, query, category, dietary, exclude_allergen, limit)
    orders.audit(
        db,
        actor,
        actor.buyer_id or "manager",
        "CATALOG_SEARCH",
        f"Catalog search: {query or 'all products'}",
        {"query": query, "codes": [p.code for p in found]},
    )
    db.commit()
    return found


@app.get("/api/v1/products/{product_code}", response_model=ProductOut, tags=["Catalog"])
def product(product_code: str, db: DB, actor: Identity):
    return catalog.product_details(db, product_code)


@app.post("/api/v1/check-offer", response_model=OfferOut, tags=["Inquiries"])
def offer(request: OfferRequest, db: DB, actor: Identity, provider: Provider):
    result = orders.check_offer(db, provider, request)
    orders.audit(
        db,
        actor,
        actor.buyer_id or "manager",
        "OFFER_INQUIRY",
        "Price and availability inquiry; no purchase commitment.",
        result.model_dump(mode="json"),
    )
    db.commit()
    return result


@app.post("/api/v1/proposals", response_model=ProposalOut, status_code=201, tags=["Proposals"])
def propose(request: ProposalRequest, db: DB, actor: Identity, provider: Provider):
    return orders.create_proposal(db, provider, actor, request)


@app.get("/api/v1/proposals", response_model=list[ProposalOut], tags=["Proposals"])
def list_proposals(db: DB, actor: Identity, status: str | None = None):
    stmt = select(Proposal).order_by(Proposal.created_at.desc()).limit(100)
    if actor.role != "manager":
        stmt = stmt.where(Proposal.buyer_id == actor.buyer_id)
    if status:
        stmt = stmt.where(Proposal.status == status)
    return [orders.serialize(p) for p in db.scalars(stmt)]


@app.get("/api/v1/proposals/{proposal_id}", response_model=ProposalOut, tags=["Proposals"])
def proposal(proposal_id: str, db: DB, actor: Identity):
    return orders.serialize(orders.get_proposal(db, actor, proposal_id))


@app.post("/api/v1/proposals/{proposal_id}/approve", response_model=ProposalOut, tags=["Human review"])
def approve(proposal_id: str, request: ReviewRequest, db: DB, actor: Identity, provider: Provider):
    return orders.review(db, provider, actor, proposal_id, True, request.note)


@app.post("/api/v1/proposals/{proposal_id}/reject", response_model=ProposalOut, tags=["Human review"])
def reject(proposal_id: str, request: ReviewRequest, db: DB, actor: Identity, provider: Provider):
    return orders.review(db, provider, actor, proposal_id, False, request.note)


@app.post("/api/v1/proposals/{proposal_id}/commit", response_model=ProposalOut, tags=["Commitment"])
def commit(
    proposal_id: str,
    db: DB,
    actor: Identity,
    provider: Provider,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
):
    """Revalidate authorization and current terms, then commit exactly once into LOCAL simulation."""
    return orders.commit_order(db, provider, actor, proposal_id, idempotency_key)


@app.get("/api/v1/audit", response_model=list[AuditOut], tags=["Audit"])
def audit(db: DB, actor: Identity, proposal_id: str | None = None):
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(250)
    if actor.role != "manager":
        stmt = stmt.where(AuditEvent.buyer_id == actor.buyer_id)
    if proposal_id:
        orders.get_proposal(db, actor, proposal_id)
        stmt = stmt.where(AuditEvent.proposal_id == proposal_id)
    return [
        AuditOut(
            id=e.id,
            proposal_id=e.proposal_id,
            buyer_id=e.buyer_id,
            actor=e.actor,
            event=e.event,
            summary=e.summary,
            data=e.data,
            created_at=e.created_at,
        )
        for e in db.scalars(stmt)
    ]


@app.get("/api/v1/policies", response_model=list[PolicyOut], tags=["Policies"])
def policies(db: DB, actor: Identity):
    ids = list(db.scalars(select(Policy.buyer_id))) if actor.role == "manager" else [actor.buyer_id]
    return [orders.get_policy(db, buyer) for buyer in ids]


@app.patch("/api/v1/policies/{buyer_id}", response_model=PolicyOut, tags=["Policies"])
def update_policy(buyer_id: str, request: PolicyUpdate, db: DB, actor: Identity):
    require_manager(actor)
    if not get_settings().mock_mode:
        raise HTTPException(403, "Policy editing is limited to mock mode.")
    policy = db.get(Policy, buyer_id)
    if not policy:
        raise HTTPException(404, "Policy not found.")
    data = {**policy.data, "max_auto_total": str(request.max_auto_total)}
    result = db.execute(
        update(Policy)
        .where(Policy.buyer_id == buyer_id, Policy.version == policy.version)
        .values(data=data, version=policy.version + 1),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Policy changed concurrently; retry.")
    orders.audit(
        db,
        actor,
        buyer_id,
        "POLICY_UPDATED",
        "Manager changed illustrative automatic approval threshold.",
        {"old_version": policy.version, "new_limit": str(request.max_auto_total)},
    )
    db.commit()
    db.expire(policy)
    return orders.get_policy(db, buyer_id)


@app.post("/api/v1/agent/chat", response_model=ChatOut, tags=["Demo agent"])
def chat(request: ChatRequest, db: DB, actor: Identity, provider: Provider):
    require_buyer(actor)
    return run_agent(get_settings(), db, provider, actor, request.message)
