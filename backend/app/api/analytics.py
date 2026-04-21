"""
Агрегаты розничных цен по данным PhoneBase (остаток / опционально проданные)
+ цены конкурентов для сравнения.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.products import _apply_product_filters
from app.core.database import get_db
from app.models.business import CompetitorPrice, Product, Store, User

router = APIRouter()


class CompetitorPriceInfo(BaseModel):
    source: str
    price_excellent: Optional[int] = None
    price_good: Optional[int] = None
    price_poor: Optional[int] = None
    price_repair: Optional[int] = None


class PriceAggRow(BaseModel):
    brand: Optional[str] = None
    model: str
    storage: Optional[str] = None
    condition: Optional[str] = None
    count: int = Field(..., ge=0)  # всего за всё время существования в базе
    in_stock_count: int = Field(0, ge=0)  # в остатке сейчас
    sold_in_window: int = Field(0, ge=0)  # продано в окне (window_days)
    avg_retail: Optional[float] = None  # средняя наша розница за окно (None, если нет продаж/остатка в окне)
    min_retail: Optional[float] = None
    max_retail: Optional[float] = None
    avg_cost: Optional[float] = None
    competitor: Optional[CompetitorPriceInfo] = None


class PriceAggResponse(BaseModel):
    items: list[PriceAggRow]
    total: int


import re

_RE_STORAGE_SLASH = re.compile(r"(\d+)/(\d+)\s*[gGгГtTтТ]")  # 8/256Gb → 256
_RE_STORAGE_NUM = re.compile(r"(\d+)")


def _normalize_storage(s: str | None) -> str:
    """'8/256Gb' → '256', '256 ГБ' → '256', '1Tb' → '1024'."""
    if not s:
        return ""
    s = s.strip()
    # Формат RAM/Storage: берём последнее число (storage)
    m = _RE_STORAGE_SLASH.search(s)
    if m:
        val = m.group(2)
    else:
        nums = _RE_STORAGE_NUM.findall(s)
        val = nums[-1] if nums else ""
    # Tb → GB
    if val and re.search(r"[tTтТ]", s):
        try:
            val = str(int(val) * 1024)
        except ValueError:
            pass
    return val


_RE_ARTCODE = re.compile(r"\b[a-z]\d{3}[a-z]?(/[a-z]{2,3})?\b", re.I)  # S921B/DS, A135F/DS
_RE_RAM = re.compile(r"\bram\s*\d+\s*(gb|гб)?\b", re.I)  # Ram 8Gb
_RE_PAREN = re.compile(r"\([^)]*\)")  # (Новый), (Dual SIM), (Lightning)
_RE_EXTRA = re.compile(r"\b(5g|4g|lte|nfc|ds)\b", re.I)
_RE_MULTI_SPACE = re.compile(r"\s{2,}")
_RE_COLOR_TAIL = re.compile(r"[,;]\s*(black|white|grey|gray|blue|green|red|gold|silver|violet|yellow|pink|marble|cobalt|titanium|starlight|midnight|jet|хорош\S*|отличн\S*|средн\S*|плох\S*).*$", re.I)
_RE_COLORS = re.compile(r"\b(black|white|grey|gray|blue|green|red|gold|silver|violet|yellow|pink|marble|cobalt|titanium|starlight|midnight|jet|onyx|cream|lavender|phantom|graphite|amber|coral|bronze|ivory|lime|orange|purple|sapphire|teal)\b", re.I)


def _normalize_model(model: str | None, brand: str | None) -> str:
    """Привести модель к ядру для сравнения.
    'Samsung Galaxy S24 Ultra , Black, Хорошее,' → 'galaxy s24 ultra'
    'Galaxy S24 S921B/DS Ram 8Gb' → 'galaxy s24'
    'Apple iPhone 14 Pro Max' + brand='Apple' → 'iphone 14 pro max'
    """
    if not model:
        return ""
    m = model.strip().lower()
    if brand:
        b = brand.strip().lower()
        if m.startswith(b + " "):
            m = m[len(b) + 1:]
    # Убираем скобки, артикулы, RAM, цвета, суффиксы
    m = _RE_PAREN.sub(" ", m)
    m = _RE_ARTCODE.sub(" ", m)
    m = _RE_RAM.sub(" ", m)
    m = _RE_COLOR_TAIL.sub("", m)
    m = _RE_COLORS.sub(" ", m)
    m = _RE_EXTRA.sub(" ", m)
    m = _RE_MULTI_SPACE.sub(" ", m).strip(" ,.")
    return m


@router.get("/price-aggregates", response_model=PriceAggResponse)
async def price_aggregates(
    store: Optional[str] = Query(None, description="Фильтр по магазину (опционально)"),
    brand: Optional[str] = Query(None),
    condition: Optional[str] = Query(None, description="Состояние"),
    q: Optional[str] = Query(None, description="Поиск по модели"),
    include_sold: bool = Query(False, description="Включить проданные позиции"),
    sold_from: Optional[str] = Query(None, description="Продано с (YYYY-MM-DD)"),
    sold_to: Optional[str] = Query(None, description="Продано по (YYYY-MM-DD)"),
    window_days: int = Query(120, ge=1, le=730,
                             description="Размер окна аналитики в днях (по умолчанию 120 = 4 месяца)"),
    is_new: Optional[bool] = Query(None, description="Фильтр: новые (true) или б/у (false)"),
    min_units: int = Query(1, ge=1, le=100, description="Минимум единиц в группе"),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    # В таблицу попадают ВСЕ товары, когда-либо бывшие в базе (включая давно проданные и без остатка).
    # Средние цены считаем ТОЛЬКО за окно window_days (по умолчанию 4 месяца) через CASE WHEN,
    # чтобы для товаров без продаж в окне avg_retail/avg_cost пришёл null (прочерк во фронте).
    from datetime import datetime, timezone, timedelta
    if not sold_from:
        sold_from = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%d")
    dt_from = datetime.fromisoformat(sold_from).replace(tzinfo=timezone.utc)
    dt_to_cap = None
    if sold_to:
        dt_to_cap = datetime.fromisoformat(sold_to).replace(tzinfo=timezone.utc) + timedelta(days=1)

    # Условие «товар в окне»: остаток сейчас (is_sold=False) ИЛИ продан в диапазоне [dt_from, dt_to)
    in_window_cond = or_(
        Product.is_sold == False,  # noqa: E712
        (Product.sold_at >= dt_from) if dt_to_cap is None
            else ((Product.sold_at >= dt_from) & (Product.sold_at < dt_to_cap)),
    )

    base = (
        select(
            Product.brand,
            Product.model,
            Product.storage,
            Product.condition,
            func.count().label("cnt"),
            # Средние цены — только по товарам в окне 4 месяца
            func.avg(case((in_window_cond, Product.price_retail), else_=None)).label("avg_p"),
            func.min(case((in_window_cond, Product.price_retail), else_=None)).label("min_p"),
            func.max(case((in_window_cond, Product.price_retail), else_=None)).label("max_p"),
            func.avg(case((in_window_cond, Product.price_cost), else_=None)).label("avg_cost"),
            # Количество проданных в окне — пригодится фронту для метрики ликвидности
            func.sum(case((in_window_cond & (Product.is_sold == True), 1), else_=0)).label("sold_in_window"),  # noqa: E712
            func.sum(case((Product.is_sold == False, 1), else_=0)).label("in_stock_cnt"),  # noqa: E712
        )
        .select_from(Product)
        .join(Store, Product.store_id == Store.id)
        .where(Product.price_retail.isnot(None))
        .where(Product.is_new.is_(False))
        .where(Product.in_repair.is_(False))
        .where(Product.condition.notin_(["Новый", "Требуется ремонт", "Ремонт", "Залог"]))
    )
    base = _apply_product_filters(
        base,
        store=store,
        brand=brand,
        condition=condition,
        q=q,
        include_sold=True,
        is_new=is_new,
    )
    base = base.group_by(Product.brand, Product.model, Product.storage, Product.condition)
    # Сохраняем параметр min_units для обратной совместимости: 0 = показать все (новый дефолт во фронте)
    if min_units > 0:
        in_stock_expr = func.sum(case((Product.is_sold == False, 1), else_=0))  # noqa: E712
        base = base.having(in_stock_expr >= min_units)
    # Сортировка по алфавиту: бренд + модель + память (по умолчанию все товары с их историей).
    base = base.order_by(Product.brand.asc(), Product.model.asc(), Product.storage.asc()).limit(limit)

    rows = (await db.execute(base)).all()

    # Загрузить все цены конкурентов одним запросом и построить lookup
    comp_rows = (await db.execute(select(CompetitorPrice))).scalars().all()
    # Индекс по очищенной модели: brand|clean_model|storage
    comp_clean: dict[str, CompetitorPrice] = {}
    # Сгруппировать по бренду для O(k) поиска вместо O(n)
    comp_by_brand: dict[str, list[tuple[str, str, CompetitorPrice]]] = {}
    for cp in comp_rows:
        mem_norm = _normalize_storage(cp.memory)
        b = (cp.brand or "").strip().lower()
        m_clean = _normalize_model(cp.model, cp.brand)
        key = f"{b}|{m_clean}|{mem_norm}"
        if key not in comp_clean:
            comp_clean[key] = cp
        comp_by_brand.setdefault(b, []).append((m_clean, mem_norm, cp))

    def _find_competitor(brand_val: str | None, model_val: str | None, storage_val: str | None):
        if not brand_val or not model_val:
            return None
        b = brand_val.strip().lower()
        m_norm = _normalize_model(model_val, brand_val)
        s = _normalize_storage(storage_val)

        # 1. Точное совпадение по очищенным моделям
        hit = comp_clean.get(f"{b}|{m_norm}|{s}")
        if hit:
            return hit

        # 2. Подстрока в обе стороны (только по тому же бренду и памяти)
        for cm, cs, cp in comp_by_brand.get(b, []):
            if cs != s:
                continue
            if cm in m_norm or m_norm in cm:
                return cp

        return None

    items = []
    for r in rows:
        cp = _find_competitor(r.brand, r.model, r.storage)
        comp_info = None
        if cp:
            comp_info = CompetitorPriceInfo(
                source=cp.source,
                price_excellent=cp.price_excellent,
                price_good=cp.price_good,
                price_poor=cp.price_poor,
                price_repair=cp.price_repair,
            )
        items.append(PriceAggRow(
            brand=r.brand,
            model=r.model,
            storage=r.storage,
            condition=r.condition,
            count=int(r.cnt),
            in_stock_count=int(r.in_stock_cnt or 0),
            sold_in_window=int(r.sold_in_window or 0),
            avg_retail=float(r.avg_p) if r.avg_p is not None else None,
            min_retail=float(r.min_p) if r.min_p is not None else None,
            max_retail=float(r.max_p) if r.max_p is not None else None,
            avg_cost=float(r.avg_cost) if r.avg_cost is not None else None,
            competitor=comp_info,
        ))

    return PriceAggResponse(items=items, total=len(items))
