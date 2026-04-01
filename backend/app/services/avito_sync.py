"""
Синхронизация цен и статусов товаров с Avito REST API.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Product, Store
from app.services.avito_api import AvitoAPIError, build_avito_client

logger = logging.getLogger(__name__)


async def push_price(db: AsyncSession, product_id: str) -> bool:
    """Обновить цену одного товара на Авито. Возвращает True при успехе."""
    product = await db.get(Product, product_id)
    if not product or not product.avito_item_id or not product.price_retail:
        return False

    store = await db.get(Store, product.store_id)
    client = build_avito_client(store)
    if not client:
        return False

    try:
        async with client:
            await client.update_item_price(product.avito_item_id, int(product.price_retail))
        product.synced_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info("Цена обновлена на Авито: product=%s item=%s price=%s", product_id, product.avito_item_id, product.price_retail)
        return True
    except AvitoAPIError as e:
        logger.error("Ошибка обновления цены на Авито: product=%s %s", product_id, e)
        return False


async def close_listing(db: AsyncSession, product_id: str) -> bool:
    """Снять объявление с публикации на Авито (товар продан)."""
    product = await db.get(Product, product_id)
    if not product or not product.avito_item_id:
        return False

    store = await db.get(Store, product.store_id)
    client = build_avito_client(store)
    if not client:
        return False

    try:
        async with client:
            await client.close_item(product.avito_item_id)
        product.avito_published = False
        product.synced_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info("Объявление закрыто на Авито: product=%s item=%s", product_id, product.avito_item_id)
        return True
    except AvitoAPIError as e:
        logger.error("Ошибка закрытия объявления на Авито: product=%s %s", product_id, e)
        return False


async def sync_all_prices(db: AsyncSession, store_id: str) -> dict:
    """Массовое обновление цен для всех активных товаров магазина на Авито."""
    store = await db.get(Store, store_id)
    client = build_avito_client(store)
    if not client:
        return {"error": "Avito API не настроен для этого магазина"}

    products = (await db.execute(
        select(Product).where(
            and_(
                Product.store_id == store_id,
                Product.avito_published == True,  # noqa: E712
                Product.is_sold == False,  # noqa: E712
                Product.avito_item_id.isnot(None),
                Product.price_retail.isnot(None),
            )
        )
    )).scalars().all()

    ok, failed = 0, 0
    async with client:
        for p in products:
            try:
                await client.update_item_price(p.avito_item_id, int(p.price_retail))
                p.synced_at = datetime.now(timezone.utc)
                ok += 1
            except AvitoAPIError as e:
                logger.warning("Ошибка обновления цены item=%s: %s", p.avito_item_id, e)
                failed += 1

    await db.commit()
    return {"updated": ok, "failed": failed, "total": len(products)}
