"""
Публичный роутер /api/sites/{store_id}/* — источник данных для сайтов-витрин.

Три магазина: мобилакс / айпрас / ремгсм.
Роутер регистрируется в main.py:
    app.include_router(sites.router, prefix="/api/sites", tags=["sites"])

OAuth endpoints — see sites_oauth.py (агент B)
"""
import base64
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog_photos import make_product_key, make_product_key_no_color
from app.core.config import settings
from app.core.database import get_db
from app.core.limiter import limiter
from app.models.business import (
    CatalogBrand,
    CatalogCategory,
    CatalogModel,
    CatalogPhoto,
    HiddenCatalogPhoto,
    HomeCard,
    HomeSection,
    PriceOverride,
    Product,
    SiteBonus,
    SiteMessage,
    SitePromotion,
    SiteVisitor,
    Store,
)

router = APIRouter()


# ── Вспомогательные функции ────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _media_url(file_path: str | None) -> str | None:
    """Формирует публичный URL к медиафайлу."""
    if not file_path:
        return None
    path = file_path.lstrip("/").replace("\\", "/")
    return f"/media/{path}"



def _slug_from_product_key(key: str) -> str:
    """Кодирует product_key в base64url для использования как slug в URL."""
    return base64.urlsafe_b64encode(key.encode()).rstrip(b"=").decode()


def _product_key_from_slug(slug: str) -> str | None:
    """Декодирует base64url slug → product_key. None если не base64."""
    # Добавляем padding
    padding = 4 - len(slug) % 4
    if padding != 4:
        slug += "=" * padding
    try:
        return base64.urlsafe_b64decode(slug).decode()
    except Exception:
        return None


def _is_uuid(s: str) -> bool:
    """Проверяет, является ли строка валидным UUID."""
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False


def _promotion_active_filter(now: datetime):
    """SQLAlchemy условие: акция активна сейчас (is_active + временной диапазон)."""
    return (
        SitePromotion.is_active.is_(True),
        or_(SitePromotion.starts_at.is_(None), SitePromotion.starts_at <= now),
        or_(SitePromotion.ends_at.is_(None), SitePromotion.ends_at >= now),
    )


def _price_override_active_filter(now: datetime):
    """SQLAlchemy условие: PriceOverride активна сейчас."""
    return (
        PriceOverride.is_active.is_(True),
        or_(PriceOverride.starts_at.is_(None), PriceOverride.starts_at <= now),
        or_(PriceOverride.ends_at.is_(None), PriceOverride.ends_at >= now),
    )


# ── Dependencies ──────────────────────────────────────────────────────────────

async def get_active_store(
    store_id: str,
    db: AsyncSession = Depends(get_db),
) -> Store:
    """Возвращает активный магазин или 404."""
    store = await db.get(Store, store_id)
    if not store or not store.is_active:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    return store


async def get_site_visitor(
    request: Request,
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> "SiteVisitor | None":
    """
    Читает HttpOnly cookie site_session, проверяет JWT (aud="site_visitor"),
    проверяет что store_id в payload совпадает с URL-параметром.
    Возвращает SiteVisitor или None (если cookie отсутствует или невалиден).
    """
    token = request.cookies.get("site_session")
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"],
            audience="site_visitor",
        )
    except JWTError:
        # Истёкший/невалидный токен — анон-режим
        return None

    # Защита от cross-store leak: JWT должен быть выдан именно для этого магазина
    if payload.get("store_id") != store.id:
        raise HTTPException(status_code=403, detail="Сессия для другого магазина")

    visitor = await db.get(SiteVisitor, payload["sub"])
    if not visitor or visitor.is_blocked:
        return None
    return visitor


async def require_site_visitor(
    visitor: "SiteVisitor | None" = Depends(get_site_visitor),
) -> SiteVisitor:
    """401 если посетитель не авторизован."""
    if not visitor:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    return visitor


# ── Pydantic схемы ─────────────────────────────────────────────────────────────

class CatalogPromoBadge(BaseModel):
    """Мини-бейдж акции на карточке каталога."""
    promotion_id: str
    title: str
    code: str | None


class CatalogItemOut(BaseModel):
    """Карточка товара в каталоге (б/у или новый)."""
    slug: str                       # product_id (used) или base64url(product_key) (new)
    condition: Literal["new", "used"]
    brand: str | None
    model: str
    storage: str | None
    color: str | None
    battery_pct: str | None         # только для б/у
    completeness: str | None
    sim_count: int | None
    sim_type: str | None
    price_retail: int | None        # «перечёркнутая» исходная цена
    price_effective: int            # финальная цена (после скидки)
    discount_percent: int | None
    promo: CatalogPromoBadge | None
    photo_main: str | None
    photos_count: int
    total_quantity: int


class CatalogOut(BaseModel):
    """Ответ эндпоинта каталога с пагинацией."""
    items: list[CatalogItemOut]
    total: int
    page: int
    per_page: int
    filters_applied: dict


class ProductPhotoItem(BaseModel):
    """Фото товара с указанием источника."""
    url: str
    is_main: bool
    source: Literal["product", "catalog"]


class ProductPromoOut(BaseModel):
    """Детальная информация об акции на странице товара."""
    promotion_id: str
    title: str
    body: str | None
    code: str | None
    ends_at: datetime | None


class ProductDetailOut(BaseModel):
    """Детальная карточка товара."""
    id: str            # Product UUID для add-to-cart (Stage 5). Для NEW —
                       # выбирается Product из текущего store; для USED — это сам Product.id.
    slug: str
    condition: Literal["new", "used"]
    brand: str | None
    model: str
    storage: str | None
    color: str | None
    category: str | None
    battery_pct: str | None
    completeness: str | None
    sim_count: int | None
    sim_type: str | None
    price_retail: int | None
    price_effective: int
    discount_percent: int | None
    promo: ProductPromoOut | None
    photos: list[ProductPhotoItem]
    total_quantity: int
    per_store_availability: dict[str, int] | None  # только для новых


class FacetItem(BaseModel):
    """Элемент фасетного фильтра."""
    value: str
    count: int


class FacetsOut(BaseModel):
    """Список значений фасета с количеством товаров."""
    items: list[FacetItem]


class PromotionOut(BaseModel):
    """Акция магазина или глобальная акция."""
    id: str
    scope: Literal["store", "global"]
    title: str
    body: str | None
    code: str | None
    discount_type: Literal["percent", "fixed", "info_only"]
    discount_value: float | None
    banner_image: str | None
    landing_url: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    priority: int
    applies_to_brand: str | None
    applies_to_category: str | None


class BonusOut(BaseModel):
    """Публичная информация о бонусной программе магазина."""
    id: str
    name: str
    description: str | None
    rule_type: Literal["cashback", "accrual", "signup", "referral"]
    accrual_percent: float | None
    accrual_fixed: float | None
    redemption_rate: float | None
    expires_days: int | None
    max_percent_of_order: float | None


class TradeinFields(BaseModel):
    """Поля trade-in заявки."""
    brand: str = Field(..., min_length=1, max_length=100)
    model: str = Field(..., min_length=1, max_length=255)
    storage: str | None = Field(None, max_length=30)
    color: str | None = Field(None, max_length=100)
    condition: str | None = None    # JSON массив проблем
    battery_pct: int | None = Field(None, ge=0, le=100)
    completeness: str | None = Field(None, max_length=255)
    estimated_price: Decimal | None = Field(None, ge=0)


class MessageCreateIn(BaseModel):
    """Входные данные для создания заявки с сайта."""
    message_type: Literal["tradein", "contact", "feedback", "order"]
    contact_name: str | None = Field(None, max_length=200)
    contact_phone: str | None = Field(None, max_length=30)
    contact_email: EmailStr | None = None
    preferred_channel: Literal["telegram", "max", "vk", "phone", "email", "whatsapp"] | None = None
    subject: str | None = Field(None, max_length=255)
    body: str | None = Field(None, max_length=5000)
    tradein: TradeinFields | None = None

    # Anti-spam
    website: str = Field("", max_length=200)    # honeypot-поле (должно остаться пустым)
    time_to_submit_ms: int = Field(..., ge=0)

    @model_validator(mode='after')
    def check_anti_spam(self) -> 'MessageCreateIn':
        """Honeypot + минимальное время заполнения формы."""
        if self.website:
            raise ValueError("spam")
        if self.time_to_submit_ms < 3000:
            raise ValueError("spam")
        return self

    @model_validator(mode='after')
    def check_tradein_fields(self) -> 'MessageCreateIn':
        """Для trade-in обязательны поля tradein."""
        if self.message_type == "tradein" and not self.tradein:
            raise ValueError("tradein fields required")
        return self


class MessageCreatedOut(BaseModel):
    """Ответ после создания заявки."""
    id: str
    status: str


class MyMessageOut(BaseModel):
    """Заявка в истории посетителя."""
    id: str
    message_type: str
    status: str
    body_preview: str | None
    last_reply_text: str | None
    answered_at: datetime | None
    created_at: datetime


class MyMessagesOut(BaseModel):
    """История заявок авторизованного посетителя."""
    items: list[MyMessageOut]
    total: int


class VisitorMeOut(BaseModel):
    """Публичный профиль авторизованного посетителя."""
    id: str
    store_id: str
    display_name: str | None
    contact_phone: str | None
    contact_email: str | None
    avatar_url: str | None
    preferred_channel: str | None
    total_messages_count: int
    first_seen_at: datetime
    last_seen_at: datetime


# ── OAuth endpoints — see sites_oauth.py ──────────────────────────────────────
# GET  /{store_id}/auth/vk/start
# GET  /{store_id}/auth/vk/callback
# POST /{store_id}/auth/telegram/callback
# POST /{store_id}/auth/logout
# GET  /{store_id}/auth/me


# ── Catalog endpoints ─────────────────────────────────────────────────────────

@router.get("/{store_id}/catalog", response_model=CatalogOut)
async def get_catalog(
    store_id: str,
    condition: Literal["new", "used"] = Query(..., description="Тип товара: new или used"),
    brand: str | None = Query(None),
    category: str | None = Query(None),
    model_id: str | None = Query(None, description="Фильтр по нормализованной модели каталога"),
    search: str | None = Query(None),
    in_stock: bool = Query(True, description="Только в наличии"),
    promo_only: bool = Query(False, description="Только товары с акцией"),
    price_from: int | None = Query(None, ge=0),
    price_to: int | None = Query(None, ge=0),
    sort: Literal["price_asc", "price_desc", "newest"] = Query("newest"),
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=1, le=60),
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> CatalogOut:
    """
    Публичный каталог товаров.
    - condition=used: б/у товары конкретного магазина.
    - condition=new: новые товары — агрегация по product_key по всем магазинам,
      мин. цена с учётом PriceOverride магазина из URL.
    - model_id: фильтр по нормализованной модели (catalog_models.id), приоритетней
      строковых brand/category. Используется новым меню /menu.
    """
    now = _now()

    if condition == "used":
        return await _catalog_used(
            store=store, db=db, now=now,
            brand=brand, category=category, model_id=model_id, search=search,
            in_stock=in_stock, promo_only=promo_only,
            price_from=price_from, price_to=price_to,
            sort=sort, page=page, per_page=per_page,
        )
    else:
        return await _catalog_new(
            store=store, db=db, now=now,
            brand=brand, category=category, model_id=model_id, search=search,
            in_stock=in_stock, promo_only=promo_only,
            price_from=price_from, price_to=price_to,
            sort=sort, page=page, per_page=per_page,
        )


async def _catalog_used(
    *,
    store: Store,
    db: AsyncSession,
    now: datetime,
    brand: str | None,
    category: str | None,
    model_id: str | None,
    search: str | None,
    in_stock: bool,
    promo_only: bool,
    price_from: int | None,
    price_to: int | None,
    sort: str,
    page: int,
    per_page: int,
) -> CatalogOut:
    """
    Б/у товары конкретного магазина.
    Условие видимости: is_new=false, site_published=true, is_sold=false,
    in_repair=false, quantity>0.
    Effective price = PriceOverride (активная для этого store) или price_retail.
    """
    # Подзапрос: активный PriceOverride + данные SitePromotion для этого магазина
    po_subq = (
        select(
            PriceOverride.product_id,
            PriceOverride.override_price,
            PriceOverride.promotion_id,
            SitePromotion.title.label("promo_title"),
            SitePromotion.code.label("promo_code"),
        )
        .join(SitePromotion, PriceOverride.promotion_id == SitePromotion.id)
        .where(
            PriceOverride.store_id == store.id,
            *_price_override_active_filter(now),
        )
    ).subquery()

    # Базовый запрос б/у товаров
    base_q = (
        select(Product, po_subq)
        .outerjoin(po_subq, po_subq.c.product_id == Product.id)
        .where(
            Product.store_id == store.id,
            Product.is_new.is_(False),
            Product.site_published.is_(True),
            Product.is_sold.is_(False),
            Product.in_repair.is_(False),
        )
    )

    if in_stock:
        base_q = base_q.where(Product.quantity > 0)
    if model_id:
        base_q = base_q.where(Product.model_id == model_id)
    if brand:
        base_q = base_q.where(Product.brand == brand)
    if category:
        base_q = base_q.where(Product.category == category)
    if search:
        for token in search.strip().split():
            pat = f"%{token}%"
            base_q = base_q.where(
                or_(
                    Product.brand.ilike(pat),
                    Product.model.ilike(pat),
                    Product.storage.ilike(pat),
                    Product.color.ilike(pat),
                )
            )
    if promo_only:
        base_q = base_q.where(po_subq.c.product_id.isnot(None))

    # Применяем фильтры по цене к effective_price (coalesce override/retail)
    if price_from is not None or price_to is not None:
        eff_price_expr = func.coalesce(po_subq.c.override_price, Product.price_retail)
        if price_from is not None:
            base_q = base_q.where(eff_price_expr >= price_from)
        if price_to is not None:
            base_q = base_q.where(eff_price_expr <= price_to)

    # Сортировка
    eff_price_sort = func.coalesce(po_subq.c.override_price, Product.price_retail)
    if sort == "price_asc":
        base_q = base_q.order_by(eff_price_sort.asc().nullslast())
    elif sort == "price_desc":
        base_q = base_q.order_by(eff_price_sort.desc().nullslast())
    else:  # newest
        base_q = base_q.order_by(Product.created_at.desc())

    # Подсчёт total
    count_q = select(func.count()).select_from(base_q.subquery())
    total: int = (await db.execute(count_q)).scalar() or 0

    # Пагинация
    rows = (await db.execute(base_q.offset((page - 1) * per_page).limit(per_page))).all()

    # Главные фото: product photos с is_main=True
    product_ids = [row[0].id for row in rows]
    main_photos: dict[str, str | None] = {}
    photo_counts: dict[str, int] = {}
    if product_ids:
        # Главные фото (is_main=true или первое)
        from app.models.business import ProductPhoto
        photo_rows = (await db.execute(
            select(ProductPhoto.product_id, ProductPhoto.file_path, ProductPhoto.is_main)
            .where(ProductPhoto.product_id.in_(product_ids))
        )).all()
        # Группируем фото в dict за один проход O(n)
        photo_map: dict[str, list] = {}
        for r in photo_rows:
            photo_map.setdefault(r.product_id, []).append((r.file_path, r.is_main))
        for pid in product_ids:
            photos_for = photo_map.get(pid, [])
            photo_counts[pid] = len(photos_for)
            main = next((fp for fp, im in photos_for if im), None)
            if main is None and photos_for:
                main = photos_for[0][0]
            main_photos[pid] = _media_url(main)

    items = []
    for row in rows:
        product = row[0]
        override_price = row[1] if len(row) > 1 else None
        promo_title = row[3] if len(row) > 3 else None
        promo_code = row[4] if len(row) > 4 else None
        promo_id = row[2] if len(row) > 2 else None

        price_retail = int(product.price_retail) if product.price_retail is not None else None
        price_eff = int(override_price) if override_price is not None else (price_retail or 0)

        discount_pct: int | None = None
        if override_price is not None and price_retail and price_retail > 0:
            discount_pct = int((1 - float(override_price) / float(price_retail)) * 100)
            if discount_pct <= 0:
                discount_pct = None

        promo_badge: CatalogPromoBadge | None = None
        if promo_id and promo_title:
            promo_badge = CatalogPromoBadge(
                promotion_id=str(promo_id),
                title=str(promo_title),
                code=str(promo_code) if promo_code else None,
            )

        items.append(CatalogItemOut(
            slug=str(product.id),
            condition="used",
            brand=product.brand,
            model=product.model,
            storage=product.storage,
            color=product.color,
            battery_pct=product.battery_pct,
            completeness=product.completeness,
            sim_count=product.sim_count,
            sim_type=product.sim_type,
            price_retail=price_retail,
            price_effective=price_eff,
            discount_percent=discount_pct,
            promo=promo_badge,
            photo_main=main_photos.get(product.id),
            photos_count=photo_counts.get(product.id, 0),
            total_quantity=product.quantity or 1,
        ))

    return CatalogOut(
        items=items, total=total, page=page, per_page=per_page,
        filters_applied={k: v for k, v in {
            "brand": brand, "category": category, "search": search,
            "in_stock": in_stock, "promo_only": promo_only,
            "price_from": price_from, "price_to": price_to, "sort": sort,
        }.items() if v is not None and v is not True},
    )


async def _catalog_new(
    *,
    store: Store,
    db: AsyncSession,
    now: datetime,
    brand: str | None,
    category: str | None,
    model_id: str | None,
    search: str | None,
    in_stock: bool,
    promo_only: bool,
    price_from: int | None,
    price_to: int | None,
    sort: str,
    page: int,
    per_page: int,
) -> CatalogOut:
    """
    Новые товары — агрегация по product_key = lower(brand|model|storage|color).
    Собираем по всем магазинам, effective_price для конкретного store из URL.

    Одна карточка на ключ:
    - min_price = MIN(effective_price) по всем магазинам
    - total_quantity = SUM(quantity) по всем магазинам
    - photo_main = CatalogPhoto.is_main по product_key для текущего store
      (минус HiddenCatalogPhoto)
    """
    # Подзапрос: активные PriceOverride для текущего магазина
    po_subq = (
        select(
            PriceOverride.product_id,
            PriceOverride.override_price,
            PriceOverride.promotion_id,
            SitePromotion.title.label("promo_title"),
            SitePromotion.code.label("promo_code"),
        )
        .join(SitePromotion, PriceOverride.promotion_id == SitePromotion.id)
        .where(
            PriceOverride.store_id == store.id,
            *_price_override_active_filter(now),
        )
    ).subquery()

    # Базовый запрос новых товаров по всем магазинам
    base_q = (
        select(
            Product,
            func.coalesce(po_subq.c.override_price, Product.price_retail).label("eff_price"),
            po_subq.c.promotion_id,
            po_subq.c.promo_title,
            po_subq.c.promo_code,
        )
        .outerjoin(po_subq, po_subq.c.product_id == Product.id)
        .where(
            Product.is_new.is_(True),
            Product.site_published.is_(True),
            Product.quantity > 0,
        )
    )

    if model_id:
        base_q = base_q.where(Product.model_id == model_id)
    if brand:
        base_q = base_q.where(Product.brand == brand)
    if category:
        base_q = base_q.where(Product.category == category)
    if search:
        for token in search.strip().split():
            pat = f"%{token}%"
            base_q = base_q.where(
                or_(
                    Product.brand.ilike(pat),
                    Product.model.ilike(pat),
                    Product.storage.ilike(pat),
                    Product.color.ilike(pat),
                )
            )

    all_rows = (await db.execute(base_q)).all()

    # Агрегируем по product_key в Python (гибче, чем GROUP BY в SQL для этой логики)
    groups: dict[str, dict] = {}
    for row in all_rows:
        product = row[0]
        eff_price = row[1]
        promotion_id = row[2]
        promo_title = row[3]
        promo_code = row[4]

        key = make_product_key(product.brand or "", product.model or "", product.storage or "", product.color or "")
        if key not in groups:
            groups[key] = {
                "key": key,
                "slug": _slug_from_product_key(key),
                "brand": product.brand,
                "model": product.model,
                "storage": product.storage,
                "color": product.color,
                "category": product.category,
                "sim_count": product.sim_count,
                "sim_type": product.sim_type,
                "completeness": product.completeness,
                # Базовая розничная цена (берём первую попавшуюся)
                "price_retail": int(product.price_retail) if product.price_retail else None,
                "min_eff_price": int(eff_price) if eff_price is not None else None,
                "total_quantity": 0,
                "promotion_id": None,
                "promo_title": None,
                "promo_code": None,
                "newest_created_at": product.created_at,
            }

        g = groups[key]
        g["total_quantity"] += product.quantity or 0

        # min_eff_price — минимальная из эффективных цен по всем магазинам
        if eff_price is not None:
            eff_int = int(eff_price)
            if g["min_eff_price"] is None or eff_int < g["min_eff_price"]:
                g["min_eff_price"] = eff_int
                # Акция от самой дешёвой позиции
                g["promotion_id"] = promotion_id
                g["promo_title"] = promo_title
                g["promo_code"] = promo_code
                g["price_retail"] = int(product.price_retail) if product.price_retail else None

        # newest — для сортировки по дате
        if product.created_at > g["newest_created_at"]:
            g["newest_created_at"] = product.created_at

    # Применяем фильтры (после агрегации)
    if promo_only:
        groups = {k: v for k, v in groups.items() if v["promotion_id"] is not None}
    if in_stock:
        groups = {k: v for k, v in groups.items() if v["total_quantity"] > 0}
    if price_from is not None:
        groups = {k: v for k, v in groups.items()
                  if v["min_eff_price"] is not None and v["min_eff_price"] >= price_from}
    if price_to is not None:
        groups = {k: v for k, v in groups.items()
                  if v["min_eff_price"] is not None and v["min_eff_price"] <= price_to}

    total = len(groups)

    # Сортировка
    if sort == "price_asc":
        sorted_groups = sorted(
            groups.values(),
            key=lambda g: (g["min_eff_price"] or 0),
        )
    elif sort == "price_desc":
        sorted_groups = sorted(
            groups.values(),
            key=lambda g: (g["min_eff_price"] or 0),
            reverse=True,
        )
    else:  # newest
        sorted_groups = sorted(
            groups.values(),
            key=lambda g: g["newest_created_at"],
            reverse=True,
        )

    # Пагинация
    paginated = sorted_groups[(page - 1) * per_page: page * per_page]

    # Главные фото из CatalogPhoto (минус HiddenCatalogPhoto для этого store)
    keys_on_page = [g["key"] for g in paginated]
    main_catalog_photos: dict[str, str | None] = {}
    catalog_photo_counts: dict[str, int] = {}
    if keys_on_page:
        # Скрытые фото для этого магазина
        hidden_subq = (
            select(HiddenCatalogPhoto.catalog_photo_id)
            .where(HiddenCatalogPhoto.store_id == store.id)
        ).subquery()

        # Для каждого ключа с цветом добавляем запасной ключ без цвета
        # (некоторые фото сохранены без color-компонента)
        key_to_nc: dict[str, str] = {}
        all_lookup_keys: list[str] = []
        for g in paginated:
            key = g["key"]
            nc_key = make_product_key_no_color(
                g["brand"] or "", g["model"] or "", g["storage"] or ""
            )
            key_to_nc[key] = nc_key
            all_lookup_keys.append(key)
            if nc_key != key:
                all_lookup_keys.append(nc_key)

        cp_rows = (await db.execute(
            select(CatalogPhoto.product_key, CatalogPhoto.file_path, CatalogPhoto.is_main, CatalogPhoto.id)
            .where(
                CatalogPhoto.product_key.in_(all_lookup_keys),
                CatalogPhoto.id.notin_(select(hidden_subq)),
            )
        )).all()

        # Индекс по product_key для быстрого поиска
        cp_by_key: dict[str, list[tuple[str, bool]]] = {}
        for r in cp_rows:
            cp_by_key.setdefault(r.product_key, []).append((r.file_path, r.is_main))

        for key in keys_on_page:
            # Сначала ищем фото по ключу с цветом, затем по ключу без цвета
            photos_for = cp_by_key.get(key) or cp_by_key.get(key_to_nc[key], [])
            catalog_photo_counts[key] = len(photos_for)
            main = next((fp for fp, im in photos_for if im), None)
            if main is None and photos_for:
                main = photos_for[0][0]
            main_catalog_photos[key] = _media_url(main)

    items = []
    for g in paginated:
        key = g["key"]
        price_retail = g["price_retail"]
        price_eff = g["min_eff_price"] or 0

        discount_pct: int | None = None
        if g["promotion_id"] and price_retail and price_retail > 0 and price_eff < price_retail:
            discount_pct = int((1 - price_eff / price_retail) * 100)
            if discount_pct <= 0:
                discount_pct = None

        promo_badge: CatalogPromoBadge | None = None
        if g["promotion_id"] and g["promo_title"]:
            promo_badge = CatalogPromoBadge(
                promotion_id=str(g["promotion_id"]),
                title=str(g["promo_title"]),
                code=str(g["promo_code"]) if g["promo_code"] else None,
            )

        items.append(CatalogItemOut(
            slug=g["slug"],
            condition="new",
            brand=g["brand"],
            model=g["model"],
            storage=g["storage"],
            color=g["color"],
            battery_pct=None,               # у новых нет battery_pct
            completeness=g["completeness"],
            sim_count=g["sim_count"],
            sim_type=g["sim_type"],
            price_retail=price_retail,
            price_effective=price_eff,
            discount_percent=discount_pct,
            promo=promo_badge,
            photo_main=main_catalog_photos.get(key),
            photos_count=catalog_photo_counts.get(key, 0),
            total_quantity=g["total_quantity"],
        ))

    return CatalogOut(
        items=items, total=total, page=page, per_page=per_page,
        filters_applied={k: v for k, v in {
            "brand": brand, "category": category, "search": search,
            "in_stock": in_stock, "promo_only": promo_only,
            "price_from": price_from, "price_to": price_to, "sort": sort,
        }.items() if v is not None and v is not True},
    )


@router.get("/{store_id}/product/{slug}", response_model=ProductDetailOut)
async def get_product_detail(
    store_id: str,
    slug: str,
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> ProductDetailOut:
    """
    Детальная карточка товара.
    slug = UUID → б/у товар.
    slug = base64url(product_key) → новый товар (агрегация по ключу).
    """
    now = _now()

    if _is_uuid(slug):
        return await _product_detail_used(slug, store=store, db=db, now=now)
    else:
        product_key_val = _product_key_from_slug(slug)
        if not product_key_val:
            raise HTTPException(status_code=404, detail="Товар не найден")
        return await _product_detail_new(product_key_val, slug=slug, store=store, db=db, now=now)


async def _product_detail_used(
    product_id: str,
    *,
    store: Store,
    db: AsyncSession,
    now: datetime,
) -> ProductDetailOut:
    """Детальная карточка б/у товара конкретного магазина."""
    product = await db.get(Product, product_id)
    if not product or product.store_id != store.id:
        raise HTTPException(status_code=404, detail="Товар не найден")
    if not product.site_published or product.is_sold or product.in_repair:
        raise HTTPException(status_code=404, detail="Товар не найден")

    # Effective price
    po_row = (await db.execute(
        select(PriceOverride, SitePromotion)
        .join(SitePromotion, PriceOverride.promotion_id == SitePromotion.id)
        .where(
            PriceOverride.product_id == product_id,
            PriceOverride.store_id == store.id,
            *_price_override_active_filter(now),
        )
        .limit(1)
    )).one_or_none()

    override_price = po_row[0].override_price if po_row else None
    promo_obj: ProductPromoOut | None = None
    price_retail = int(product.price_retail) if product.price_retail is not None else None
    price_eff = int(override_price) if override_price is not None else (price_retail or 0)

    if po_row:
        po, sp = po_row
        discount_pct = None
        if price_retail and price_retail > 0 and int(po.override_price) < price_retail:
            discount_pct = int((1 - float(po.override_price) / float(price_retail)) * 100)
        promo_obj = ProductPromoOut(
            promotion_id=str(sp.id),
            title=sp.title,
            body=sp.body,
            code=sp.code,
            ends_at=sp.ends_at,
        )
    else:
        discount_pct = None

    # Фото товара (ProductPhoto)
    from app.models.business import ProductPhoto
    photo_rows = (await db.execute(
        select(ProductPhoto)
        .where(ProductPhoto.product_id == product_id)
        .order_by(ProductPhoto.is_main.desc(), ProductPhoto.created_at.asc())
    )).scalars().all()

    photos = [
        ProductPhotoItem(url=_media_url(ph.file_path) or "", is_main=ph.is_main, source="product")
        for ph in photo_rows
        if ph.file_path
    ]

    return ProductDetailOut(
        id=product.id,
        slug=product_id,
        condition="used",
        brand=product.brand,
        model=product.model,
        storage=product.storage,
        color=product.color,
        category=product.category,
        battery_pct=product.battery_pct,
        completeness=product.completeness,
        sim_count=product.sim_count,
        sim_type=product.sim_type,
        price_retail=price_retail,
        price_effective=price_eff,
        discount_percent=discount_pct,
        promo=promo_obj,
        photos=photos,
        total_quantity=product.quantity or 1,
        per_store_availability=None,    # у б/у нет агрегации по магазинам
    )


async def _product_detail_new(
    product_key_val: str,
    *,
    slug: str,
    store: Store,
    db: AsyncSession,
    now: datetime,
) -> ProductDetailOut:
    """
    Детальная карточка нового товара.
    Агрегация по product_key: все магазины, per_store_availability.
    Фото: CatalogPhoto минус HiddenCatalogPhoto для текущего store.
    """
    # Разбираем product_key на компоненты. make_product_key опускает color если пустой,
    # поэтому ключ может быть из 3 (brand|model|storage) или 4 частей (с цветом).
    parts = product_key_val.split("|")
    if len(parts) < 3:
        raise HTTPException(status_code=404, detail="Неверный slug")
    brand_val, model_val, storage_val = parts[0], parts[1], parts[2]
    color_val = parts[3] if len(parts) >= 4 else ""

    # Все товары с этим ключом по всем магазинам (фильтр по компонентам в SQL)
    matching = (await db.execute(
        select(Product)
        .where(
            Product.is_new.is_(True),
            Product.site_published.is_(True),
            Product.quantity > 0,
            func.lower(Product.brand) == brand_val,
            func.lower(Product.model) == model_val,
            func.lower(func.coalesce(Product.storage, "")) == storage_val,
            func.lower(func.coalesce(Product.color, "")) == color_val,
        )
    )).scalars().all()

    if not matching:
        raise HTTPException(status_code=404, detail="Товар не найден")

    # Берём базовые атрибуты из первого совпадения
    ref = matching[0]

    # Effective price для текущего store
    store_products_ids = [p.id for p in matching if p.store_id == store.id]
    override_price = None
    promo_obj: ProductPromoOut | None = None

    if store_products_ids:
        po_row = (await db.execute(
            select(PriceOverride, SitePromotion)
            .join(SitePromotion, PriceOverride.promotion_id == SitePromotion.id)
            .where(
                PriceOverride.product_id.in_(store_products_ids),
                PriceOverride.store_id == store.id,
                *_price_override_active_filter(now),
            )
            .order_by(PriceOverride.override_price.asc())
            .limit(1)
        )).one_or_none()
        if po_row:
            override_price = po_row[0].override_price
            sp = po_row[1]
            promo_obj = ProductPromoOut(
                promotion_id=str(sp.id),
                title=sp.title,
                body=sp.body,
                code=sp.code,
                ends_at=sp.ends_at,
            )

    price_retail = int(ref.price_retail) if ref.price_retail is not None else None
    price_eff = int(override_price) if override_price is not None else (price_retail or 0)

    discount_pct: int | None = None
    if override_price is not None and price_retail and price_retail > 0:
        discount_pct = int((1 - float(override_price) / float(price_retail)) * 100)
        if discount_pct <= 0:
            discount_pct = None

    # per_store_availability: store_id → суммарный quantity
    per_store: dict[str, int] = {}
    for p in matching:
        per_store[p.store_id] = per_store.get(p.store_id, 0) + (p.quantity or 0)
    total_qty = sum(per_store.values())

    # Фото: CatalogPhoto - HiddenCatalogPhoto для текущего store
    # Используем оба ключа: с цветом и без (fallback для старых записей)
    pkey_nc = make_product_key_no_color(
        ref.brand or "", ref.model or "", ref.storage or ""
    )
    photo_keys = list({product_key_val, pkey_nc})

    hidden_subq = (
        select(HiddenCatalogPhoto.catalog_photo_id)
        .where(HiddenCatalogPhoto.store_id == store.id)
    ).subquery()

    cp_rows = (await db.execute(
        select(CatalogPhoto)
        .where(
            CatalogPhoto.product_key.in_(photo_keys),
            CatalogPhoto.id.notin_(select(hidden_subq)),
        )
        .order_by(CatalogPhoto.is_main.desc(), CatalogPhoto.created_at.asc())
    )).scalars().all()

    photos = [
        ProductPhotoItem(
            url=_media_url(cp.file_path) or "",
            is_main=cp.is_main,
            source="catalog",
        )
        for cp in cp_rows
        if cp.file_path
    ]

    # Cart UUID: предпочитаем Product из текущего store (для shop.basestock.ru =
    # mobileax). Fallback на любой matching, если в этом store нет (cross-store
    # витрина показывает товар даже когда конкретно тут его нет — клиент увидит,
    # но при add-to-cart backend вернёт 404 «не найден в этом магазине»).
    chosen_id = store_products_ids[0] if store_products_ids else matching[0].id

    return ProductDetailOut(
        id=chosen_id,
        slug=slug,
        condition="new",
        brand=ref.brand,
        model=ref.model,
        storage=ref.storage,
        color=ref.color,
        category=ref.category,
        battery_pct=None,
        completeness=ref.completeness,
        sim_count=ref.sim_count,
        sim_type=ref.sim_type,
        price_retail=price_retail,
        price_effective=price_eff,
        discount_percent=discount_pct,
        promo=promo_obj,
        photos=photos,
        total_quantity=total_qty,
        per_store_availability=per_store,
    )


# ── Фасеты ────────────────────────────────────────────────────────────────────

@router.get("/{store_id}/categories", response_model=FacetsOut)
async def get_categories(
    store_id: str,
    condition: Literal["new", "used"] | None = Query(None),
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> FacetsOut:
    """Список категорий с количеством видимых товаров.
    condition необязателен: если не передан — считается по всем товарам (new + used).
    """
    rows = await _facet_rows(store=store, db=db, field=Product.category, condition=condition)
    items = [FacetItem(value=v, count=c) for v, c in rows if v]
    return FacetsOut(items=items)


@router.get("/{store_id}/brands", response_model=FacetsOut)
async def get_brands(
    store_id: str,
    condition: Literal["new", "used"] | None = Query(None),
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> FacetsOut:
    """Список брендов с количеством видимых товаров.
    condition необязателен: если не передан — считается по всем товарам (new + used).
    """
    rows = await _facet_rows(store=store, db=db, field=Product.brand, condition=condition)
    items = [FacetItem(value=v, count=c) for v, c in rows if v]
    return FacetsOut(items=items)


async def _facet_rows(
    *,
    store: Store,
    db: AsyncSession,
    field,
    condition: str | None,
) -> list[tuple[str, int]]:
    """
    Вспомогательная функция: возвращает (value, count) для фасета.
    Учитывает фильтры видимости (site_published, is_sold, in_repair).
    condition=None → все товары (new + used).
    """
    if condition == "used":
        q = (
            select(field, func.count(Product.id).label("cnt"))
            .where(
                Product.store_id == store.id,
                Product.is_new.is_(False),
                Product.site_published.is_(True),
                Product.is_sold.is_(False),
                Product.in_repair.is_(False),
                Product.quantity > 0,
            )
            .group_by(field)
            .order_by(func.count(Product.id).desc())
        )
    elif condition == "new":
        q = (
            select(field, func.count(Product.id).label("cnt"))
            .where(
                Product.is_new.is_(True),
                Product.site_published.is_(True),
                Product.quantity > 0,
            )
            .group_by(field)
            .order_by(func.count(Product.id).desc())
        )
    else:
        # condition=None: все товары без фильтра по is_new
        q = (
            select(field, func.count(Product.id).label("cnt"))
            .where(
                Product.store_id == store.id,
                Product.site_published.is_(True),
                Product.quantity > 0,
            )
            .group_by(field)
            .order_by(func.count(Product.id).desc())
        )
    rows = (await db.execute(q)).all()
    return [(r[0], r[1]) for r in rows]


# ── Меню каталога: дерево Категория → Бренд → Модель ─────────────────────────

# Защита от чудовищного дерева: при некорректной заливке (тысячи дублирующихся моделей)
# /menu может вернуть многомегабайтный JSON. Логируем и режем.
_MENU_HARD_LIMIT = 5000

# Redis TTL для /menu cache. Каталог меняется редко (1С импорт ~раз в N часов);
# 60s даёт sub-10ms hit для 99% запросов и не более 1 минуты несвежести.
# Явная инвалидация на admin-mutate не делается — TTL покрывает.
_MENU_CACHE_TTL = 60


class MenuModelItem(BaseModel):
    id: str
    slug: str
    display_name: str
    hero_image_url: str | None
    products_count: int


class MenuBrandItem(BaseModel):
    id: str
    slug: str
    display_name: str
    logo_url: str | None
    products_count: int
    models: list[MenuModelItem]


class MenuCategoryItem(BaseModel):
    id: str
    slug: str
    display_name: str
    icon_url: str | None
    products_count: int
    brands: list[MenuBrandItem]


class MenuOut(BaseModel):
    condition: Literal["new", "used"]
    categories: list[MenuCategoryItem]


@router.get("/{store_id}/menu", response_model=MenuOut)
async def get_menu(
    store_id: str,
    condition: Literal["new", "used"] = Query(..., description="new или used"),
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> MenuOut:
    """Дерево каталога для меню витрины: Категория → Бренд → Модель со счётчиками.

    Учитывает is_visible на всех уровнях (категории/бренда/модели) и видимость
    товаров (site_published, quantity>0, is_sold=false, in_repair=false).

    condition=used → товары конкретного магазина (Product.store_id=store.id).
    condition=new  → агрегация по всем магазинам (как /catalog?condition=new).

    Категории/бренды/модели без хотя бы одного видимого товара в меню не попадают.

    Cache: Redis JSON, TTL = _MENU_CACHE_TTL (60s). При Redis-down — direct compute
    (см. redis_cache.get_or_set_json — best-effort).
    """
    from app.core.redis_cache import get_or_set_json

    cache_key = f"menu:{store.id}:{condition}"

    async def _compute() -> dict:
        return (await _compute_menu(db, store, condition)).model_dump(mode="json")

    cached = await get_or_set_json(cache_key, _MENU_CACHE_TTL, _compute)
    return MenuOut.model_validate(cached)


async def _compute_menu(
    db: AsyncSession,
    store: Store,
    condition: Literal["new", "used"],
) -> MenuOut:
    """Чистая SQL+сборка дерева — вынесено из get_menu для cache-aside обёртки.

    Никакого DI и cache внутри: только запрос + group-by сборка.
    """
    product_filter = [
        Product.site_published.is_(True),
        Product.is_sold.is_(False),
        Product.in_repair.is_(False),
        Product.quantity > 0,
        Product.model_id.isnot(None),
    ]
    if condition == "used":
        product_filter.append(Product.is_new.is_(False))
        product_filter.append(Product.store_id == store.id)
    else:
        product_filter.append(Product.is_new.is_(True))

    # Один запрос: GROUP BY (category, brand, model).
    # Counter:
    #   used → count(Product.id) — каждый IMEI отдельная карточка.
    #   new  → count(DISTINCT product_key) — на витрине агрегация по brand|model|
    #          storage|color (один товар в N магазинах = одна карточка). count(id)
    #          бы overcounted (Codex r5).
    if condition == "new":
        # Должно совпадать с make_product_key (catalog_photos.py): strip+lower
        # каждой части. Без trim'а «Apple» и «Apple » посчитаются как два, хотя
        # витрина схлопнет их в одну карточку (Codex r7).
        cnt_expr = func.count(func.distinct(func.concat_ws(
            "|",
            func.lower(func.trim(Product.brand)),
            func.lower(func.trim(Product.model)),
            func.lower(func.trim(func.coalesce(Product.storage, ""))),
            func.lower(func.trim(func.coalesce(Product.color, ""))),
        ))).label("cnt")
    else:
        cnt_expr = func.count(Product.id).label("cnt")

    rows = (await db.execute(
        select(
            CatalogCategory.id, CatalogCategory.slug, CatalogCategory.display_name,
            CatalogCategory.icon_url, CatalogCategory.sort_order,
            CatalogBrand.id, CatalogBrand.slug, CatalogBrand.display_name,
            CatalogBrand.logo_url, CatalogBrand.sort_order,
            CatalogModel.id, CatalogModel.slug, CatalogModel.display_name,
            CatalogModel.hero_image_url, CatalogModel.sort_order,
            cnt_expr,
        )
        .select_from(CatalogModel)
        .join(CatalogBrand, CatalogBrand.id == CatalogModel.brand_id)
        .join(CatalogCategory, CatalogCategory.id == CatalogModel.category_id)
        .join(Product, Product.model_id == CatalogModel.id)
        .where(
            CatalogCategory.is_visible.is_(True),
            CatalogBrand.is_visible.is_(True),
            CatalogModel.is_visible.is_(True),
            *product_filter,
        )
        .group_by(
            CatalogCategory.id, CatalogCategory.slug, CatalogCategory.display_name,
            CatalogCategory.icon_url, CatalogCategory.sort_order,
            CatalogBrand.id, CatalogBrand.slug, CatalogBrand.display_name,
            CatalogBrand.logo_url, CatalogBrand.sort_order,
            CatalogModel.id, CatalogModel.slug, CatalogModel.display_name,
            CatalogModel.hero_image_url, CatalogModel.sort_order,
        )
        .order_by(
            CatalogCategory.sort_order, CatalogCategory.display_name,
            CatalogBrand.sort_order, CatalogBrand.display_name,
            CatalogModel.sort_order, CatalogModel.display_name,
        )
        .limit(_MENU_HARD_LIMIT + 1)
    )).all()

    if len(rows) > _MENU_HARD_LIMIT:
        import logging
        logging.getLogger(__name__).warning(
            "menu: store=%s condition=%s превысил HARD_LIMIT=%s — ответ обрезан",
            store.id, condition, _MENU_HARD_LIMIT,
        )
        rows = rows[:_MENU_HARD_LIMIT]

    # Сборка дерева. dict-индекс по id для быстрого аппенда.
    cats: dict[str, MenuCategoryItem] = {}
    cat_brands: dict[tuple[str, str], MenuBrandItem] = {}

    for r in rows:
        (
            c_id, c_slug, c_name, c_icon, _c_sort,
            b_id, b_slug, b_name, b_logo, _b_sort,
            m_id, m_slug, m_name, m_hero, _m_sort,
            cnt,
        ) = r

        cat = cats.get(c_id)
        if cat is None:
            cat = MenuCategoryItem(
                id=c_id, slug=c_slug, display_name=c_name, icon_url=c_icon,
                products_count=0, brands=[],
            )
            cats[c_id] = cat

        brand_key = (c_id, b_id)
        brand = cat_brands.get(brand_key)
        if brand is None:
            brand = MenuBrandItem(
                id=b_id, slug=b_slug, display_name=b_name, logo_url=b_logo,
                products_count=0, models=[],
            )
            cat.brands.append(brand)
            cat_brands[brand_key] = brand

        brand.models.append(MenuModelItem(
            id=m_id, slug=m_slug, display_name=m_name,
            hero_image_url=m_hero, products_count=cnt,
        ))
        brand.products_count += cnt
        cat.products_count += cnt

    return MenuOut(condition=condition, categories=list(cats.values()))


# ── Акции ─────────────────────────────────────────────────────────────────────

@router.get("/{store_id}/promotions", response_model=list[PromotionOut])
async def get_promotions(
    store_id: str,
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> list[PromotionOut]:
    """
    Активные акции: store-specific (store_id=URL.store_id) + глобальные (store_id IS NULL).
    Сортировка: priority DESC, created_at DESC.
    """
    now = _now()
    rows = (await db.execute(
        select(SitePromotion)
        .where(
            *_promotion_active_filter(now),
            or_(
                SitePromotion.store_id == store.id,
                SitePromotion.store_id.is_(None),
            ),
        )
        .order_by(SitePromotion.priority.desc(), SitePromotion.created_at.desc())
    )).scalars().all()

    return [_promotion_out(sp) for sp in rows]


@router.get("/{store_id}/promotions/{promotion_id}", response_model=PromotionOut)
async def get_promotion_detail(
    store_id: str,
    promotion_id: str,
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> PromotionOut:
    """Детали акции. 404 если акция не принадлежит магазину и не глобальная."""
    sp = await db.get(SitePromotion, promotion_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Акция не найдена")
    # Scope mismatch: акция не для этого магазина и не глобальная
    if sp.store_id is not None and sp.store_id != store.id:
        raise HTTPException(status_code=404, detail="Акция не найдена")
    return _promotion_out(sp)


def _promotion_out(sp: SitePromotion) -> PromotionOut:
    """Конвертирует ORM-объект SitePromotion в Pydantic схему."""
    return PromotionOut(
        id=str(sp.id),
        scope="global" if sp.store_id is None else "store",
        title=sp.title,
        body=sp.body,
        code=sp.code,
        discount_type=sp.discount_type,
        discount_value=float(sp.discount_value) if sp.discount_value is not None else None,
        banner_image=sp.banner_image,
        landing_url=sp.landing_url,
        starts_at=sp.starts_at,
        ends_at=sp.ends_at,
        priority=sp.priority,
        applies_to_brand=sp.applies_to_brand,
        applies_to_category=sp.applies_to_category,
    )


# ── Бонусы ────────────────────────────────────────────────────────────────────

@router.get("/{store_id}/bonuses", response_model=list[BonusOut])
async def get_bonuses(
    store_id: str,
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> list[BonusOut]:
    """Активные бонусные программы магазина (без чувствительных полей)."""
    rows = (await db.execute(
        select(SiteBonus)
        .where(
            SiteBonus.store_id == store.id,
            SiteBonus.is_active.is_(True),
        )
        .order_by(SiteBonus.created_at.asc())
    )).scalars().all()

    return [
        BonusOut(
            id=str(b.id),
            name=b.name,
            description=b.description,
            rule_type=b.rule_type,
            accrual_percent=float(b.accrual_percent) if b.accrual_percent is not None else None,
            accrual_fixed=float(b.accrual_fixed) if b.accrual_fixed is not None else None,
            redemption_rate=float(b.redemption_rate) if b.redemption_rate is not None else None,
            expires_days=b.expires_days,
            max_percent_of_order=float(b.max_percent_of_order) if b.max_percent_of_order is not None else None,
        )
        for b in rows
    ]


# ── Сообщения / заявки ────────────────────────────────────────────────────────

@router.post("/{store_id}/messages", response_model=MessageCreatedOut, status_code=201)
@limiter.limit("10/hour")
async def create_message(
    store_id: str,
    body: MessageCreateIn,
    request: Request,
    store: Store = Depends(get_active_store),
    visitor: "SiteVisitor | None" = Depends(get_site_visitor),
    db: AsyncSession = Depends(get_db),
) -> MessageCreatedOut:
    """
    Создание заявки с сайта.
    - Авторизованный visitor: JWT из cookie, проверка store_id.
    - Анонимный: обязателен телефон или email, создаётся новый SiteVisitor.
    Rate-limit 10/hour per IP добавляется как middleware (агент C).
    Blocked visitor проверяется здесь явно → 403.
    """
    # Заблокированный visitor не может отправлять заявки
    if visitor and visitor.is_blocked:
        raise HTTPException(status_code=403, detail="Доступ заблокирован")

    # Cross-store leak: visitor из другого магазина
    if visitor and visitor.store_id != store.id:
        raise HTTPException(status_code=403, detail="Cross-store forbidden")

    # Анон: обязателен контакт
    if not visitor and not (body.contact_phone or body.contact_email):
        raise HTTPException(status_code=400, detail="Требуется телефон или email")

    # Анон → пробуем найти существующего visitor по телефону в этом магазине,
    # чтобы не создавать новую «анонимную» карточку клиента при каждом обращении.
    # Идентификация по phone — наиболее надёжный анти-фрагментирующий ключ
    # (email тоже норм, но phone обязателен для контакта в РФ).
    if not visitor:
        if body.contact_phone:
            existing = (await db.execute(
                select(SiteVisitor).where(
                    SiteVisitor.store_id == store.id,
                    SiteVisitor.auth_provider.is_(None),
                    SiteVisitor.contact_phone == body.contact_phone,
                ).limit(1)
            )).scalar_one_or_none()
            if existing and not existing.is_blocked:
                visitor = existing
                # Освежаем данные если новые более полные (имя/email/канал)
                if body.contact_name and not visitor.display_name:
                    visitor.display_name = body.contact_name
                if body.contact_email and not visitor.contact_email:
                    visitor.contact_email = str(body.contact_email)
                if body.preferred_channel and not visitor.preferred_channel:
                    visitor.preferred_channel = body.preferred_channel
                visitor.last_seen_at = datetime.now(timezone.utc)

    if not visitor:
        # Новый анонимный visitor — создаём запись.
        # ВНИМАНИЕ: SiteVisitor.display_name (а не contact_name).
        # contact_name — это поле SiteMessage (денормализация для админ-фильтров).
        visitor = SiteVisitor(
            store_id=store.id,
            auth_provider=None,
            display_name=body.contact_name,
            contact_phone=body.contact_phone,
            contact_email=str(body.contact_email) if body.contact_email else None,
            preferred_channel=body.preferred_channel,
        )
        # SAVEPOINT: при race-condition с параллельным анон-сабмитом одного и того
        # же телефона партиальный UNIQUE-индекс uq_anon_visitor_store_phone бросит
        # IntegrityError — ловим, откатываем только savepoint и подхватываем
        # уже созданного visitor другим запросом.
        try:
            async with db.begin_nested():
                db.add(visitor)
                await db.flush()  # получаем visitor.id до создания SiteMessage
        except IntegrityError:
            visitor = None
            if body.contact_phone:
                visitor = (await db.execute(
                    select(SiteVisitor).where(
                        SiteVisitor.store_id == store.id,
                        SiteVisitor.auth_provider.is_(None),
                        SiteVisitor.contact_phone == body.contact_phone,
                    ).limit(1)
                )).scalar_one_or_none()
            if not visitor:
                raise HTTPException(status_code=409, detail="Конфликт создания клиента, повторите")
            if visitor.is_blocked:
                raise HTTPException(status_code=403, detail="Доступ заблокирован")

    # Создаём заявку с денормализацией данных из visitor
    msg = SiteMessage(
        store_id=store.id,
        visitor_id=visitor.id,
        message_type=body.message_type,
        is_verified=bool(visitor.auth_provider),
        auth_provider=visitor.auth_provider,
        contact_name=body.contact_name or visitor.display_name,
        contact_phone=body.contact_phone or visitor.contact_phone,
        contact_email=(
            str(body.contact_email) if body.contact_email
            else visitor.contact_email
        ),
        preferred_channel=body.preferred_channel or visitor.preferred_channel,
        subject=body.subject,
        body=body.body,
        # Trade-in поля
        tradein_brand=body.tradein.brand if body.tradein else None,
        tradein_model=body.tradein.model if body.tradein else None,
        tradein_storage=body.tradein.storage if body.tradein else None,
        tradein_color=body.tradein.color if body.tradein else None,
        tradein_condition=body.tradein.condition if body.tradein else None,
        tradein_battery_pct=body.tradein.battery_pct if body.tradein else None,
        tradein_completeness=body.tradein.completeness if body.tradein else None,
        tradein_estimated_price=(
            body.tradein.estimated_price if body.tradein and body.tradein.estimated_price else None
        ),
        # Технические метаданные
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        referer=request.headers.get("referer"),
    )
    db.add(msg)

    # Обновляем счётчики visitor
    visitor.total_messages_count = (visitor.total_messages_count or 0) + 1
    visitor.last_seen_at = _now()

    await db.commit()
    return MessageCreatedOut(id=str(msg.id), status=msg.status)


@router.get("/{store_id}/messages/my", response_model=MyMessagesOut)
async def get_my_messages(
    store_id: str,
    store: Store = Depends(get_active_store),
    visitor: SiteVisitor = Depends(require_site_visitor),
    db: AsyncSession = Depends(get_db),
) -> MyMessagesOut:
    """
    История заявок авторизованного посетителя.
    Требует авторизацию через cookie (require_site_visitor).
    """
    rows = (await db.execute(
        select(SiteMessage)
        .where(
            SiteMessage.visitor_id == visitor.id,
            SiteMessage.store_id == store.id,
        )
        .order_by(SiteMessage.created_at.desc())
    )).scalars().all()

    items = [
        MyMessageOut(
            id=str(m.id),
            message_type=m.message_type,
            status=m.status,
            # body_preview: первые 200 символов body (или тема trade-in)
            body_preview=(
                (m.body[:200] if m.body else None)
                or (f"Trade-in: {m.tradein_brand} {m.tradein_model}" if m.tradein_brand else None)
            ),
            last_reply_text=m.last_reply_text,
            answered_at=m.answered_at,
            created_at=m.created_at,
        )
        for m in rows
    ]

    return MyMessagesOut(items=items, total=len(items))


# ── Home blocks (CMS) ────────────────────────────────────────────────────────


class _HomeCardPublic(BaseModel):
    eyebrow: str | None = None
    title: str | None = None
    subtitle: str | None = None
    image_url: str | None = None
    bg_preset: str
    text_dark: bool
    cta_label: str | None = None
    cta_href: str | None = None
    cta_color: str


class _HomeSectionPublic(BaseModel):
    key: str
    enabled: bool
    cards: list[_HomeCardPublic]


class HomeBlocksOut(BaseModel):
    sections: list[_HomeSectionPublic]


@router.get("/{store_id}/home-blocks", response_model=HomeBlocksOut)
@limiter.limit("60/minute")
async def get_home_blocks(
    request: Request,
    store_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Публичный feed блоков главной для mobileax-next SSR.

    Возвращает только enabled секции и enabled карточки внутри них,
    в том порядке как заданы в админке (sort_order).
    """
    store = await db.get(Store, store_id)
    if not store or not store.is_active:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    sections = (
        (
            await db.execute(
                select(HomeSection)
                .where(HomeSection.store_id == store_id, HomeSection.enabled.is_(True))
                .order_by(HomeSection.sort_order, HomeSection.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not sections:
        return HomeBlocksOut(sections=[])

    section_ids = [s.id for s in sections]
    cards = (
        (
            await db.execute(
                select(HomeCard)
                .where(HomeCard.section_id.in_(section_ids), HomeCard.enabled.is_(True))
                .order_by(HomeCard.sort_order, HomeCard.created_at)
            )
        )
        .scalars()
        .all()
    )
    cards_by_section: dict[str, list[HomeCard]] = {}
    for c in cards:
        cards_by_section.setdefault(c.section_id, []).append(c)

    def _img(p: str | None) -> str | None:
        if not p:
            return None
        if p.startswith(("http://", "https://", "/themes/")):
            return p
        return f"/media/{p.lstrip('/')}"

    out_sections = []
    for s in sections:
        out_sections.append(
            _HomeSectionPublic(
                key=s.key,
                enabled=s.enabled,
                cards=[
                    _HomeCardPublic(
                        eyebrow=c.eyebrow,
                        title=c.title,
                        subtitle=c.subtitle,
                        image_url=_img(c.image_path),
                        bg_preset=c.bg_preset,
                        text_dark=c.text_dark,
                        cta_label=c.cta_label,
                        cta_href=c.cta_href,
                        cta_color=c.cta_color,
                    )
                    for c in cards_by_section.get(s.id, [])
                ],
            )
        )

    return HomeBlocksOut(sections=out_sections)


# ── Site header (SSR layout) ────────────────────────────────────────────────


class HeaderPromoOut(BaseModel):
    title: str | None = None
    cta_label: str | None = None
    cta_href: str | None = None


class HeaderCategoryOut(BaseModel):
    id: str
    slug: str
    display_name: str
    sort_order: int


class HeaderBrandOut(BaseModel):
    id: str
    slug: str
    display_name: str
    sort_order: int
    logo_url: str | None = None


class SiteHeaderOut(BaseModel):
    promo: HeaderPromoOut | None = None
    categories: list[HeaderCategoryOut] = []
    brands: list[HeaderBrandOut] = []


@router.get("/{store_id}/header", response_model=SiteHeaderOut)
@limiter.limit("60/minute")
async def get_site_header(
    request: Request,
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> SiteHeaderOut:
    """Header config для shop.basestock.ru — promo + categories + brands.

    SSR-fetch на каждый layout render (rate-limit высокий, 60/min).
    Categories из CatalogCategory (admin раздел «Каталог»), brands из CatalogBrand.
    Promo — из home_sections.key='header_promo' (admin раздел «Магазин»).
    """
    # Promo
    promo_section = (
        await db.execute(
            select(HomeSection).where(
                HomeSection.store_id == store.id,
                HomeSection.key == "header_promo",
                HomeSection.enabled.is_(True),
            )
        )
    ).scalar_one_or_none()

    promo: HeaderPromoOut | None = None
    if promo_section is not None:
        card = (
            await db.execute(
                select(HomeCard)
                .where(HomeCard.section_id == promo_section.id)
                .order_by(HomeCard.sort_order.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if card is not None:
            promo = HeaderPromoOut(
                title=card.title,
                cta_label=card.cta_label,
                cta_href=card.cta_href,
            )

    # Categories
    cats = (
        await db.execute(
            select(CatalogCategory)
            .where(CatalogCategory.is_visible.is_(True))
            .order_by(
                CatalogCategory.sort_order.asc(),
                CatalogCategory.display_name.asc(),
            )
        )
    ).scalars().all()

    # Brands
    brands = (
        await db.execute(
            select(CatalogBrand)
            .where(CatalogBrand.is_visible.is_(True))
            .order_by(
                CatalogBrand.sort_order.asc(),
                CatalogBrand.display_name.asc(),
            )
        )
    ).scalars().all()

    return SiteHeaderOut(
        promo=promo,
        categories=[
            HeaderCategoryOut(
                id=c.id,
                slug=c.slug,
                display_name=c.display_name,
                sort_order=c.sort_order,
            )
            for c in cats
        ],
        brands=[
            HeaderBrandOut(
                id=b.id,
                slug=b.slug,
                display_name=b.display_name,
                sort_order=b.sort_order,
                logo_url=b.logo_url,
            )
            for b in brands
        ],
    )


# ── Site footer (SSR layout) ────────────────────────────────────────────────


class FooterLinkOut(BaseModel):
    label: str
    href: str


class FooterColumnOut(BaseModel):
    key: str  # shop | services | help | social
    title: str | None = None
    links: list[FooterLinkOut] = []


class SiteFooterOut(BaseModel):
    columns: list[FooterColumnOut] = []
    legal: str | None = None


_FOOTER_COL_KEYS = (
    "footer_col_shop",
    "footer_col_services",
    "footer_col_help",
    "footer_col_social",
)


@router.get("/{store_id}/footer", response_model=SiteFooterOut)
@limiter.limit("60/minute")
async def get_site_footer(
    request: Request,
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> SiteFooterOut:
    """Footer config для shop.basestock.ru — 4 mega-cols + legal.

    SSR-fetch на каждый layout render (rate-limit высокий, 60/min).
    Колонки берутся из home_sections.key IN ('footer_col_shop',
    'footer_col_services', 'footer_col_help', 'footer_col_social');
    legal — из home_sections.key='footer_legal' (первая карточка по sort_order,
    subtitle предпочтительнее title). Отсутствующие секции просто не включаем.
    """
    # 1) Колонки: все 4 (или меньше) footer_col_* секции + footer_legal — одним запросом.
    all_keys = list(_FOOTER_COL_KEYS) + ["footer_legal"]
    sections = (
        (
            await db.execute(
                select(HomeSection)
                .where(
                    HomeSection.store_id == store.id,
                    HomeSection.key.in_(all_keys),
                    HomeSection.enabled.is_(True),
                )
                .order_by(HomeSection.key.asc())
            )
        )
        .scalars()
        .all()
    )

    if not sections:
        return SiteFooterOut(columns=[], legal=None)

    section_ids = [s.id for s in sections]

    # 2) Карточки всех секций — батч-SELECT (N+1-safe).
    cards = (
        (
            await db.execute(
                select(HomeCard)
                .where(HomeCard.section_id.in_(section_ids))
                .order_by(HomeCard.sort_order.asc())
            )
        )
        .scalars()
        .all()
    )
    cards_by_section: dict[str, list[HomeCard]] = {}
    for c in cards:
        cards_by_section.setdefault(c.section_id, []).append(c)

    # 3) Группируем footer_col_* в columns в фиксированном порядке.
    sections_by_key = {s.key: s for s in sections if s.key in _FOOTER_COL_KEYS}
    columns: list[FooterColumnOut] = []
    for full_key in _FOOTER_COL_KEYS:
        s = sections_by_key.get(full_key)
        if s is None:
            continue
        short_key = full_key.removeprefix("footer_col_")  # shop | services | help | social
        col_cards = cards_by_section.get(s.id, [])
        columns.append(
            FooterColumnOut(
                key=short_key,
                title=s.title,
                links=[
                    FooterLinkOut(
                        label=c.title or "",
                        href=c.cta_href or "#",
                    )
                    for c in col_cards
                ],
            )
        )

    # 4) Legal — первая карточка footer_legal-секции (по sort_order).
    legal: str | None = None
    legal_section = next((s for s in sections if s.key == "footer_legal"), None)
    if legal_section is not None:
        legal_cards = cards_by_section.get(legal_section.id, [])
        if legal_cards:
            first = legal_cards[0]
            legal = first.subtitle or first.title

    return SiteFooterOut(columns=columns, legal=legal)
