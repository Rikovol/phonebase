"""
Удаление данных проданных товаров по истечении срока хранения (1 год).

Запускать периодически: через Celery beat, cron, или вручную через CLI.
Логика:
  - Выбрать все products где is_sold=True и data_cleanup_at <= NOW.
  - Удалить связанные фото (файлы + записи), документы (файлы + записи).
  - Удалить сам товар из БД.
"""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.business import Product, ProductPhoto, PurchaseDoc

logger = logging.getLogger(__name__)


async def cleanup_sold_products() -> dict:
    """Удалить данные проданных товаров, у которых истёк срок хранения."""
    now = datetime.now(timezone.utc)
    deleted_count = 0
    photos_deleted = 0
    docs_deleted = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Product)
            .where(
                and_(
                    Product.is_sold == True,  # noqa: E712
                    Product.data_cleanup_at != None,  # noqa: E711
                    Product.data_cleanup_at <= now,
                )
            )
            .options(
                selectinload(Product.photos),
                selectinload(Product.docs),
            )
        )
        products = result.scalars().all()

        for product in products:
            for photo in product.photos:
                _remove_file(Path(settings.MEDIA_ROOT) / photo.file_path)
                await db.delete(photo)
                photos_deleted += 1

            for doc in product.docs:
                if doc.file_path:
                    _remove_file(Path(settings.PURCHASE_DOCS_ROOT) / doc.file_path.replace("\\", "/"))
                await db.delete(doc)
                docs_deleted += 1

            await db.delete(product)
            deleted_count += 1

        await db.commit()

    result = {
        "products_deleted": deleted_count,
        "photos_deleted": photos_deleted,
        "docs_deleted": docs_deleted,
    }
    logger.info("cleanup_sold_products: %s", result)
    return result


def _remove_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError as e:
        logger.warning("Не удалось удалить файл %s: %s", path, e)
