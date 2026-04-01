"""
Синхронизация НОВЫХ товаров по результату парсинга HTML-выгрузки из 1С.

Аналогична import_sync.py, но для новых товаров (is_new=True).
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import ImportLog, Product, Store
from app.services.import_1c_new import OneCNewHTMLParser, ParsedNewProduct
from app.services.import_sync import ensure_stores_from_1c_names

DATA_RETENTION_DAYS = 365


async def sync_import_new(
    db: AsyncSession,
    html_bytes: bytes,
    filename: str,
    user_id: str,
) -> tuple[ImportLog, list[str]]:
    parser = OneCNewHTMLParser()
    parsed = parser.parse(html_bytes)

    now = datetime.now(timezone.utc)

    stores_in_file: dict[str, list[ParsedNewProduct]] = {}
    for p in parsed:
        stores_in_file.setdefault(p.store_name, []).append(p)

    store_names = set(stores_in_file.keys())
    stores_created = await ensure_stores_from_1c_names(db, store_names)
    store_map = await _store_map(db)

    created = updated = sold = 0

    for store_name, items in stores_in_file.items():
        store_id = store_map.get(store_name)
        if not store_id:
            continue

        imeis_in_file = {item.imei for item in items}

        existing = await _existing_new_products(db, store_id)

        for item in items:
            product = existing.get(item.imei)
            if product is None:
                product = Product(
                    store_id=store_id,
                    sku_1c=item.imei,
                    is_new=True,
                )
                db.add(product)
                created += 1
            else:
                updated += 1

            _apply(product, item, now)

            if product.is_sold:
                product.is_sold = False
                product.sold_at = None
                product.data_cleanup_at = None

        for imei, product in existing.items():
            if imei not in imeis_in_file and not product.is_sold:
                product.is_sold = True
                product.sold_at = now
                product.data_cleanup_at = now + timedelta(days=DATA_RETENTION_DAYS)
                product.updated_at = now
                sold += 1

    first_store_name = next(iter(stores_in_file), None)
    log_store_id = store_map.get(first_store_name, "") if first_store_name else ""

    log = ImportLog(
        store_id=log_store_id,
        imported_by=user_id,
        filename=filename,
        status="success",
        items_total=len(parsed),
        items_created=created,
        items_updated=updated,
        items_sold=sold,
        started_at=now,
        finished_at=datetime.now(timezone.utc),
    )
    db.add(log)
    await db.commit()
    return log, stores_created


def _apply(product: Product, item: ParsedNewProduct, now: datetime) -> None:
    product.brand = item.brand
    product.model = item.model
    product.storage = item.storage
    product.color = item.color
    product.condition = "Новый"
    product.battery_pct = None
    product.in_repair = False
    product.category = item.category
    product.quantity = item.quantity
    product.is_new = True
    product.price_retail = Decimal(str(item.price_retail)) if item.price_retail else None
    product.price_cost = Decimal(str(item.price_cost)) if item.price_cost else None
    product.synced_at = now
    product.updated_at = now


async def _store_map(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(Store))
    return {s.name: s.id for s in result.scalars().all()}


async def _existing_new_products(db: AsyncSession, store_id: str) -> dict[str, Product]:
    result = await db.execute(
        select(Product).where(
            and_(Product.store_id == store_id, Product.is_new == True)  # noqa: E712
        )
    )
    return {p.sku_1c: p for p in result.scalars().all()}
