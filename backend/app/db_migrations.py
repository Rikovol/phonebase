"""Одноразовые миграции данных при старте приложения."""
import logging

from sqlalchemy import text, update

from app.core.database import AsyncSessionLocal, engine, Base
from app.models.business import User, AvitoStats, AvitoMessage  # noqa: F401 — ensure tables registered

logger = logging.getLogger(__name__)


async def migrate_legacy_role_manager_to_staff() -> None:
    """Устаревшая роль manager в данных → staff (роль manager больше не используется)."""
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(update(User).where(User.role == "manager").values(role="staff"))
            await session.commit()
            n = getattr(res, "rowcount", None) or 0
            if n:
                logger.info("Миграция ролей: записей manager→staff: %s", n)
    except Exception:
        logger.exception("Миграция legacy manager→staff не выполнена")


async def migrate_admin_clear_store() -> None:
    """Администраторы не привязаны к магазинам — сбрасываем store_id."""
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(update(User).where(User.role == "admin").values(store_id=None))
            await session.commit()
            n = getattr(res, "rowcount", None) or 0
            if n:
                logger.info("Миграция: у администраторов сброшена привязка к магазину (%s записей)", n)
    except Exception:
        logger.exception("Миграция admin→без магазина не выполнена")


async def migrate_info_clear_store() -> None:
    """Роль info без привязки к магазину — сбрасываем store_id у бывших info с точкой."""
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(update(User).where(User.role == "info").values(store_id=None))
            await session.commit()
            n = getattr(res, "rowcount", None) or 0
            if n:
                logger.info("Миграция: у роли info сброшена привязка к магазину (%s записей)", n)
    except Exception:
        logger.exception("Миграция info→без магазина не выполнена")


async def migrate_add_is_new_column() -> None:
    """Добавить колонку is_new в таблицу products, если отсутствует."""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'products' AND column_name = 'is_new'"
                )
            )
            if result.first() is None:
                await session.execute(
                    text("ALTER TABLE products ADD COLUMN is_new BOOLEAN NOT NULL DEFAULT false")
                )
                await session.execute(
                    text("CREATE INDEX ix_products_is_new ON products (is_new)")
                )
                await session.commit()
                logger.info("Миграция: добавлена колонка is_new в products")
    except Exception:
        logger.exception("Миграция add_is_new_column не выполнена")


async def migrate_add_website_feed_columns() -> None:
    """Добавить колонки website_url и website_feed_enabled в stores."""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'stores' AND column_name = 'website_url'"
                )
            )
            if result.first() is None:
                await session.execute(
                    text("ALTER TABLE stores ADD COLUMN website_url VARCHAR(256)")
                )
                await session.execute(
                    text("ALTER TABLE stores ADD COLUMN website_feed_enabled BOOLEAN NOT NULL DEFAULT false")
                )
                await session.commit()
                logger.info("Миграция: добавлены колонки website_url, website_feed_enabled в stores")
    except Exception:
        logger.exception("Миграция add_website_feed_columns не выполнена")


async def migrate_add_avito_api_columns() -> None:
    """Добавить колонки Avito REST API в stores и products, создать таблицы avito_stats/avito_messages."""
    try:
        async with AsyncSessionLocal() as session:
            # stores: avito_client_id, avito_client_secret
            res = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'stores' AND column_name = 'avito_client_id'"
                )
            )
            if res.first() is None:
                await session.execute(text("ALTER TABLE stores ADD COLUMN avito_client_id VARCHAR(100)"))
                await session.execute(text("ALTER TABLE stores ADD COLUMN avito_client_secret TEXT"))
                logger.info("Миграция: добавлены avito_client_id/secret в stores")

            # products: avito_item_id, avito_url
            res = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'products' AND column_name = 'avito_item_id'"
                )
            )
            if res.first() is None:
                await session.execute(text("ALTER TABLE products ADD COLUMN avito_item_id VARCHAR(50)"))
                await session.execute(text("ALTER TABLE products ADD COLUMN avito_url VARCHAR(512)"))
                await session.execute(text("CREATE INDEX ix_products_avito_item_id ON products (avito_item_id)"))
                logger.info("Миграция: добавлены avito_item_id/url в products")

            await session.commit()
    except Exception:
        logger.exception("Миграция add_avito_api_columns не выполнена")


async def migrate_add_sim_completeness() -> None:
    """Добавить колонки sim_count и completeness в products."""
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'products' AND column_name = 'sim_count'"
                )
            )
            if res.first() is None:
                await session.execute(text("ALTER TABLE products ADD COLUMN sim_count INTEGER"))
                await session.execute(text("ALTER TABLE products ADD COLUMN completeness VARCHAR(100)"))
                logger.info("Миграция: добавлены sim_count/completeness в products")
            await session.commit()
    except Exception:
        logger.exception("Миграция add_sim_completeness не выполнена")


async def migrate_create_avito_tables() -> None:
    """Создать таблицы avito_stats и avito_messages если не существуют."""
    try:
        async with engine.begin() as conn:
            # create_all с checkfirst=True — безопасно для существующих таблиц
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[AvitoStats.__table__, AvitoMessage.__table__],
            )
        logger.info("Миграция: таблицы avito_stats/avito_messages проверены/созданы")
    except Exception:
        logger.exception("Миграция create_avito_tables не выполнена")
