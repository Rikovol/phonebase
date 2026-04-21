"""
Публичные фиды товаров: JSON для сайта, единый XML для Авито.
Фид аналитики цен для trade-in защищён токеном (env TRADEIN_FEED_TOKEN).
"""
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.business import CompetitorPrice, Product, Store

router = APIRouter()


# ── JSON-фид для сайта магазина ────────────────────────────

@router.get("/website/{store_id}.json")
async def website_feed(
    store_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Публичный JSON-фид товаров для сайта магазина. Без авторизации."""
    from app.services.website_feed import generate_website_feed

    store = await db.get(Store, store_id)
    if not store or not store.is_active:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    return await generate_website_feed(db, store_id)


# ── Единый XML-фид для Авито (б/у + новые) ─────────────────

@router.get("/avito/{store_id}.xml")
async def avito_feed(
    store_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Единый XML-фид товаров магазина для автозагрузки на Авито:
    в одном документе и б/у, и новые товары. Без авторизации.
    """
    from app.services.avito_feed import generate_feed_ads as used_ads
    from app.services.avito_feed_new import generate_feed_ads_new as new_ads
    from lxml.etree import Element, tostring

    store = await db.get(Store, store_id)
    if not store or not store.is_active:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    root = Element("Ads", formatVersion="3", target="Avito.ru")
    for ad in await used_ads(db, store_id):
        root.append(ad)
    for ad in await new_ads(db, store_id):
        root.append(ad)

    xml_body = tostring(root, encoding="utf-8", xml_declaration=False)
    xml_bytes = b'<?xml version="1.0" encoding="utf-8"?>\n' + xml_body
    return Response(content=xml_bytes, media_type="application/xml")


# ── Фид аналитики цен для trade-in (защищён токеном) ───────

def _check_tradein_token(
    token: Optional[str] = Query(None, description="Токен доступа (или заголовок Authorization: Bearer)"),
    authorization: Optional[str] = Header(None),
):
    expected = os.getenv("TRADEIN_FEED_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Feed not configured")
    provided = token
    if not provided and authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided = parts[1].strip()
    if provided != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


_WANTED_BRANDS = ("Apple", "Samsung", "Xiaomi")


def _norm_storage(s: Optional[str]) -> str:
    if not s:
        return ""
    s = s.strip()
    if m := re.search(r"(\d+)\s*[tT]", s):
        return str(int(m.group(1)) * 1024)
    if m := re.search(r"(\d+)", s):
        return m.group(1)
    return ""


def _clean_model(brand: str, model: str) -> str:
    m = re.sub(rf"^{re.escape(brand)}\s+", "", model, flags=re.I).strip()
    return re.sub(r"\s+", " ", m)


@router.get("/tradein-prices.json", dependencies=[Depends(_check_tradein_token)])
async def tradein_prices(
    window_days: int = Query(120, ge=30, le=730),
    db: AsyncSession = Depends(get_db),
):
    """
    Агрегированный фид цен для trade-in калькулятора (мобилакс.рф и др.).
    Возвращает по каждой паре (brand, model, storage) набор цен скупки
    конкурента (goodcom) для 4 состояний + наша средняя розница за окно.
    Защищено токеном TRADEIN_FEED_TOKEN.
    """
    dt_from = datetime.now(timezone.utc) - timedelta(days=window_days)
    in_window = or_(Product.is_sold == False, Product.sold_at >= dt_from)  # noqa: E712

    # Для каждой (brand, model, storage, condition) — средняя розница за окно и
    # число товаров в окне. window_cnt используется как вес при усреднении,
    # т.к. avg_retail посчитан только по товарам в окне (остальные — NULL).
    stmt = (
        select(
            Product.brand,
            Product.model,
            Product.storage,
            Product.condition,
            func.sum(case((in_window, 1), else_=0)).label("window_cnt"),
            func.avg(case((in_window, Product.price_retail), else_=None)).label("avg_retail"),
            func.avg(case((in_window, Product.price_cost), else_=None)).label("avg_cost"),
        )
        .select_from(Product)
        .join(Store, Product.store_id == Store.id)
        .where(Product.price_retail.isnot(None))
        .where(Product.is_new.is_(False))
        .where(Product.in_repair.is_(False))
        .where(Product.condition.notin_(["Новый", "Требуется ремонт", "Ремонт", "Залог"]))
        .where(Product.brand.in_(_WANTED_BRANDS))
        .group_by(Product.brand, Product.model, Product.storage, Product.condition)
    )
    rows = (await db.execute(stmt)).all()

    # Competitor prices индекс — (brand_lower, clean_model, norm_storage)
    comps = (await db.execute(select(CompetitorPrice))).scalars().all()
    comp_index: dict[tuple[str, str, str], CompetitorPrice] = {}
    for cp in comps:
        key = (
            (cp.brand or "").strip().lower(),
            _clean_model(cp.brand or "", cp.model or "").lower(),
            _norm_storage(cp.memory),
        )
        comp_index.setdefault(key, cp)

    # Группируем по (brand, model, storage) — берём конкурент-цены + нашу среднюю розницу
    groups: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        if r.brand not in _WANTED_BRANDS:
            continue
        storage = _norm_storage(r.storage)
        if not storage:
            continue
        model_clean = _clean_model(r.brand, r.model)
        key = (r.brand, model_clean, storage)
        cp = comp_index.get((r.brand.lower(), model_clean.lower(), storage))
        if not cp or not all([cp.price_excellent, cp.price_good, cp.price_poor, cp.price_repair]):
            continue
        g = groups.setdefault(key, {
            "brand": r.brand,
            "model": model_clean,
            "storage": storage,
            "excellent": int(cp.price_excellent),
            "good": int(cp.price_good),
            "poor": int(cp.price_poor),
            "repair": int(cp.price_repair),
            "our_retail_sum": 0.0,
            "our_retail_n": 0,
        })
        window_cnt = int(r.window_cnt or 0)
        if r.avg_retail is not None and window_cnt > 0:
            g["our_retail_sum"] += float(r.avg_retail) * window_cnt
            g["our_retail_n"] += window_cnt

    items = []
    for g in groups.values():
        retail_avg = int(g["our_retail_sum"] / g["our_retail_n"]) if g["our_retail_n"] else None
        items.append({
            "brand": g["brand"],
            "model": g["model"],
            "storage": g["storage"],
            "excellent": g["excellent"],
            "good": g["good"],
            "poor": g["poor"],
            "repair": g["repair"],
            "our_retail_avg": retail_avg,
        })
    items.sort(key=lambda x: (x["brand"], x["model"], int(x["storage"])))

    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "source": "phonebase",
        "window_days": window_days,
        "brands": list(_WANTED_BRANDS),
        "items": items,
    }
