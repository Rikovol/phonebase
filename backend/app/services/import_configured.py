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
from app.services.import_remote import fetch_import_html, load_import_html_from_path
from app.services.import_sync import sync_import
from app.services.import_sync_new import sync_import_new

logger = logging.getLogger(__name__)


async def _db_setting(db: AsyncSession, key: str) -> str | None:
    row = await db.get(SystemSetting, key)
    return (row.value or "").strip() or None if row else None


async def is_import_source_configured(db: AsyncSession | None = None) -> bool:
    if db:
        if await _db_setting(db, "import_1c_url"):
            return True
    p = (settings.IMPORT_1C_HTML_PATH or "").strip()
    u = (settings.IMPORT_1C_HTML_URL or "").strip()
    return bool(p or u)


async def is_import_new_source_configured(db: AsyncSession | None = None) -> bool:
    if db:
        if await _db_setting(db, "import_1c_new_url"):
            return True
    p = (settings.IMPORT_1C_NEW_HTML_PATH or "").strip()
    u = (settings.IMPORT_1C_NEW_HTML_URL or "").strip()
    return bool(p or u)


async def run_configured_import(
    db: AsyncSession, user_id: str
) -> tuple[ImportLog, list[str]] | None:
    """
    Загружает файл по настройкам и выполняет sync_import.
    Приоритет: БД → IMPORT_1C_HTML_PATH → IMPORT_1C_HTML_URL из .env.
    Возвращает None если ничего не задано.
    """
    db_url = await _db_setting(db, "import_1c_url")
    path_cfg = (settings.IMPORT_1C_HTML_PATH or "").strip()
    env_url = (settings.IMPORT_1C_HTML_URL or "").strip()

    url = db_url or env_url
    if path_cfg:
        data, filename = load_import_html_from_path(path_cfg)
    elif url:
        data, filename = await fetch_import_html(url)
    else:
        return None
    return await sync_import(db=db, html_bytes=data, filename=filename, user_id=user_id)


async def run_configured_import_new(
    db: AsyncSession, user_id: str
) -> tuple[ImportLog, list[str]] | None:
    """
    Загружает файл НОВЫХ товаров по настройкам и выполняет sync_import_new.
    Приоритет: БД → IMPORT_1C_NEW_HTML_PATH → IMPORT_1C_NEW_HTML_URL из .env.
    """
    db_url = await _db_setting(db, "import_1c_new_url")
    path_cfg = (settings.IMPORT_1C_NEW_HTML_PATH or "").strip()
    env_url = (settings.IMPORT_1C_NEW_HTML_URL or "").strip()

    url = db_url or env_url
    if path_cfg:
        data, filename = load_import_html_from_path(path_cfg)
    elif url:
        data, filename = await fetch_import_html(url)
    else:
        return None
    return await sync_import_new(db=db, html_bytes=data, filename=filename, user_id=user_id)
