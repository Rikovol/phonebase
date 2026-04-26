"""Одноразовые миграции данных при старте приложения."""
import logging

from sqlalchemy import text, update

from app.core.database import AsyncSessionLocal, engine, Base
from app.models.business import User, AvitoStats, AvitoMessage, CompetitorPrice  # noqa: F401 — ensure tables registered

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


async def migrate_add_avito_message_metadata() -> None:
    """v1.4.39: добавить поля профиля клиента + контекст объявления + status в avito_messages."""
    columns = [
        ("author_name", "VARCHAR(200)"),
        ("author_avatar_url", "VARCHAR(500)"),
        ("author_profile_url", "VARCHAR(500)"),
        ("item_id", "VARCHAR(100)"),
        ("item_title", "VARCHAR(500)"),
        ("item_url", "VARCHAR(500)"),
        ("is_order", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("status", "VARCHAR(20) NOT NULL DEFAULT 'new'"),
        ("answered_at", "TIMESTAMPTZ"),
        ("answered_by", "VARCHAR(36)"),
    ]
    try:
        async with AsyncSessionLocal() as session:
            existing = (await session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'avito_messages'"
            ))).scalars().all()
            existing_set = {c.lower() for c in existing}
            added = []
            for col, ddl in columns:
                if col.lower() in existing_set:
                    continue
                await session.execute(text(f"ALTER TABLE avito_messages ADD COLUMN {col} {ddl}"))
                added.append(col)
            if "item_id" in added:
                await session.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_avito_messages_item_id "
                    "ON avito_messages(item_id)"
                ))
            if "is_order" in added:
                await session.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_avito_messages_is_order "
                    "ON avito_messages(is_order)"
                ))
            if "status" in added:
                await session.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_avito_messages_status "
                    "ON avito_messages(status)"
                ))
            await session.commit()
            if added:
                logger.info("Миграция: добавлены колонки в avito_messages: %s", added)
    except Exception:
        logger.exception("Миграция add_avito_message_metadata не выполнена")


async def migrate_add_anon_visitor_phone_unique() -> None:
    """v1.4.43: партиальный UNIQUE-индекс для дедупликации анонимных visitor по телефону.

    Гарантирует на уровне БД, что в одном магазине не появится два анонимных
    SiteVisitor (auth_provider IS NULL) с одинаковым contact_phone — закрывает
    race-condition между двумя параллельными POST /api/sites/{store_id}/messages.

    Перед созданием индекса очищает существующие дубликаты:
    - Все site_messages дубликата перепривязываются к самой старой копии (keeper).
    - Дубли (rn>1) удаляются.
    """
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'site_visitors' "
                "AND indexname = 'uq_anon_visitor_store_phone'"
            ))
            if res.first() is not None:
                return  # индекс уже есть — миграция выполнялась ранее

            # Шаг 1: перепривязать SiteMessages с дубликатов на keeper
            reassigned = await session.execute(text("""
                WITH dup AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY store_id, contact_phone
                               ORDER BY first_seen_at
                           ) AS rn,
                           FIRST_VALUE(id) OVER (
                               PARTITION BY store_id, contact_phone
                               ORDER BY first_seen_at
                           ) AS keeper
                    FROM site_visitors
                    WHERE auth_provider IS NULL AND contact_phone IS NOT NULL
                )
                UPDATE site_messages
                SET visitor_id = dup.keeper
                FROM dup
                WHERE site_messages.visitor_id = dup.id AND dup.rn > 1
            """))
            if reassigned.rowcount:
                logger.info(
                    "Миграция: перепривязано %s site_messages с дублей анонимных visitor",
                    reassigned.rowcount,
                )

            # Шаг 2: удалить дубли
            deleted = await session.execute(text("""
                WITH dup AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY store_id, contact_phone
                               ORDER BY first_seen_at
                           ) AS rn
                    FROM site_visitors
                    WHERE auth_provider IS NULL AND contact_phone IS NOT NULL
                )
                DELETE FROM site_visitors
                WHERE id IN (SELECT id FROM dup WHERE rn > 1)
            """))
            if deleted.rowcount:
                logger.info(
                    "Миграция: удалено %s дубликатов анонимных SiteVisitor",
                    deleted.rowcount,
                )

            # Шаг 3: создать unique index
            await session.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_anon_visitor_store_phone "
                "ON site_visitors (store_id, contact_phone) "
                "WHERE auth_provider IS NULL AND contact_phone IS NOT NULL"
            ))
            await session.commit()
            logger.info("Миграция: создан партиальный UNIQUE uq_anon_visitor_store_phone")
    except Exception:
        logger.exception("Миграция add_anon_visitor_phone_unique не выполнена")


async def migrate_seed_competitor_prices() -> None:
    """Загрузить начальные цены конкурентов из CSV, если таблица пуста."""
    import csv
    from datetime import datetime, timezone
    from pathlib import Path

    csv_path = Path(__file__).resolve().parent.parent / "fixtures" / "goodcom_prices.csv"
    if not csv_path.is_file():
        return

    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(text("SELECT count(*) FROM competitor_prices"))
            if (res.scalar() or 0) > 0:
                return

            now = datetime.now(timezone.utc)
            seen = set()
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    key = (row["brand"].strip(), row["model"].strip(), row["memory"])
                    if key in seen:
                        continue
                    seen.add(key)
                    session.add(CompetitorPrice(
                        source="goodcom",
                        brand=row["brand"].strip(),
                        model=row["model"].strip(),
                        memory=row["memory"] if row["memory"] != "-" else None,
                        full_name=row.get("name", ""),
                        price_excellent=int(row["price_b"]) if row["price_b"] else None,
                        price_good=int(row["price_c"]) if row["price_c"] else None,
                        price_poor=int(row["price_d"]) if row["price_d"] else None,
                        price_repair=int(row["price_g"]) if row["price_g"] else None,
                        parsed_at=now,
                    ))
            await session.commit()
            logger.info("Миграция: загружены начальные цены конкурентов из CSV")
    except Exception:
        logger.exception("Миграция seed_competitor_prices не выполнена")


async def migrate_widen_staff_log_columns() -> None:
    """Расширить action и target_id в staff_action_log (были слишком короткие)."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("ALTER TABLE staff_action_log ALTER COLUMN action TYPE VARCHAR(100)")
            )
            await session.execute(
                text("ALTER TABLE staff_action_log ALTER COLUMN target_id TYPE VARCHAR(500)")
            )
            await session.commit()
            logger.info("Миграция: расширены action/target_id в staff_action_log")
    except Exception:
        logger.warning("Миграция widen_staff_log_columns: уже выполнена или ошибка", exc_info=True)


async def migrate_add_purchased_at() -> None:
    """Добавить колонку purchased_at (дата покупки из 1С) в products."""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'products' AND column_name = 'purchased_at'"
                )
            )
            if result.first() is None:
                await session.execute(
                    text("ALTER TABLE products ADD COLUMN purchased_at TIMESTAMPTZ")
                )
                await session.commit()
                logger.info("Миграция: добавлена колонка purchased_at в products")
    except Exception:
        logger.exception("Миграция add_purchased_at не выполнена")
