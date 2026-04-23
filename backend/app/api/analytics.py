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
    is_gap: bool = False  # true — позиция есть у конкурента, но нет в нашем ассортименте


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
    min_units: int = Query(0, ge=0, le=100, description="Минимум единиц в остатке в группе (0 = без ограничения)"),
    include_gaps: bool = Query(True, description="Добавлять позиции конкурентов, которых нет в нашем ассортименте"),
    limit: int = Query(500, ge=1, le=1000),
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

    # Бренды, которые делают ТОЛЬКО не-смартфоны — целиком в blacklist.
    # ВАЖНО: ASUS, Honor, Nothing, Huawei, TECNO, Samsung, Xiaomi и др. сюда НЕ
    # добавлять — они производят и смартфоны, и ноутбуки/часы/наушники. Их
    # ноутбуки ловим model-pattern'ом ниже.
    NON_PHONE_BRANDS = [
        "Ноутбук", "Ноутбуки", "Наушники", "Планшет", "Планшеты",
        "Часы", "Аксессуары", "Гарнитура",
        "Acer", "Lenovo", "MSI", "HP", "Dell",
        "Packard Bell", "Toshiba", "Fujitsu",
        "Aquarius", "ARDOR", "ARDOR GAMING",
    ]

    # Маркеры не-смартфонов в model — ловят ноутбуки/часы/наушники от тех брендов,
    # которые делают и смартфоны (Asus VivoBook, Honor MagicBook, Galaxy Watch и т.п.).
    # MacBook — ИСКЛЮЧЕНИЕ из 'book' (обрабатывается отдельно ниже).
    NON_PHONE_MODEL_PATTERNS = [
        "watch", "buds", "airpods", "headphone", "наушник", "гарнитура",
        "ноутбук", "лэптоп", "laptop", "notebook",
        "планшет", "tablet", "ipad", "tab ",
        # Ноутбучные линейки разных брендов:
        "vivobook", "zenbook", "tuf gaming", "rog strix", "rog zephyrus",
        "thinkpad", "ideapad", "yoga ", "legion",
        "pavilion", "elitebook", "probook", "omen ", "envy ",
        "aspire", "predator", "nitro ", "swift ",
        "katana", "stealth", "raider ", "sword ", "prestige", "bravo ", "pulse",
        "megabook", "magicbook", "matebook",
    ]

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
        # Отсекаем не-смартфонные товары (ноутбуки/наушники/планшеты/часы).
        .where(Product.brand.notin_(NON_PHONE_BRANDS))
        # MacBook — исключение из паттерна 'book'.
        .where(or_(~Product.model.ilike("%book%"), Product.model.ilike("%macbook%")))
    )
    # Остальные model-pattern'ы применяем циклом для компактности.
    for _pat in NON_PHONE_MODEL_PATTERNS:
        base = base.where(~Product.model.ilike(f"%{_pat}%"))
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

    # Существенные модификаторы линейки — их наличие делает модели РАЗНЫМИ.
    # Пример: "galaxy s26" vs "galaxy s26 ultra" — substring совпал бы, но это
    # разные товары. Нельзя поглощать Ultra/Pro как competitor для базовой модели.
    ESSENTIAL_MODIFIERS = {
        # Apple / Samsung / Xiaomi / Google — верхние линейки
        "ultra", "pro", "plus", "+", "max", "mini",
        # Бюджетные / производные варианты
        "lite", "se", "fe", "edge", "air", "prime", "active",
        # Samsung Galaxy Note / Fold / Flip
        "note", "fold", "flip",
        # Google Pixel XL / Pro XL
        "xl",
        # Xiaomi / OnePlus суб-варианты
        "turbo", "neo", "gt",
    }

    def _substring_safe(a: str, b: str) -> bool:
        """a — строгая подстрока b, но «лишние» слова в b не являются
        существенным модификатором линейки. Токенизация устойчива к
        приклеенному "+" и дефисам ("s26-ultra" → ["s26","ultra"])."""
        if a not in b:
            return False
        extra = b.replace(a, "", 1).strip().lower()
        if not extra:
            return True  # полное совпадение
        # Вынимаем «слова» алфанум + отдельно символ "+" (он тоже в модификаторах).
        extra_words = set(re.findall(r"[a-zа-я0-9]+|\+", extra))
        return not (extra_words & ESSENTIAL_MODIFIERS)

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

        # 2. Безопасная подстрока (без поглощения Ultra/Pro/Plus и прочих
        #    существенных модификаторов) — только при совпадении памяти.
        for cm, cs, cp in comp_by_brand.get(b, []):
            if cs != s:
                continue
            if _substring_safe(cm, m_norm) or _substring_safe(m_norm, cm):
                return cp

        return None

    items = []
    matched_comp_ids: set[str] = set()
    # Ключи наших групп в том же пространстве нормализации, что и gap-fill.
    # Нужно, чтобы не породить gap-дубль к нашей уже-матченной позиции, если у
    # конкурента несколько записей с разных source под одну модель.
    matched_norm_keys: set[str] = set()
    for r in rows:
        cp = _find_competitor(r.brand, r.model, r.storage)
        comp_info = None
        if cp:
            matched_comp_ids.add(cp.id)
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
        matched_norm_keys.add(
            f"{(r.brand or '').strip().lower()}|{_normalize_model(r.model or '', r.brand)}|{_normalize_storage(r.storage)}"
        )

    # Gap-fill: позиции конкурентов, которых нет в нашем ассортименте.
    # Каждой ценовой градации (excellent/good/poor/repair) соответствует наш condition.
    # Фронт склеит строки по (brand, model, storage) в одну группу с раскрытием.
    # is_new=True означает «хочу только новые» → gap не нужны (они все б/у).
    if include_gaps and is_new is not True:
        q_lower = (q or "").strip().lower()
        brand_lower = (brand or "").strip().lower()
        # Маппинг: поле цены у конкурента → наш condition
        gap_condition_map = [
            ("price_excellent", "Отличное"),
            ("price_good", "Хорошее"),
            ("price_poor", "Удовлетворительное"),
            ("price_repair", "На запчасти"),
        ]
        # Дедуп по нормализованному ключу — одна и та же модель может быть
        # у нескольких источников (goodcom, другие) с uniq по (source,brand,model,memory).
        # Также исключаем ключи наших matched-позиций, чтобы не получить дубль
        # к уже существующей строке (разные source у одной модели).
        seen_gap_keys: set[str] = set(matched_norm_keys)
        for cp in comp_rows:
            if cp.id in matched_comp_ids:
                continue
            cp_brand = (cp.brand or "").strip()
            if brand_lower and cp_brand.lower() != brand_lower:
                continue
            if q_lower and q_lower not in (cp.model or "").lower() and q_lower not in (cp.full_name or "").lower():
                continue
            dedup_key = f"{cp_brand.lower()}|{_normalize_model(cp.model or '', cp_brand)}|{_normalize_storage(cp.memory or '')}"
            if dedup_key in seen_gap_keys:
                continue
            seen_gap_keys.add(dedup_key)
            comp_info = CompetitorPriceInfo(
                source=cp.source,
                price_excellent=cp.price_excellent,
                price_good=cp.price_good,
                price_poor=cp.price_poor,
                price_repair=cp.price_repair,
            )
            for price_field, cond_name in gap_condition_map:
                if getattr(cp, price_field) is None:
                    continue
                if condition and condition != cond_name:
                    continue
                items.append(PriceAggRow(
                    brand=cp_brand or None,
                    model=cp.model,
                    storage=cp.memory or None,
                    condition=cond_name,
                    count=0,
                    in_stock_count=0,
                    sold_in_window=0,
                    avg_retail=None,
                    min_retail=None,
                    max_retail=None,
                    avg_cost=None,
                    competitor=comp_info,
                    is_gap=True,
                ))

    # Финальный cap: SQL-limit ограничивает только наш catalog-запрос, gap-строки
    # добавляются сверху. Обрезаем общий массив, чтобы не отдавать непредсказуемый объём.
    if len(items) > limit:
        items = items[:limit]
    return PriceAggResponse(items=items, total=len(items))
