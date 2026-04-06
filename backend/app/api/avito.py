"""
API-эндпоинты Авито: статистика, мессенджер, синхронизация цен, мониторинг.
"""
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import and_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.access import can_modify_product
from app.api.auth import get_current_user, require_admin
from app.core.database import get_db
from app.models.business import AvitoMessage, AvitoStats, Product, Store, User
from app.services.website_feed import generate_website_feed

router = APIRouter()


# ── Публичный JSON-фид для сайта ─────────────────────────

@router.get("/website-feed/{store_id}.json")
async def website_feed(
    store_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Публичный JSON-фид б/у товаров для сайта магазина. Без авторизации."""
    store = await db.get(Store, store_id)
    if not store or not store.is_active:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not store.website_feed_enabled:
        raise HTTPException(status_code=403, detail="Фид для сайта отключён")

    return await generate_website_feed(db, store_id)


# ── Pydantic-схемы ───────────────────────────────────────

class AvitoCredentialsIn(BaseModel):
    client_id: str
    client_secret: str


class StatsOut(BaseModel):
    product_id: str
    date: str
    views: int
    contacts: int
    favorites: int


class MessageOut(BaseModel):
    id: str
    chat_id: str
    direction: str
    author_id: str
    content: str
    created_at: str


class FeedStatusOut(BaseModel):
    report_id: Optional[str] = None
    active: int = 0
    errors: int = 0
    mapped: int = 0
    error_msg: Optional[str] = None


# ── Credentials (admin only) ─────────────────────────────

@router.post("/credentials/{store_id}")
async def save_avito_credentials(
    store_id: str,
    body: AvitoCredentialsIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Сохранить Avito API credentials для магазина. Проверяет валидность, получая токен."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    # Проверяем credentials — пытаемся получить токен
    from app.services.avito_api import AvitoAPIClient, AvitoAPIError

    client = AvitoAPIClient(client_id=body.client_id, client_secret=body.client_secret)
    profile = {}
    try:
        async with client:
            await client._ensure_token()
            # Подтягиваем профиль продавца
            try:
                profile = await client.get_profile()
            except AvitoAPIError:
                pass  # credentials валидны, но профиль не удалось получить — не критично
    except AvitoAPIError as e:
        raise HTTPException(status_code=400, detail=f"Невалидные credentials: {e.detail[:200]}")

    # Шифруем client_secret
    from app.services.pd_encryption import pd_crypto
    encrypted_secret = pd_crypto.encrypt(body.client_secret).decode()

    store.avito_client_id = body.client_id
    store.avito_client_secret = encrypted_secret

    # Автозаполнение контактных данных из профиля Авито
    profile_phone = profile.get("phone") or ""
    profile_name = profile.get("name") or ""
    profile_address = ""
    # Адрес может быть в поле location
    loc = profile.get("location") or {}
    if isinstance(loc, dict):
        parts = [loc.get("city", ""), loc.get("address", "")]
        profile_address = ", ".join(p for p in parts if p)

    if profile_phone and not store.avito_phone:
        store.avito_phone = profile_phone
    if profile_name and not store.avito_manager_name:
        store.avito_manager_name = profile_name
    if profile_address and not store.avito_address:
        store.avito_address = profile_address

    await db.commit()
    await db.refresh(store)

    return {
        "status": "ok",
        "profile": {
            "phone": profile_phone,
            "name": profile_name,
            "address": profile_address,
        },
        "store": {
            "avito_phone": store.avito_phone,
            "avito_manager_name": store.avito_manager_name,
            "avito_address": store.avito_address,
        },
    }


# ── Статистика ────────────────────────────────────────────

@router.get("/stats/{store_id}")
async def get_store_stats(
    store_id: str,
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Агрегированная статистика Авито по магазину (из БД, не live)."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if current_user.role != "admin" and current_user.store_id != store_id:
        raise HTTPException(status_code=403, detail="Нет доступа к данному магазину")

    query = select(AvitoStats).where(AvitoStats.store_id == store_id)

    if date_from:
        query = query.where(AvitoStats.date >= date_from)
    if date_to:
        query = query.where(AvitoStats.date <= date_to)

    query = query.order_by(AvitoStats.date.desc()).limit(1000)
    rows = (await db.execute(query)).scalars().all()

    # Агрегация
    total_views = sum(r.views for r in rows)
    total_contacts = sum(r.contacts for r in rows)
    total_favorites = sum(r.favorites for r in rows)

    items = [
        StatsOut(
            product_id=r.product_id,
            date=r.date.isoformat() if hasattr(r.date, 'isoformat') else str(r.date),
            views=r.views,
            contacts=r.contacts,
            favorites=r.favorites,
        )
        for r in rows
    ]

    return {
        "store_id": store_id,
        "total_views": total_views,
        "total_contacts": total_contacts,
        "total_favorites": total_favorites,
        "items": items,
    }


@router.get("/stats/{store_id}/{product_id}")
async def get_product_stats(
    store_id: str,
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Статистика Авито по конкретному товару (time series)."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if current_user.role != "admin" and current_user.store_id != store_id:
        raise HTTPException(status_code=403, detail="Нет доступа к данному магазину")
    rows = (await db.execute(
        select(AvitoStats)
        .where(
            and_(
                AvitoStats.store_id == store_id,
                AvitoStats.product_id == product_id,
            )
        )
        .order_by(AvitoStats.date.desc())
        .limit(90)
    )).scalars().all()

    return {
        "product_id": product_id,
        "items": [
            StatsOut(
                product_id=r.product_id,
                date=r.date.isoformat() if hasattr(r.date, 'isoformat') else str(r.date),
                views=r.views,
                contacts=r.contacts,
                favorites=r.favorites,
            )
            for r in rows
        ],
    }


# ── Мессенджер ────────────────────────────────────────────

@router.get("/messages/{store_id}")
async def get_store_messages(
    store_id: str,
    direction: Optional[str] = Query(None, description="incoming или outgoing"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Сообщения из мессенджера Авито (из БД)."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if current_user.role != "admin" and current_user.store_id != store_id:
        raise HTTPException(status_code=403, detail="Нет доступа к данному магазину")

    query = select(AvitoMessage).where(AvitoMessage.store_id == store_id)
    if direction:
        query = query.where(AvitoMessage.direction == direction)

    total = (await db.execute(
        select(func.count()).select_from(
            query.subquery()
        )
    )).scalar() or 0

    query = query.order_by(AvitoMessage.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(query)).scalars().all()

    return {
        "total": total,
        "items": [
            MessageOut(
                id=m.id,
                chat_id=m.chat_id,
                direction=m.direction,
                author_id=m.author_id,
                content=m.content,
                created_at=m.created_at.isoformat(),
            )
            for m in rows
        ],
    }


# ── Синхронизация цен ─────────────────────────────────────

@router.post("/sync-prices/{store_id}")
async def sync_prices(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Массовое обновление цен на Авито (запускает Celery-задачу)."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not store.avito_client_id:
        raise HTTPException(status_code=400, detail="Avito API не настроен для этого магазина")

    from app.services.avito_sync import sync_all_prices
    result = await sync_all_prices(db, store_id)

    return result


@router.post("/close-listing/{product_id}")
async def close_listing_endpoint(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Снять конкретное объявление с Авито."""
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Недоступно для роли info")

    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    if not can_modify_product(current_user, product):
        raise HTTPException(status_code=403, detail="Нет доступа к данному товару")
    if not product.avito_item_id:
        raise HTTPException(status_code=400, detail="У товара нет avito_item_id")

    from app.tasks import avito_close_listing
    avito_close_listing.delay(product_id)

    return {"status": "queued", "product_id": product_id}


# ── Мониторинг фида ──────────────────────────────────────

@router.get("/feed-status/{store_id}")
async def feed_status(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Статус последнего отчёта автозагрузки Авито (live запрос к API)."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not store.avito_client_id:
        raise HTTPException(status_code=400, detail="Avito API не настроен")

    from app.services.avito_api import AvitoAPIError, build_avito_client

    client = build_avito_client(store)
    if not client:
        raise HTTPException(status_code=400, detail="Не удалось создать клиент Avito API")

    try:
        async with client:
            data = await client.get_autoload_reports(page=1, per_page=1)
    except AvitoAPIError as e:
        return FeedStatusOut(error_msg=str(e))

    reports = data.get("reports", [])
    if not reports:
        return FeedStatusOut(error_msg="Нет отчётов автозагрузки")

    report = reports[0]
    return FeedStatusOut(
        report_id=str(report.get("id", "")),
        active=report.get("items_count", 0),
        errors=report.get("errors_count", 0),
    )


@router.post("/fetch-stats/{store_id}")
async def trigger_fetch_stats(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Принудительно запустить сбор статистики Авито для магазина."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not store.avito_client_id:
        raise HTTPException(status_code=400, detail="Avito API не настроен")

    from app.services.avito_monitor import fetch_stats_for_store
    result = await fetch_stats_for_store(db, store)
    return result


@router.post("/import-items/{store_id}")
async def import_avito_items_endpoint(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Импортировать все объявления с Авито и привязать к товарам по IMEI/модели."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not store.avito_client_id:
        raise HTTPException(status_code=400, detail="Avito API не настроен")

    from app.services.avito_import import import_avito_items
    result = await import_avito_items(db, store)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/webhook/{store_id}", include_in_schema=False)
async def avito_webhook_receiver(
    store_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Публичный вебхук-приёмник для Авито мессенджера. Без авторизации."""
    store = await db.get(Store, store_id)
    if not store or not store.avito_client_id:
        raise HTTPException(status_code=404)

    async def _pull():
        from app.core.database import AsyncSessionLocal
        from app.services.avito_monitor import fetch_messages_for_store
        async with AsyncSessionLocal() as session:
            s = await session.get(Store, store_id)
            if s:
                await fetch_messages_for_store(session, s)

    background_tasks.add_task(_pull)
    return {"ok": True}


@router.post("/register-webhook/{store_id}")
async def register_avito_webhook(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Зарегистрировать вебхук Авито мессенджера для магазина."""
    from app.core.config import settings as app_settings
    from app.services.avito_api import AvitoAPIError, build_avito_client

    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not store.avito_client_id:
        raise HTTPException(status_code=400, detail="Avito API не настроен")

    client = build_avito_client(store)
    if not client:
        raise HTTPException(status_code=400, detail="Не удалось создать клиент Avito API")

    webhook_url = f"{app_settings.PUBLIC_URL}/api/avito/webhook/{store_id}"

    try:
        async with client:
            user_id = await client.get_user_id()

            # Удаляем существующие вебхуки во избежание дублей
            try:
                existing = await client.get_webhooks(user_id)
                for wh in existing.get("webhooks", []):
                    await client.delete_webhook(user_id, str(wh["id"]))
            except AvitoAPIError:
                pass

            result = await client.subscribe_webhook(user_id, webhook_url)
    except AvitoAPIError as e:
        raise HTTPException(status_code=400, detail=f"Ошибка Avito API: {e.detail[:200]}")

    return {"ok": True, "webhook_url": webhook_url, "result": result}


@router.post("/check-feed/{store_id}")
async def trigger_check_feed(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Принудительно проверить фид и обновить маппинг item_id."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not store.avito_client_id:
        raise HTTPException(status_code=400, detail="Avito API не настроен")

    from app.services.avito_monitor import check_feed_and_map_ids
    result = await check_feed_and_map_ids(db, store)
    return result
