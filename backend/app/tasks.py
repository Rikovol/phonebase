"""
Celery-задачи для фонового и периодического импорта.
"""
import asyncio
import logging

from app.celery_app import app

logger = logging.getLogger(__name__)

SYSTEM_USER_ID = "system"


def _run_async(coro):
    """Запускает корутину из синхронной Celery-задачи."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _do_import_used():
    from app.core.database import AsyncSessionLocal
    from app.services.import_configured import run_configured_import

    async with AsyncSessionLocal() as db:
        result = await run_configured_import(db, SYSTEM_USER_ID)
        if result is None:
            logger.info("Автоимпорт Б/У: источник не настроен, пропуск")
            return None
        log, stores = result
        logger.info(
            "Автоимпорт Б/У: %s, создано=%d, обновлено=%d, продано=%d",
            log.filename, log.items_created, log.items_updated, log.items_sold,
        )
        return {
            "filename": log.filename,
            "items_total": log.items_total,
            "items_created": log.items_created,
            "items_updated": log.items_updated,
            "items_sold": log.items_sold,
        }


async def _do_import_new():
    from app.core.database import AsyncSessionLocal
    from app.services.import_configured import run_configured_import_new

    async with AsyncSessionLocal() as db:
        result = await run_configured_import_new(db, SYSTEM_USER_ID)
        if result is None:
            logger.info("Автоимпорт НОВЫХ: источник не настроен, пропуск")
            return None
        log, stores = result
        logger.info(
            "Автоимпорт НОВЫХ: %s, создано=%d, обновлено=%d, продано=%d",
            log.filename, log.items_created, log.items_updated, log.items_sold,
        )
        return {
            "filename": log.filename,
            "items_total": log.items_total,
            "items_created": log.items_created,
            "items_updated": log.items_updated,
            "items_sold": log.items_sold,
        }


@app.task(bind=True, max_retries=2, default_retry_delay=60)
def periodic_import_used(self):
    """Периодический импорт Б/У товаров из настроенного файла/URL."""
    try:
        return _run_async(_do_import_used())
    except Exception as exc:
        logger.exception("Ошибка автоимпорта Б/У")
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=60)
def periodic_import_new(self):
    """Периодический импорт НОВЫХ товаров из настроенного файла/URL."""
    try:
        return _run_async(_do_import_new())
    except Exception as exc:
        logger.exception("Ошибка автоимпорта НОВЫХ")
        raise self.retry(exc=exc)


# ── Avito REST API задачи ─────────────────────────────────

async def _do_fetch_avito_stats():
    from app.core.database import AsyncSessionLocal
    from app.models.business import Store
    from app.services.avito_monitor import fetch_stats_for_store
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        stores = (await db.execute(
            select(Store).where(Store.avito_client_id.isnot(None), Store.is_active == True)  # noqa: E712
        )).scalars().all()

        results = {}
        for store in stores:
            r = await fetch_stats_for_store(db, store)
            results[store.name] = r
            logger.info("Статистика Авито %s: %s", store.name, r)

        return results


async def _do_fetch_avito_messages():
    from app.core.database import AsyncSessionLocal
    from app.models.business import Store
    from app.services.avito_monitor import fetch_messages_for_store
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        stores = (await db.execute(
            select(Store).where(Store.avito_client_id.isnot(None), Store.is_active == True)  # noqa: E712
        )).scalars().all()

        results = {}
        for store in stores:
            r = await fetch_messages_for_store(db, store)
            results[store.name] = r
            logger.info("Мессенджер Авито %s: %s", store.name, r)

        return results


async def _do_check_avito_feed():
    from app.core.database import AsyncSessionLocal
    from app.models.business import Store
    from app.services.avito_monitor import check_feed_and_map_ids
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        stores = (await db.execute(
            select(Store).where(Store.avito_client_id.isnot(None), Store.is_active == True)  # noqa: E712
        )).scalars().all()

        results = {}
        for store in stores:
            r = await check_feed_and_map_ids(db, store)
            results[store.name] = r
            logger.info("Мониторинг фида Авито %s: %s", store.name, r)

        return results


async def _do_push_price(product_id: str):
    from app.core.database import AsyncSessionLocal
    from app.services.avito_sync import push_price

    async with AsyncSessionLocal() as db:
        return await push_price(db, product_id)


async def _do_close_listing(product_id: str):
    from app.core.database import AsyncSessionLocal
    from app.services.avito_sync import close_listing

    async with AsyncSessionLocal() as db:
        return await close_listing(db, product_id)


@app.task(bind=True, max_retries=2, default_retry_delay=120)
def periodic_fetch_avito_stats(self):
    """Периодический сбор статистики Авито."""
    try:
        return _run_async(_do_fetch_avito_stats())
    except Exception as exc:
        logger.exception("Ошибка сбора статистики Авито")
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=1, default_retry_delay=60)
def periodic_fetch_avito_messages(self):
    """Периодический сбор сообщений из мессенджера Авито."""
    try:
        return _run_async(_do_fetch_avito_messages())
    except Exception as exc:
        logger.exception("Ошибка сбора сообщений Авито")
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=120)
def periodic_check_avito_feed(self):
    """Периодическая проверка отчётов автозагрузки + маппинг item_id."""
    try:
        return _run_async(_do_check_avito_feed())
    except Exception as exc:
        logger.exception("Ошибка проверки фида Авито")
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def avito_push_price(self, product_id: str):
    """Обновить цену одного товара на Авито."""
    try:
        return _run_async(_do_push_price(product_id))
    except Exception as exc:
        logger.exception("Ошибка обновления цены на Авито: %s", product_id)
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def avito_close_listing(self, product_id: str):
    """Снять объявление с Авито (товар продан)."""
    try:
        return _run_async(_do_close_listing(product_id))
    except Exception as exc:
        logger.exception("Ошибка закрытия объявления Авито: %s", product_id)
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=1, default_retry_delay=300)
def periodic_parse_goodcom(self):
    """Парсинг цен GoodCom — пн/ср/пт в 04:00 МСК."""
    async def _run():
        from app.core.database import AsyncSessionLocal
        from app.services.parse_goodcom import run_goodcom_parse
        async with AsyncSessionLocal() as db:
            count = await run_goodcom_parse(db)
            logger.info("Автопарсинг GoodCom завершён: %d записей", count)

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.exception("Ошибка автопарсинга GoodCom")
        raise self.retry(exc=exc)
