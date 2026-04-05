"""
Импорт HTML 1С из настроек: сначала БД (system_settings), потом .env.
Используется эндпоинтом /imports/from-configured-url и фоновой задачей после входа.
"""
from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.business import ImportLog, SystemSetting
from app.services.import_remote import fetch_import_html
from app.services.import_sync import sync_import
from app.services.import_sync_new import sync_import_new

logger = logging.getLogger(__name__)


async def _db_setting(db: AsyncSession, key: str) -> str | None:
    row = await db.get(SystemSetting, key)
    return (row.value or "").strip() or None if row else None


async def is_import_source_configured(db: AsyncSession | None = None) -> bool:
    if db and await _db_setting(db, "import_1c_url"):
        return True
    return bool((settings.IMPORT_1C_HTML_URL or "").strip())


async def is_import_new_source_configured(db: AsyncSession | None = None) -> bool:
    if db and await _db_setting(db, "import_1c_new_url"):
        return True
    return bool((settings.IMPORT_1C_NEW_HTML_URL or "").strip())


async def run_configured_import(
    db: AsyncSession, user_id: str
) -> tuple[ImportLog, list[str]] | None:
    """
    Загружает файл по настройкам и выполняет sync_import.
    Приоритет: БД → IMPORT_1C_HTML_URL из .env.
    Возвращает None если ничего не задано.
    """
    url = await _db_setting(db, "import_1c_url") or (settings.IMPORT_1C_HTML_URL or "").strip()
    if not url:
        return None
    data, filename = await fetch_import_html(url)
    return await sync_import(db=db, html_bytes=data, filename=filename, user_id=user_id)


async def run_configured_import_new(
    db: AsyncSession, user_id: str
) -> tuple[ImportLog, list[str]] | None:
    """
    Загружает файл НОВЫХ товаров по настройкам и выполняет sync_import_new.
    Приоритет: БД → IMPORT_1C_NEW_HTML_URL из .env.
    """
    url = await _db_setting(db, "import_1c_new_url") or (settings.IMPORT_1C_NEW_HTML_URL or "").strip()
    if not url:
        return None
    data, filename = await fetch_import_html(url)
    return await sync_import_new(db=db, html_bytes=data, filename=filename, user_id=user_id)
