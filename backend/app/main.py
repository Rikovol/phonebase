import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import app.models  # noqa: F401 — регистрация metadata
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import analytics, auth, avito, competitor_prices, imports, logs, personal_data, photos, products, purchase_docs, stores, users
from app.api import settings as settings_api
from app.core.config import settings
from app.core.database import Base, engine
from app.db_migrations import (
    migrate_add_avito_api_columns,
    migrate_add_is_new_column,
    migrate_add_purchased_at,
    migrate_add_sim_completeness,
    migrate_add_website_feed_columns,
    migrate_admin_clear_store,
    migrate_create_avito_tables,
    migrate_info_clear_store,
    migrate_legacy_role_manager_to_staff,
    migrate_seed_competitor_prices,
)
from app.seed import seed_if_empty


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate_add_is_new_column()
    await migrate_add_website_feed_columns()
    await migrate_add_avito_api_columns()
    await migrate_add_sim_completeness()
    await migrate_create_avito_tables()
    await migrate_add_purchased_at()
    await seed_if_empty()
    await migrate_legacy_role_manager_to_staff()
    await migrate_admin_clear_store()
    await migrate_info_clear_store()
    await migrate_seed_competitor_prices()

    from app.services.auto_import import auto_import_loop

    import_task = asyncio.create_task(auto_import_loop())
    yield
    import_task.cancel()


app = FastAPI(
    title="PhoneBase API",
    version="1.3.2",
    docs_url="/api/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

if settings.ENVIRONMENT == "development":
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
else:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_csv(settings.ALLOWED_HOSTS))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_csv(settings.CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(stores.router, prefix="/api/stores", tags=["stores"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(products.router, prefix="/api/products", tags=["products"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(imports.router, prefix="/api/imports", tags=["imports"])
app.include_router(photos.router, prefix="/api/photos", tags=["photos"])
app.include_router(personal_data.router, prefix="/api/pd", tags=["personal_data"])
app.include_router(purchase_docs.router, prefix="/api/purchase-docs", tags=["purchase_docs"])
app.include_router(avito.router, prefix="/api/avito", tags=["avito"])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])
app.include_router(competitor_prices.router, prefix="/api/competitor-prices", tags=["competitor_prices"])

_media_root = Path(settings.MEDIA_ROOT)
_media_root.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(_media_root)), name="media")
Path(settings.PURCHASE_DOCS_ROOT).mkdir(parents=True, exist_ok=True)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
