from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api.marketplace import router as marketplace_router
from app.api.routes import router
from app.core.config import PROJECT_ROOT, get_settings
from app.database.migrations import apply_schema_migrations
from app.database.session import SessionLocal, engine
from app.models import ApplicationSetting  # noqa: F401 - register all table metadata
from app.services.marketplace import seed_marketplace_catalog
from app.services.seed import seed_demo_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_schema_migrations(engine)
    with SessionLocal() as db:
        if get_settings().seed_demo_data:
            seed_demo_data(db)
            seed_marketplace_catalog(db)
        db.merge(ApplicationSetting(key="simulation_only", value="true"))
        db.merge(ApplicationSetting(key="assistant_model", value=get_settings().ollama_model))
        db.commit()
    yield


app = FastAPI(title="PayPilot AI", description="Unified local transaction, marketplace, analytics, fraud, and PayPal Sandbox intelligence.", version="3.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Conversation-Session", "X-Workspace-Admin-Token"],
)
app.include_router(router)
app.include_router(marketplace_router)
frontend = PROJECT_ROOT / "frontend"
if frontend.exists():
    app.mount("/static", StaticFiles(directory=frontend), name="static")
product_upload_dir = get_settings().product_upload_dir
product_upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=product_upload_dir), name="merchant-product-images")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(frontend / "index.html")
