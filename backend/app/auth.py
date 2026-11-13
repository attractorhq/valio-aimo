from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(
    auto_error=False, description="DEMO ONLY: demo-restaurant-a, demo-hotel-b, demo-manager"
)


@dataclass(frozen=True)
class Actor:
    id: str
    role: str
    buyer_id: str | None


# Deliberately public demo identities, NOT production authentication.
DEMO_IDENTITIES = {
    "demo-restaurant-a": Actor("buyer-restaurant-a", "buyer", "restaurant-a"),
    "demo-hotel-b": Actor("buyer-hotel-b", "buyer", "hotel-b"),
    "demo-manager": Actor("manager-demo", "manager", None),
}


def get_actor(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]) -> Actor:
    actor = DEMO_IDENTITIES.get(credentials.credentials) if credentials else None
    if not actor:
        raise HTTPException(
            401, "Use a documented demo bearer identity.", headers={"WWW-Authenticate": "Bearer"}
        )
    return actor


def require_buyer(actor: Actor):
    if actor.role != "buyer":
        raise HTTPException(403, "A buyer identity is required.")


def require_manager(actor: Actor):
    if actor.role != "manager":
        raise HTTPException(403, "A manager identity is required for human review.")


def authorize_buyer(actor: Actor, buyer_id: str):
    if actor.role != "manager" and actor.buyer_id != buyer_id:
        raise HTTPException(403, "This resource belongs to another buyer.")
