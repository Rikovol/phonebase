"""
Фоновый автоимпорт: периодически скачивает HTML из Google Drive
и синхронизирует каталог. Работает без Celery/Redis — через asyncio.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.business import User
from app.services.import_configured import (
    is_import_new_source_configured,
    is_import_source_configured,
    run_configured_import,
    run_configured_import_new,
)

logger = logging.getLogger(__name__)

_import_lock: asyncio.Lock | None = None


def get_import_lock() -> asyncio.Lock:
    global _import_lock
    if _import_lock is None:
        _import_lock = asyncio.Lock()
    return _import_lock


async def _get_admin_id(db) -> str | None:
    result = await db.execute(
        select(User.id).where(User.role == "admin").limit(1)
    )
    return result.scalar_one_or_none()


async def _run_once() -> None:
    async with AsyncSessionLocal() as db:
        user_id = await _get_admin_id(db)
    if not user_id:
        logger.warning("auto-import: no admin user found, skipping")
        return

    if await is_import_source_configured():
        async with AsyncSessionLocal() as db:
            try:
                result = await run_configured_import(db, user_id)
                if result:
                    log, stores = result
                    logger.info(
                        "auto-import used: %s — total=%s created=%s updated=%s sold=%s",
                        log.status,
                        log.items_total,
                        log.items_created,
                        log.items_updated,
                        log.items_sold,
                    )
            except Exception:
                logger.exception("auto-import used failed")

    if await is_import_new_source_configured():
        async with AsyncSessionLocal() as db:
            try:
                result = await run_configured_import_new(db, user_id)
                if result:
                    log, stores = result
                    logger.info(
                        "auto-import new: %s — total=%s created=%s updated=%s sold=%s",
                        log.status,
                        log.items_total,
                        log.items_created,
                        log.items_updated,
                        log.items_sold,
                    )
            except Exception:
                logger.exception("auto-import new failed")


async def _run_competitor_parse() -> None:
    """Парсинг цен конкурентов (GoodCom). Запускается по расписанию."""
    from app.services.parse_goodcom import run_goodcom_parse

    async with AsyncSessionLocal() as db:
        try:
            count = await run_goodcom_parse(db)
            logger.info("competitor-parse goodcom: сохранено %d записей", count)
        except Exception:
            logger.exception("competitor-parse goodcom failed")


def _is_friday_3am() -> bool:
    """Проверить, что сейчас пятница и час = 03 (по UTC+3, Москва)."""
    from datetime import timedelta
    now_msk = datetime.now(timezone.utc) + timedelta(hours=3)
    return now_msk.weekday() == 4 and now_msk.hour == 3


async def auto_import_loop() -> None:
    interval = settings.IMPORT_INTERVAL_MINUTES
    if interval <= 0:
        logger.info("auto-import disabled (IMPORT_INTERVAL_MINUTES=0)")
        return

    logger.info("auto-import started, interval=%d min", interval)

    # Первый импорт через 10 секунд после старта (дать БД подняться)
    await asyncio.sleep(10)

    last_competitor_parse_day: str | None = None

    while True:
        async with get_import_lock():
            await _run_once()

        # Парсинг конкурентов: каждую пятницу в 03:00 MSK (один раз за день)
        from datetime import timedelta
        now_msk = datetime.now(timezone.utc) + timedelta(hours=3)
        today_str = now_msk.strftime("%Y-%m-%d")
        if _is_friday_3am() and last_competitor_parse_day != today_str:
            last_competitor_parse_day = today_str
            await _run_competitor_parse()

        await asyncio.sleep(interval * 60)
