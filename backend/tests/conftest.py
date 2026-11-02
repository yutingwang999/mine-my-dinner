from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from minemydinner.db import get_session
from minemydinner.main import app
from minemydinner.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture(scope="session")
def engine(test_settings: Settings) -> Iterator[Engine]:
    test_engine = create_engine(test_settings.test_database_url)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after each test."""
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as test_session:
            yield test_session
        transaction.rollback()


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()
