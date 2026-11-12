"""Initialize SQLite and populate deterministic synthetic demo data."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database.migrations import apply_schema_migrations
from app.database.session import SessionLocal, engine
from app.models import ApplicationSetting  # noqa: F401
from app.services.marketplace import seed_marketplace_catalog
from app.services.seed import seed_demo_data

apply_schema_migrations(engine)
with SessionLocal() as session:
    added = seed_demo_data(session)
    products_added = seed_marketplace_catalog(session)
print(f"Database initialized. Added {added} local synthetic demo transactions and {products_added} catalogue products.")
