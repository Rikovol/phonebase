"""
Синхронизация товаров по результату парсинга HTML-выгрузки из 1С.

Логика:
  0. Уникальные названия магазинов из файла: для отсутствующих в БД создаются записи business.stores
     (имя как в 1С после нормализации «Склад»).
  1. Файл разбирается парсером → список ParsedProduct с IMEI.
  2. Для каждого магазина из файла:
     a) Товары, присутствующие в файле  → upsert (создать или обновить).
     b) Товары, которые есть в БД, но отсутствуют в файле → is_sold=True, sold_at=NOW,
        data_cleanup_at = NOW + 1 год.
  3. Результат — ImportLog со счётчиками created / updated / sold.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.business import ImportLog, Product, Store
from app.services.catalog_refs import resolve_catalog_refs
from app.services.import_1c import OneCHTMLParser, ParsedProduct

DATA_RETENTION_DAYS = 365


async def ensure_stores_from_1c_names(db: AsyncSession, names: set[str]) -> list[str]:
    """
    Создаёт записи магазинов для названий из выгрузки 1С, которых ещё нет в БД (сравнение по name).
    Возвращает список имён новых магазинов.
    """
    if not names:
        return []
    result = await db.execute(select(Store.name))
    existing = {row[0] for row in result.all()}
    created: list[str] = []
    for name in sorted(names):
        n = (name or "").strip()
        if not n or n in existing:
            continue
        db.add(
            Store(
                id=str(uuid.uuid4()),
                name=n,
                city=None,
                address=None,
                is_active=True,
            )
        )
        existing.add(n)
        created.append(n)
    if created:
        await db.flush()
    return created



async def sync_import(
    db: AsyncSession,
    html_bytes: bytes,
    filename: str,
    user_id: str,
) -> tuple[ImportLog, list[str]]:
    parser = OneCHTMLParser()
    parsed = parser.parse(html_bytes)

    now = datetime.now(timezone.utc)

    stores_in_file: dict[str, list[ParsedProduct]] = {}
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

        existing = await _existing_products(db, store_id)

        # Кэш caталожных FK на уровне магазина: (brand, category, model) → model_id.
        # resolve_catalog_refs делает round-trip к БД и при необходимости создаёт скрытые
        # записи; кэш устраняет N лишних round-trip'ов на однотипных товарах.
        ref_cache: dict[tuple[str, str, str], str] = {}

        for item in items:
            product = existing.get(item.imei)
            if product is None:
                product = Product(
                    store_id=store_id,
                    sku_1c=item.imei,
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

            # Резолв нормализованного каталога. Если все три поля заполнены — проставим
            # model_id; иначе обнуляем FK (товар, ранее классифицированный, мог потерять
            # классификацию в новом импорте — без обнуления /menu продолжит показывать
            # его под прежней моделью, Codex review).
            if product.brand and product.category and product.model:
                key = (product.brand.strip(), product.category.strip(), product.model.strip())
                if key in ref_cache:
                    product.model_id = ref_cache[key]
                else:
                    refs = await resolve_catalog_refs(
                        db, brand=key[0], category=key[1], model=key[2]
                    )
                    if refs is not None:
                        ref_cache[key] = refs.model_id
                        product.model_id = refs.model_id
                    else:
                        product.model_id = None
            else:
                product.model_id = None

        to_sell = [p for imei, p in existing.items() if imei not in imeis_in_file and not p.is_sold]
        if to_sell:
            sell_ids = [p.id for p in to_sell]
            await db.execute(
                update(Product)
                .where(Product.id.in_(sell_ids))
                .values(
                    is_sold=True,
                    sold_at=now,
                    data_cleanup_at=now + timedelta(days=DATA_RETENTION_DAYS),
                    updated_at=now,
                )
            )
            sold += len(to_sell)
            for p in to_sell:
                if p.avito_published and p.avito_item_id:
                    try:
                        from app.tasks import avito_close_listing
                        avito_close_listing.delay(p.id)
                    except Exception:
                        pass

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


def _apply(product: Product, item: ParsedProduct, now: datetime) -> None:
    product.brand = item.brand
    product.model = item.model
    product.storage = item.storage
    product.color = item.color
    product.condition = item.condition
    product.battery_pct = item.battery_pct
    product.in_repair = item.in_repair
    product.category = item.category
    product.quantity = item.quantity
    product.price_retail = Decimal(str(item.price_retail)) if item.price_retail else None
    product.price_cost = Decimal(str(item.price_cost)) if item.price_cost else None
    product.purchased_at = item.purchased_at
    product.synced_at = now
    product.updated_at = now


async def _store_map(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(Store))
    return {s.name: s.id for s in result.scalars().all()}


async def _existing_products(db: AsyncSession, store_id: str) -> dict[str, Product]:
    result = await db.execute(
        select(Product)
        .where(and_(Product.store_id == store_id, Product.is_new == False))  # noqa: E712
        .options(load_only(
            Product.id, Product.sku_1c, Product.is_sold,
            Product.avito_published, Product.avito_item_id,
        ))
    )
    return {p.sku_1c: p for p in result.scalars().all()}
