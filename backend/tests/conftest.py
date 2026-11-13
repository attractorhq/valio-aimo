import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db.models import Base
from app.db.seed import seed
from app.db.session import get_db
from app.integrations.mock_valio import MockValioProvider
from app.main import app, get_provider

BUYER = {"Authorization": "Bearer demo-restaurant-a"}
HOTEL = {"Authorization": "Bearer demo-hotel-b"}
MANAGER = {"Authorization": "Bearer demo-manager"}


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        seed(db)
    provider = MockValioProvider()
    provider.prices = dict(provider.prices)
    provider.stock = dict(provider.stock)

    def override_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_provider] = lambda: provider
    settings = get_settings().model_copy(update={"deepseek_api_key": "", "mock_mode": True})
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    # Runner reads the settings accessor directly; fixture patches it in tests that need it.
    client = TestClient(app)
    yield client, provider, factory, settings
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def payload(context):
    client = context[0]
    day = client.get("/api/v1/context", headers=BUYER).json()["next_monday"]
    return {
        "items": [{"product_code": "DEMO-MLK-02", "quantity": "100", "unit": "L"}],
        "delivery_date": day,
        "budget": "200.00",
        "required_dietary": ["lactose_free"],
        "request": "100 litres of lactose-free milk for Monday, maximum EUR 200",
    }


def propose(client, payload, headers=BUYER):
    result = client.post("/api/v1/proposals", json=payload, headers=headers)
    assert result.status_code == 201, result.text
    return result.json()


def commit(client, pid, headers=BUYER, key="test-commit-key"):
    return client.post(f"/api/v1/proposals/{pid}/commit", headers={**headers, "Idempotency-Key": key})
