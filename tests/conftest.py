import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database.session import Base, get_db
from app.main import app
from app.core.config import get_settings
from app.services.marketplace import seed_marketplace_catalog


@pytest.fixture(autouse=True)
def ollama_offline_by_default(monkeypatch):
    """Keep ordinary API tests deterministic and independent of a local model."""
    monkeypatch.setattr(get_settings(), "ollama_base_url", "http://127.0.0.1:1")
    from app.services import assistant

    def unavailable(*_args, **_kwargs):
        from urllib.error import URLError

        raise URLError("Ollama is intentionally unavailable in tests")

    monkeypatch.setattr(assistant.urllib.request, "urlopen", unavailable)


@pytest.fixture
def client():
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    testing_session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)
    with testing_session() as seed_db:
        seed_marketplace_catalog(seed_db)

    def override_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    # Do not enter the application lifespan here: it seeds the user's normal
    # workspace database. The test dependency override owns this isolated DB.
    yield TestClient(app)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
