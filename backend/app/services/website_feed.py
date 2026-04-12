"""
Генерация JSON-фида товаров для сайтов магазинов.

Секция used_products: только товары этого магазина — is_new=False,
не продано, не в ремонте, есть цена, есть собственные фото, site_published=True.

Секция new_products: сгруппированные новые товары ВСЕХ магазинов
(brand+model+storage+color+sim_type), фото из CatalogPhoto по названию
(любой магазин), есть хотя бы один экземпляр с site_published=True.
"""
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.catalog_photos import make_product_key
from app.core.config import settings
from app.models.business import CatalogPhoto, Product, ProductPhoto, Store
from app.utils.imei_sn import imei_or_sn_display


def _photo_url_from_path(file_path: str) -> str:
    base = settings.PUBLIC_URL.rstrip("/")
    path = file_path.lstrip("/").replace("\\", "/")
    return f"{base}/media/{path}"


def _photo_url(photo: ProductPhoto) -> str:
    return _photo_url_from_path(photo.file_path)


async def _build_used(db: AsyncSession, store_id: str) -> list[dict]:
    result = await db.execute(
        select(Product)
        .where(
            and_(
                Product.store_id == store_id,
                Product.is_new == False,  # noqa: E712
                Product.is_sold == False,  # noqa: E712
                Product.in_repair == False,  # noqa: E712
                Product.site_published == True,  # noqa: E712
                Product.price_retail.isnot(None),
                Product.condition.isnot(None),
                Product.condition != "",
                Product.condition.notin_(["Ремонт", "Требуется ремонт", "Залог"]),
            )
        )
        .options(selectinload(Product.photos))
        .order_by(Product.model.asc())
    )
    products = result.scalars().all()

    items = []
    for p in products:
        if not p.photos:
            continue
        photos = sorted(p.photos, key=lambda ph: (not ph.is_main, ph.created_at))
        items.append({
            "id": p.id,
            "brand": p.brand,
            "model": p.model,
            "storage": p.storage,
            "color": p.color,
            "condition": p.condition,
            "battery_pct": p.battery_pct,
            "imei": imei_or_sn_display(p.sku_1c),
            "price": int(p.price_retail) if p.price_retail else None,
            "photos": [_photo_url(ph) for ph in photos[:10]],
        })
    return items


async def _build_new(db: AsyncSession) -> list[dict]:
    # Все новые товары ВСЕХ магазинов в наличии (сайт — общий пул)
    result = await db.execute(
        select(Product)
        .where(
            and_(
                Product.is_new == True,  # noqa: E712
                Product.is_sold == False,  # noqa: E712
                Product.in_repair == False,  # noqa: E712
                Product.price_retail.isnot(None),
            )
        )
    )
    products = result.scalars().all()

    # Группировка по (brand, model, storage, color, sim_type)
    groups: dict[tuple, dict] = {}
    for p in products:
        key = (
            (p.brand or "").strip(),
            (p.model or "").strip(),
            (p.storage or "").strip(),
            (p.color or "").strip(),
            (p.sim_type or "").strip(),
        )
        g = groups.get(key)
        if g is None:
            g = {
                "brand": p.brand,
                "model": p.model,
                "storage": p.storage,
                "color": p.color,
                "sim_type": p.sim_type,
                "qty": 0,
                "min_price": None,
                "max_price": None,
                "any_published": False,
            }
            groups[key] = g
        qty = p.quantity if p.quantity and p.quantity > 0 else 1
        g["qty"] += qty
        price = int(p.price_retail) if p.price_retail else None
        if price is not None:
            g["min_price"] = price if g["min_price"] is None else min(g["min_price"], price)
            g["max_price"] = price if g["max_price"] is None else max(g["max_price"], price)
        if p.site_published:
            g["any_published"] = True

    # Собираем только нужные product_key и загружаем фото с фильтром
    needed_keys = set()
    for g in groups.values():
        if g["any_published"] and g["qty"] > 0:
            needed_keys.add(make_product_key(g["brand"] or "", g["model"] or "", g["storage"] or ""))

    photos_by_key: dict[str, list[CatalogPhoto]] = {}
    if needed_keys:
        cat_rows = (await db.execute(
            select(CatalogPhoto).where(CatalogPhoto.product_key.in_(list(needed_keys)))
        )).scalars().all()
        for cp in cat_rows:
            photos_by_key.setdefault(cp.product_key, []).append(cp)

    items: list[dict] = []
    for g in groups.values():
        if not g["any_published"]:
            continue
        if g["qty"] <= 0:
            continue
        key = make_product_key(g["brand"] or "", g["model"] or "", g["storage"] or "")
        cat_photos = photos_by_key.get(key, [])
        if not cat_photos:
            continue
        cat_photos_sorted = sorted(cat_photos, key=lambda ph: (not ph.is_main, ph.created_at))
        items.append({
            "brand": g["brand"],
            "model": g["model"],
            "storage": g["storage"],
            "color": g["color"],
            "sim_type": g["sim_type"] or None,
            "price": g["min_price"],
            "price_max": g["max_price"] if g["max_price"] != g["min_price"] else None,
            "count_in_stock": g["qty"],
            "photos": [_photo_url_from_path(ph.file_path) for ph in cat_photos_sorted[:10]],
        })

    items.sort(key=lambda x: ((x["model"] or "").lower(), (x["storage"] or ""), (x["color"] or ""), (x["sim_type"] or "")))
    return items


async def generate_website_feed(db: AsyncSession, store_id: str) -> dict:
    store = await db.get(Store, store_id)
    if not store:
        return {"store": None, "used_products": [], "new_products": []}

    used_items = await _build_used(db, store_id)
    new_items = await _build_new(db)

    return {
        "store": {
            "name": store.name,
            "address": store.avito_address,
            "phone": store.avito_phone,
        },
        "used_count": len(used_items),
        "new_count": len(new_items),
        "used_products": used_items,
        "new_products": new_items,
    }
