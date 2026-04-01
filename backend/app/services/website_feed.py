"""
Генерация JSON-фида б/у товаров для сайтов магазинов.

Включаются только: is_new=False, is_sold=False, in_repair=False, есть цена.
"""
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.business import Product, ProductPhoto, Store
from app.utils.imei_sn import imei_or_sn_display


def _photo_url(photo: ProductPhoto) -> str:
    base = settings.PUBLIC_URL.rstrip("/")
    path = photo.file_path.lstrip("/").replace("\\", "/")
    return f"{base}/media/{path}"


async def generate_website_feed(db: AsyncSession, store_id: str) -> dict:
    store = await db.get(Store, store_id)
    if not store:
        return {"store": None, "products": []}

    result = await db.execute(
        select(Product)
        .where(
            and_(
                Product.store_id == store_id,
                Product.is_new == False,  # noqa: E712
                Product.is_sold == False,  # noqa: E712
                Product.in_repair == False,  # noqa: E712
                Product.price_retail.isnot(None),
            )
        )
        .options(selectinload(Product.photos))
        .order_by(Product.model.asc())
    )
    products = result.scalars().all()

    items = []
    for p in products:
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

    return {
        "store": {
            "name": store.name,
            "address": store.avito_address,
            "phone": store.avito_phone,
        },
        "count": len(items),
        "products": items,
    }
