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


# Маппинг condition из phonebase → стандартные уровни состояния в фиде.
# «Как новый» — топовое б/у состояние, маппим на excellent (вместе с «Отличное»).
_COND_MAP = {
    "Как новый": "excellent",
    "Отличное": "excellent",
    "Новое": "excellent",
    "Хорошее": "good",
    "Удовлетворительное": "poor",
    "Плохое": "poor",
    "На запчасти": "repair",
    "Ремонт": "repair",
    "Требуется ремонт": "repair",
}


@router.get("/tradein-prices.json", dependencies=[Depends(_check_tradein_token)])
async def tradein_prices(
    window_days: int = Query(120, ge=30, le=730),
    db: AsyncSession = Depends(get_db),
):
    """
    Фид рыночных цен выкупа для trade-in калькулятора.
    По каждой (brand, model, storage) возвращает 4 готовые цены:
      excellent, good, poor, repair — «средняя рынок» по состоянию.

    Формула расчёта для каждого состояния:
      market_X = (наша_средняя_закупка_X + цена_конкурента_X) / 2  — если есть оба
               | наша_средняя_закупка_X                             — если есть только наша
               | цена_конкурента_X                                  — если есть только конкурент
               | null                                               — если нет ни того, ни другого

    Товар включается в фид, если хотя бы одно из 4 состояний != null
    (даже если мы никогда не закупали эту модель — даём оценку по конкуренту).

    Защищено токеном TRADEIN_FEED_TOKEN (env).
    """
    dt_from = datetime.now(timezone.utc) - timedelta(days=window_days)
    in_window = or_(Product.is_sold == False, Product.sold_at >= dt_from)  # noqa: E712

    # 1) Наши данные: средняя закупка + розница за окно, в разбивке по состоянию
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
        .where(Product.brand.in_(_WANTED_BRANDS))
        .group_by(Product.brand, Product.model, Product.storage, Product.condition)
    )
    rows = (await db.execute(stmt)).all()

    # 2) Цены конкурентов (goodcom) — индекс по (brand_lower, clean_model, norm_storage)
    comps = (await db.execute(select(CompetitorPrice))).scalars().all()
    comp_index: dict[tuple[str, str, str], CompetitorPrice] = {}
    for cp in comps:
        key = (
            (cp.brand or "").strip().lower(),
            _clean_model(cp.brand or "", cp.model or "").lower(),
            _norm_storage(cp.memory),
        )
        comp_index.setdefault(key, cp)

    # 3) Собираем все (brand, model, storage), встречавшиеся у нас в аналитике,
    #    + добавляем те, что есть у конкурента (даже если мы их никогда не закупали)
    groups: dict[tuple[str, str, str], dict] = {}

    def _get_or_make(brand: str, model_clean: str, storage: str) -> dict:
        k = (brand, model_clean, storage)
        return groups.setdefault(k, {
            "brand": brand, "model": model_clean, "storage": storage,
            # по каждому уровню состояния: взвешенная сумма + вес для нашей avg_cost
            "our_cost": {"excellent": [0.0, 0], "good": [0.0, 0],
                         "poor": [0.0, 0], "repair": [0.0, 0]},
            # общая средняя розница за окно (по всем состояниям)
            "retail_sum": 0.0, "retail_n": 0,
        })

    # 3a) Наполняем из наших данных
    for r in rows:
        if r.brand not in _WANTED_BRANDS:
            continue
        storage = _norm_storage(r.storage)
        if not storage:
            continue
        model_clean = _clean_model(r.brand, r.model)
        cond_level = _COND_MAP.get(r.condition or "")
        if not cond_level:
            # «Залог» и прочее, не вписывающееся в 4 состояния — пропускаем
            continue

        g = _get_or_make(r.brand, model_clean, storage)
        window_cnt = int(r.window_cnt or 0)
        if r.avg_cost is not None and window_cnt > 0:
            g["our_cost"][cond_level][0] += float(r.avg_cost) * window_cnt
            g["our_cost"][cond_level][1] += window_cnt
        if r.avg_retail is not None and window_cnt > 0:
            g["retail_sum"] += float(r.avg_retail) * window_cnt
            g["retail_n"] += window_cnt

    # 3b) Добавляем товары от конкурента, которых нет у нас (мы их не закупаем, но даём оценку)
    for cp in comps:
        if (cp.brand or "") not in _WANTED_BRANDS:
            continue
        storage = _norm_storage(cp.memory)
        if not storage:
            continue
        model_clean = _clean_model(cp.brand or "", cp.model or "")
        _get_or_make(cp.brand, model_clean, storage)

    # 4) Формируем items: по каждой (brand, model, storage) — 4 уровня + our_retail_avg
    items = []
    for g in groups.values():
        cp = comp_index.get((g["brand"].lower(), g["model"].lower(), g["storage"]))
        item = {
            "brand": g["brand"],
            "model": g["model"],
            "storage": g["storage"],
        }
        has_any = False
        for level in ("excellent", "good", "poor", "repair"):
            sum_, n = g["our_cost"][level]
            our = (sum_ / n) if n > 0 else None
            comp = None
            if cp:
                comp = getattr(cp, f"price_{level}", None)
                comp = int(comp) if comp else None
            if our is not None and comp is not None:
                market = int((our + comp) / 2)
            elif comp is not None:
                market = comp
            elif our is not None:
                market = int(our)
            else:
                market = None
            item[level] = market
            if market is not None:
                has_any = True
        item["our_retail_avg"] = int(g["retail_sum"] / g["retail_n"]) if g["retail_n"] else None
        if has_any:
            items.append(item)

    items.sort(key=lambda x: (x["brand"], x["model"], int(x["storage"])))

    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "source": "phonebase",
        "window_days": window_days,
        "brands": list(_WANTED_BRANDS),
        "items": items,
    }
