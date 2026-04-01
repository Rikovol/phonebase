"""
Мониторинг автозагрузки Авито: проверка отчётов, маппинг item_id, алерты.
"""
import logging
from datetime import date, datetime, timezone

from sqlalchemy import and_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import AvitoStats, Product, Store
from app.services.avito_api import AvitoAPIError, build_avito_client

logger = logging.getLogger(__name__)


async def fetch_stats_for_store(db: AsyncSession, store: Store) -> dict:
    """Получить статистику просмотров/контактов с Авито и сохранить в БД."""
    client = build_avito_client(store)
    if not client:
        return {"error": "not_configured"}

    products = (await db.execute(
        select(Product).where(
            and_(
                Product.store_id == store.id,
                Product.avito_item_id.isnot(None),
                Product.is_sold == False,  # noqa: E712
            )
        )
    )).scalars().all()

    if not products:
        return {"skipped": True, "reason": "no_items"}

    product_map = {p.avito_item_id: p for p in products}
    item_ids = list(product_map.keys())
    today = date.today().isoformat()

    stats_saved = 0
    async with client:
        # API лимит ~200 item_ids за запрос
        for i in range(0, len(item_ids), 200):
            batch = item_ids[i:i + 200]
            try:
                data = await client.get_items_stats(batch, today, today)
            except AvitoAPIError as e:
                logger.error("Ошибка получения статистики Авито store=%s: %s", store.id, e)
                continue

            items_stats = data.get("result", {}).get("items", [])
            for item_stat in items_stats:
                avito_id = str(item_stat.get("itemId", ""))
                product = product_map.get(avito_id)
                if not product:
                    continue

                stats = item_stat.get("stats", [])
                for day_stat in stats:
                    stat_date = day_stat.get("date", today)
                    views = day_stat.get("uniqViews", 0)
                    contacts = day_stat.get("uniqContacts", 0)
                    favorites = day_stat.get("uniqFavorites", 0)

                    # Upsert: проверяем существующую запись
                    existing = (await db.execute(
                        select(AvitoStats).where(
                            and_(
                                AvitoStats.product_id == product.id,
                                AvitoStats.date == stat_date,
                            )
                        )
                    )).scalar_one_or_none()

                    if existing:
                        existing.views = views
                        existing.contacts = contacts
                        existing.favorites = favorites
                    else:
                        db.add(AvitoStats(
                            product_id=product.id,
                            store_id=store.id,
                            date=stat_date,
                            views=views,
                            contacts=contacts,
                            favorites=favorites,
                        ))
                    stats_saved += 1

    await db.commit()
    return {"stats_saved": stats_saved, "products_count": len(products)}


async def check_feed_and_map_ids(db: AsyncSession, store: Store) -> dict:
    """
    Проверить отчёты автозагрузки Авито:
    - маппинг product.id → avito_item_id
    - подсчёт ошибок для мониторинга
    """
    client = build_avito_client(store)
    if not client:
        return {"error": "not_configured"}

    result = {"mapped": 0, "errors": 0, "active": 0, "report_id": None}

    async with client:
        try:
            reports_data = await client.get_autoload_reports(page=1, per_page=1)
        except AvitoAPIError as e:
            logger.error("Ошибка получения отчётов Авито store=%s: %s", store.id, e)
            return {"error": str(e)}

        reports = reports_data.get("reports", [])
        if not reports:
            logger.warning("ALERT: Нет отчётов автозагрузки для магазина %s (%s)", store.name, store.id)
            return {"error": "no_reports"}

        report = reports[0]
        result["report_id"] = report.get("id")

        # Считаем ошибки из отчёта
        fee_errors = report.get("errors_count", 0)
        items_count = report.get("items_count", 0)
        result["errors"] = fee_errors
        result["active"] = items_count

        if items_count == 0:
            logger.critical(
                "ALERT: Фид Авито пуст! Магазин %s (%s), отчёт %s",
                store.name, store.id, result["report_id"],
            )

        if fee_errors > 0 and items_count > 0:
            error_ratio = fee_errors / (fee_errors + items_count)
            if error_ratio > 0.5:
                logger.warning(
                    "ALERT: Высокий процент ошибок в фиде Авито: %d/%d (%.0f%%) — магазин %s",
                    fee_errors, fee_errors + items_count, error_ratio * 100, store.name,
                )

        # Маппинг Id → avito_item_id из элементов отчёта
        report_id = result["report_id"]
        if not report_id:
            return result

        try:
            page = 1
            while True:
                items_data = await client.get_report_items(str(report_id), page=page, per_page=100)
                items = items_data.get("items", [])
                if not items:
                    break

                for item in items:
                    xml_id = item.get("xml_id") or item.get("id")
                    avito_id = item.get("avito_id") or item.get("item_id")
                    avito_url = item.get("url", "")

                    if not xml_id or not avito_id:
                        continue

                    product = await db.get(Product, str(xml_id))
                    if product and product.avito_item_id != str(avito_id):
                        product.avito_item_id = str(avito_id)
                        if avito_url:
                            product.avito_url = avito_url
                        result["mapped"] += 1

                if len(items) < 100:
                    break
                page += 1

        except AvitoAPIError as e:
            logger.error("Ошибка при маппинге item_id store=%s: %s", store.id, e)

    await db.commit()
    return result


async def fetch_messages_for_store(db: AsyncSession, store: Store) -> dict:
    """Получить новые сообщения из мессенджера Авито."""
    from app.models.business import AvitoMessage

    client = build_avito_client(store)
    if not client:
        return {"error": "not_configured"}

    new_messages = 0
    async with client:
        try:
            user_id = await client.get_user_id()
        except AvitoAPIError as e:
            logger.error("Не удалось получить user_id Авито store=%s: %s", store.id, e)
            return {"error": str(e)}

        try:
            chats_data = await client.get_chats(user_id, limit=50)
        except AvitoAPIError as e:
            logger.error("Ошибка получения чатов Авито store=%s: %s", store.id, e)
            return {"error": str(e)}

        chats = chats_data.get("chats", [])
        for chat in chats:
            chat_id = str(chat.get("id", ""))
            if not chat_id:
                continue

            try:
                msgs_data = await client.get_chat_messages(user_id, chat_id, limit=50)
            except AvitoAPIError:
                continue

            messages = msgs_data.get("messages", [])
            for msg in messages:
                msg_id = str(msg.get("id", ""))
                if not msg_id:
                    continue

                # Дедупликация
                existing = (await db.execute(
                    select(AvitoMessage.id).where(AvitoMessage.avito_message_id == msg_id)
                )).first()
                if existing:
                    continue

                author_id = str(msg.get("author_id", ""))
                direction = "outgoing" if author_id == user_id else "incoming"
                content = msg.get("content", {}).get("text", "") or ""
                created = msg.get("created", "")

                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    created_dt = datetime.now(timezone.utc)

                db.add(AvitoMessage(
                    store_id=store.id,
                    chat_id=chat_id,
                    avito_message_id=msg_id,
                    direction=direction,
                    author_id=author_id,
                    content=content,
                    created_at=created_dt,
                ))
                new_messages += 1

    await db.commit()
    return {"new_messages": new_messages, "chats_checked": len(chats_data.get("chats", []))}
