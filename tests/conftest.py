import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models  # noqa: F401  (registers models on Base.metadata)
from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.routers.chat import CHAT_RATE_LIMITER
from backend.app.routers.conversations import CONVERSATION_CREATE_RATE_LIMITER


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Isolate tests from each other's rate-limit usage.

    The limiters are process-wide singletons (see
    backend/app/rate_limit.py), and Starlette's TestClient reports the same
    `request.client.host` for every call — so without this, one test's
    requests would count toward the next test's limit. Dedicated
    rate-limiting tests (tests/test_rate_limiting.py) rely on this running
    before *and* after each test.
    """
    CHAT_RATE_LIMITER.reset()
    CONVERSATION_CREATE_RATE_LIMITER.reset()
    yield
    CHAT_RATE_LIMITER.reset()
    CONVERSATION_CREATE_RATE_LIMITER.reset()


@pytest.fixture()
def db_session():
    """A fresh, isolated in-memory SQLite database for a single test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    """A TestClient wired to the isolated test database instead of the real one."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
