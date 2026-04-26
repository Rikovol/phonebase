"""Админ-роутер: акции магазина (site_promotions) и привязка скидок к товарам (price_overrides).

Используется React-админкой CRM (раздел «Магазин»).
Публичный роутер для сайтов-витрин — в sites.py, не трогать.
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.access import can_modify_site_promotion, can_view_site_promotion
from app.api.auth import require_active
from app.core.database import get_db
from app.models.business import PriceOverride, Product, SitePromotion, User

# Бизнес-правило: скидка типа `fixed` не может превышать 3000₽
MAX_FIXED_DISCOUNT = 3000.0

router = APIRouter()


# ── Схемы ────────────────────────────────────────────────────────────────────


class SitePromotionOut(BaseModel):
    id: str
    store_id: Optional[str]
    title: str
    body: Optional[str]
    code: Optional[str]
    discount_type: str
    discount_value: Optional[float]
    applies_to_brand: Optional[str]
    applies_to_category: Optional[str]
    applies_to_products: Optional[str]
    min_order_amount: Optional[float]
    starts_at: Optional[str]
    ends_at: Optional[str]
    is_active: bool
    priority: int
    banner_image: Optional[str]
    landing_url: Optional[str]
    created_by: Optional[str]
    created_at: str
    updated_at: str


class SitePromotionCreate(BaseModel):
    store_id: Optional[str] = None  # admin может создать глобальную (None) или per-store
    title: str
    body: Optional[str] = None
    code: Optional[str] = None
    discount_type: str = "info_only"  # percent | fixed | info_only
    discount_value: Optional[float] = None
    applies_to_brand: Optional[str] = None
    applies_to_category: Optional[str] = None
    applies_to_products: Optional[str] = None
    min_order_amount: Optional[float] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    is_active: bool = True
    priority: int = 0
    banner_image: Optional[str] = None
    landing_url: Optional[str] = None


class SitePromotionUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    code: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    applies_to_brand: Optional[str] = None
    applies_to_category: Optional[str] = None
    applies_to_products: Optional[str] = None
    min_order_amount: Optional[float] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None
    banner_image: Optional[str] = None
    landing_url: Optional[str] = None


class ApplyToProductBody(BaseModel):
    override_price: float
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None


class ApplyToProductsBody(BaseModel):
    """Батч: применить fixed-акцию к нескольким товарам.
    override_price вычисляется автоматически как price_retail - discount_value."""
    product_ids: List[str]
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _promo_out(p: SitePromotion) -> SitePromotionOut:
    return SitePromotionOut(
        id=p.id,
        store_id=p.store_id,
        title=p.title,
        body=p.body,
        code=p.code,
        discount_type=p.discount_type,
        discount_value=float(p.discount_value) if p.discount_value else None,
        applies_to_brand=p.applies_to_brand,
        applies_to_category=p.applies_to_category,
        applies_to_products=p.applies_to_products,
        min_order_amount=float(p.min_order_amount) if p.min_order_amount else None,
        starts_at=p.starts_at.isoformat() if p.starts_at else None,
        ends_at=p.ends_at.isoformat() if p.ends_at else None,
        is_active=p.is_active,
        priority=p.priority,
        banner_image=p.banner_image,
        landing_url=p.landing_url,
        created_by=p.created_by,
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
    )


_VALID_DISCOUNT_TYPES = {"percent", "fixed", "info_only"}


def _validate_discount(discount_type: Optional[str], discount_value: Optional[float]) -> None:
    """Проверяет тип и сумму скидки.
    fixed: discount_value (₽) должно быть <= MAX_FIXED_DISCOUNT.
    """
    if discount_type is not None and discount_type not in _VALID_DISCOUNT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый discount_type. Допустимые: {_VALID_DISCOUNT_TYPES}",
        )
    if (
        discount_type == "fixed"
        and discount_value is not None
        and float(discount_value) > MAX_FIXED_DISCOUNT
    ):
        raise HTTPException(
            status_code=422,
            detail=f"Максимальная сумма скидки — {int(MAX_FIXED_DISCOUNT)}₽",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/")
async def list_promotions(
    store_id: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Список акций. Staff видит свои + глобальные (store_id=NULL). Admin — все."""
    query = select(SitePromotion)

    if current_user.role == "staff":
        # staff: только свои + глобальные
        query = query.where(
            or_(
                SitePromotion.store_id == current_user.store_id,
                SitePromotion.store_id.is_(None),
            )
        )
    elif store_id:
        query = query.where(SitePromotion.store_id == store_id)

    if is_active is not None:
        query = query.where(SitePromotion.is_active == is_active)
    if search:
        pattern = f"%{search}%"
        query = query.where(SitePromotion.title.ilike(pattern))

    query = query.order_by(SitePromotion.priority.desc(), SitePromotion.created_at.desc())
    rows = (await db.execute(query)).scalars().all()
    return {"items": [_promo_out(p) for p in rows], "total": len(rows)}


@router.get("/{promotion_id}", response_model=SitePromotionOut)
async def get_promotion(
    promotion_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    promo = await db.get(SitePromotion, promotion_id)
    if not promo:
        raise HTTPException(status_code=404, detail="Акция не найдена")
    if not can_view_site_promotion(current_user, promo):
        raise HTTPException(status_code=403, detail="Нет доступа к акции")
    return _promo_out(promo)


@router.post("/")
async def create_promotion(
    body: SitePromotionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Роль «Инфо» не может создавать акции")

    # Определяем store_id для новой акции
    if current_user.role == "staff":
        # staff создаёт только для своего магазина
        effective_store_id = current_user.store_id
    else:
        # admin: None = глобальная, или явный store_id
        effective_store_id = body.store_id

    _validate_discount(body.discount_type, body.discount_value)

    promo = SitePromotion(
        store_id=effective_store_id,
        title=body.title,
        body=body.body,
        code=body.code,
        discount_type=body.discount_type,
        discount_value=body.discount_value,
        applies_to_brand=body.applies_to_brand,
        applies_to_category=body.applies_to_category,
        applies_to_products=body.applies_to_products,
        min_order_amount=body.min_order_amount,
        starts_at=datetime.fromisoformat(body.starts_at).replace(tzinfo=timezone.utc) if body.starts_at else None,
        ends_at=datetime.fromisoformat(body.ends_at).replace(tzinfo=timezone.utc) if body.ends_at else None,
        is_active=body.is_active,
        priority=body.priority,
        banner_image=body.banner_image,
        landing_url=body.landing_url,
        created_by=str(current_user.id),
    )
    db.add(promo)
    await db.commit()
    await db.refresh(promo)
    return {"status": "created", "id": promo.id}


@router.patch("/{promotion_id}")
async def update_promotion(
    promotion_id: str,
    body: SitePromotionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    promo = await db.get(SitePromotion, promotion_id)
    if not promo:
        raise HTTPException(status_code=404, detail="Акция не найдена")
    if not can_modify_site_promotion(current_user, promo):
        raise HTTPException(status_code=403, detail="Нет доступа к редактированию акции")

    # Проверяем разногласие между новым и старым значением:
    # либо переданы оба, либо проверяем относительно текущих значений.
    new_type = body.discount_type if body.discount_type is not None else promo.discount_type
    new_value = body.discount_value if body.discount_value is not None else (
        float(promo.discount_value) if promo.discount_value is not None else None
    )
    _validate_discount(new_type, new_value)

    if body.title is not None:
        promo.title = body.title
    if body.body is not None:
        promo.body = body.body or None
    if body.code is not None:
        promo.code = body.code or None
    if body.discount_type is not None:
        promo.discount_type = body.discount_type
    if body.discount_value is not None:
        promo.discount_value = body.discount_value
    if body.applies_to_brand is not None:
        promo.applies_to_brand = body.applies_to_brand or None
    if body.applies_to_category is not None:
        promo.applies_to_category = body.applies_to_category or None
    if body.applies_to_products is not None:
        promo.applies_to_products = body.applies_to_products or None
    if body.min_order_amount is not None:
        promo.min_order_amount = body.min_order_amount
    if body.starts_at is not None:
        promo.starts_at = datetime.fromisoformat(body.starts_at).replace(tzinfo=timezone.utc) if body.starts_at else None
    if body.ends_at is not None:
        promo.ends_at = datetime.fromisoformat(body.ends_at).replace(tzinfo=timezone.utc) if body.ends_at else None
    if body.is_active is not None:
        promo.is_active = body.is_active
    if body.priority is not None:
        promo.priority = body.priority
    if body.banner_image is not None:
        promo.banner_image = body.banner_image or None
    if body.landing_url is not None:
        promo.landing_url = body.landing_url or None

    promo.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "updated", "id": promo.id}


@router.delete("/{promotion_id}")
async def delete_promotion(
    promotion_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Soft-delete: is_active=False. PriceOverride FK история сохраняется."""
    promo = await db.get(SitePromotion, promotion_id)
    if not promo:
        raise HTTPException(status_code=404, detail="Акция не найдена")
    if not can_modify_site_promotion(current_user, promo):
        raise HTTPException(status_code=403, detail="Нет доступа к удалению акции")

    promo.is_active = False
    promo.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "deactivated", "id": promo.id}


@router.post("/{promotion_id}/apply-to-product/{product_id}")
async def apply_to_product(
    promotion_id: str,
    product_id: str,
    body: ApplyToProductBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Применить акцию к товару: создать PriceOverride.

    Правило «одна активная скидка на товар в магазине»:
    - Ищем существующий активный PriceOverride на (product_id, store_id).
    - Если есть — деактивируем (is_active=False).
    - Создаём новый PriceOverride с is_active=True.
    """
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Роль «Инфо» не может применять акции")

    promo = await db.get(SitePromotion, promotion_id)
    if not promo:
        raise HTTPException(status_code=404, detail="Акция не найдена")
    if not promo.is_active:
        raise HTTPException(status_code=400, detail="Нельзя применить неактивную акцию")

    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")

    # Определяем store_id для привязки скидки
    if current_user.role == "staff":
        override_store_id = current_user.store_id
    else:
        # admin: берём store_id из акции, или из магазина товара
        override_store_id = promo.store_id or product.store_id

    # Проверяем права на акцию и товар
    if current_user.role == "staff":
        if promo.store_id is not None and promo.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="Нет доступа к акции другого магазина")
        if product.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="Нет доступа к товарам другого магазина")

    # Деактивируем существующий активный PriceOverride на (product_id, store_id)
    existing_active = (await db.execute(
        select(PriceOverride).where(
            PriceOverride.product_id == product_id,
            PriceOverride.store_id == override_store_id,
            PriceOverride.is_active == True,  # noqa: E712
        )
    )).scalars().all()

    now = datetime.now(timezone.utc)
    for old in existing_active:
        old.is_active = False
        old.updated_at = now

    # Создаём новый активный PriceOverride
    new_override = PriceOverride(
        product_id=product_id,
        store_id=override_store_id,
        promotion_id=promotion_id,
        override_price=body.override_price,
        starts_at=datetime.fromisoformat(body.starts_at).replace(tzinfo=timezone.utc) if body.starts_at else None,
        ends_at=datetime.fromisoformat(body.ends_at).replace(tzinfo=timezone.utc) if body.ends_at else None,
        is_active=True,
        created_by=str(current_user.id),
    )
    try:
        db.add(new_override)
        await db.commit()
        await db.refresh(new_override)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Параллельная попытка создать скидку на этот товар. Повторите операцию."
        )

    return {
        "status": "applied",
        "price_override_id": new_override.id,
        "deactivated_count": len(existing_active),
    }


@router.post("/{promotion_id}/apply-to-products")
async def apply_to_products(
    promotion_id: str,
    body: ApplyToProductsBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Батч-применение fixed-акции к нескольким товарам.

    override_price вычисляется как `product.price_retail - promo.discount_value`.
    Товары без price_retail или с new_price <= 0 пропускаются — возвращаются в `skipped`.
    """
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Роль «Инфо» не может применять акции")

    promo = await db.get(SitePromotion, promotion_id)
    if not promo:
        raise HTTPException(status_code=404, detail="Акция не найдена")
    if not promo.is_active:
        raise HTTPException(status_code=400, detail="Нельзя применить неактивную акцию")
    if promo.discount_type != "fixed" or not promo.discount_value:
        raise HTTPException(
            status_code=400,
            detail="Батч-применение поддерживается только для акций типа `fixed` с указанной суммой скидки",
        )
    if current_user.role == "staff":
        if promo.store_id is not None and promo.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="Нет доступа к акции другого магазина")
    if not body.product_ids:
        raise HTTPException(status_code=400, detail="Список товаров пуст")

    discount = float(promo.discount_value)
    try:
        starts_at = (
            datetime.fromisoformat(body.starts_at).replace(tzinfo=timezone.utc)
            if body.starts_at else None
        )
        ends_at = (
            datetime.fromisoformat(body.ends_at).replace(tzinfo=timezone.utc)
            if body.ends_at else None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Некорректный формат даты: {e}")

    now = datetime.now(timezone.utc)
    applied_ids: list[str] = []
    skipped: list[dict] = []
    # Дедупликация: один и тот же product_id в запросе не должен обрабатываться дважды
    unique_product_ids = list(dict.fromkeys(body.product_ids))

    for product_id in unique_product_ids:
        product = await db.get(Product, product_id)
        if not product:
            skipped.append({"product_id": product_id, "reason": "не найден"})
            continue

        # Определяем store_id для PriceOverride
        if current_user.role == "staff":
            override_store_id = current_user.store_id
            if product.store_id != current_user.store_id:
                skipped.append({"product_id": product_id, "reason": "чужой магазин"})
                continue
        else:
            override_store_id = promo.store_id or product.store_id

        if not product.price_retail:
            skipped.append({"product_id": product_id, "reason": "нет price_retail"})
            continue

        new_price = float(product.price_retail) - discount
        if new_price <= 0:
            skipped.append({"product_id": product_id, "reason": "цена после скидки <= 0"})
            continue

        # Правило «одна активная скидка на товар в магазине» — то же что и в
        # apply_to_product (single). Применение новой акции снимает все
        # активные оверрайды этого товара, в т.ч. от других акций. Намеренно.
        existing_active = (await db.execute(
            select(PriceOverride).where(
                PriceOverride.product_id == product_id,
                PriceOverride.store_id == override_store_id,
                PriceOverride.is_active == True,  # noqa: E712
            )
        )).scalars().all()
        for old in existing_active:
            old.is_active = False
            old.updated_at = now

        new_override = PriceOverride(
            product_id=product_id,
            store_id=override_store_id,
            promotion_id=promotion_id,
            override_price=new_price,
            starts_at=starts_at,
            ends_at=ends_at,
            is_active=True,
            created_by=str(current_user.id),
        )
        db.add(new_override)
        applied_ids.append(product_id)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Параллельная попытка создать скидку. Повторите операцию.",
        )

    return {
        "status": "applied",
        "applied_count": len(applied_ids),
        "applied_ids": applied_ids,
        "skipped": skipped,
    }


@router.delete("/{promotion_id}/apply-to-product/{product_id}")
async def remove_from_product(
    promotion_id: str,
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Деактивировать PriceOverride для пары (product_id, promotion_id)."""
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Роль «Инфо» не может снимать акции")

    promo = await db.get(SitePromotion, promotion_id)
    if not promo:
        raise HTTPException(status_code=404, detail="Акция не найдена")

    if current_user.role == "staff":
        if promo.store_id is not None and promo.store_id != current_user.store_id:
            raise HTTPException(status_code=403, detail="Нет доступа к акции другого магазина")
        target_store_id = current_user.store_id
    else:
        target_store_id = promo.store_id

    overrides = (await db.execute(
        select(PriceOverride).where(
            PriceOverride.product_id == product_id,
            PriceOverride.promotion_id == promotion_id,
            PriceOverride.is_active == True,  # noqa: E712
            *([PriceOverride.store_id == target_store_id] if target_store_id else []),
        )
    )).scalars().all()

    if not overrides:
        raise HTTPException(status_code=404, detail="Активная скидка не найдена")

    now = datetime.now(timezone.utc)
    for override in overrides:
        override.is_active = False
        override.updated_at = now

    await db.commit()
    return {"status": "deactivated", "count": len(overrides)}
