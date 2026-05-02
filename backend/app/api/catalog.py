"""Admin API для нормализованного каталога.

Иерархия М.Видео: Категория → Бренд → Модель. Глобальная для всех магазинов.
Витрины (sites.py) читают через /sites/{store_id}/menu и /catalog?model_id=...

Все write-операции требуют admin-роли. Чтение — любой активный пользователь
(staff видит каталог, но не редактирует).

«Требуют проверки» = модели с is_visible=false, созданные импортом 1С автоматически.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_active, require_admin
from app.core.database import get_db
from app.core.slug import slugify
from app.models.business import (
    CatalogBrand,
    CatalogCategory,
    CatalogModel,
    CatalogPhoto,
    Product,
    User,
)

router = APIRouter()


# ============================================================================
# Schemas
# ============================================================================


class CategoryOut(BaseModel):
    id: str
    slug: str
    display_name: str
    icon_url: str | None
    sort_order: int
    is_visible: bool
    models_count: int
    created_at: datetime
    updated_at: datetime


class BrandOut(BaseModel):
    id: str
    slug: str
    display_name: str
    logo_url: str | None
    sort_order: int
    is_visible: bool
    models_count: int
    created_at: datetime
    updated_at: datetime


class ModelOut(BaseModel):
    id: str
    brand_id: str
    brand_name: str
    category_id: str
    category_name: str
    slug: str
    display_name: str
    hero_image_url: str | None
    sort_order: int
    is_visible: bool
    products_count: int
    created_at: datetime
    updated_at: datetime


class CategoryIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=100)
    icon_url: str | None = Field(default=None, max_length=500)
    sort_order: int = 0
    is_visible: bool = True


class CategoryPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=100)
    icon_url: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None
    is_visible: bool | None = None


class BrandIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=100)
    logo_url: str | None = Field(default=None, max_length=500)
    sort_order: int = 0
    is_visible: bool = True


class BrandPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=100)
    logo_url: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None
    is_visible: bool | None = None


class ModelIn(BaseModel):
    brand_id: str
    category_id: str
    display_name: str = Field(min_length=1, max_length=160)
    slug: str | None = Field(default=None, max_length=120)
    hero_image_url: str | None = Field(default=None, max_length=500)
    sort_order: int = 0
    is_visible: bool = True


class ModelPatch(BaseModel):
    brand_id: str | None = None
    category_id: str | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str | None = Field(default=None, max_length=120)
    hero_image_url: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None
    is_visible: bool | None = None


class MergeResult(BaseModel):
    moved_products: int
    deleted_model_id: str
    target_model_id: str


class ModelsListOut(BaseModel):
    items: list[ModelOut]
    total: int
    limit: int
    offset: int


# ============================================================================
# Categories
# ============================================================================


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(
    include_hidden: bool = Query(True, description="Включать скрытые (admin UI)"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_active),
) -> list[CategoryOut]:
    q = select(CatalogCategory).order_by(CatalogCategory.sort_order, CatalogCategory.display_name)
    if not include_hidden:
        q = q.where(CatalogCategory.is_visible.is_(True))
    rows = (await db.execute(q)).scalars().all()

    counts = dict((await db.execute(
        select(CatalogModel.category_id, func.count(CatalogModel.id))
        .group_by(CatalogModel.category_id)
    )).all())

    return [
        CategoryOut(
            id=c.id, slug=c.slug, display_name=c.display_name, icon_url=c.icon_url,
            sort_order=c.sort_order, is_visible=c.is_visible,
            models_count=counts.get(c.id, 0),
            created_at=c.created_at, updated_at=c.updated_at,
        ) for c in rows
    ]


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(
    payload: CategoryIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> CategoryOut:
    # case-insensitive дубль по display_name — иначе resolve_catalog_refs()
    # падает MultipleResultsFound (Codex r4).
    if await _category_name_exists(db, payload.display_name, exclude_id=None):
        raise HTTPException(status_code=409, detail="Категория с таким названием уже существует")
    obj = CatalogCategory(
        slug=(payload.slug or slugify(payload.display_name)) or "category",
        display_name=payload.display_name,
        icon_url=payload.icon_url,
        sort_order=payload.sort_order,
        is_visible=payload.is_visible,
    )
    db.add(obj)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Категория с таким slug уже существует")
    await db.refresh(obj)
    return _category_out(obj, models_count=0)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: str,
    payload: CategoryPatch,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> CategoryOut:
    obj = await db.get(CatalogCategory, category_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    data = payload.model_dump(exclude_unset=True)
    if "display_name" in data and await _category_name_exists(
        db, data["display_name"], exclude_id=category_id
    ):
        raise HTTPException(status_code=409, detail="Категория с таким названием уже существует")
    old_cat_name = obj.display_name
    for k, v in data.items():
        setattr(obj, k, v)

    # Синк Product.category при rename (Codex r6). catalog_photos не зависят
    # от категории — только от brand|model|storage|color, поэтому не трогаем.
    if "display_name" in data and obj.display_name != old_cat_name:
        await db.execute(text("""
            UPDATE products SET category = :new_name
            WHERE model_id IN (SELECT id FROM catalog_models WHERE category_id = :cat_id)
        """), {"new_name": obj.display_name, "cat_id": obj.id})

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Категория с таким slug уже существует")
    await db.refresh(obj)
    cnt = (await db.execute(
        select(func.count(CatalogModel.id)).where(CatalogModel.category_id == obj.id)
    )).scalar_one()
    return _category_out(obj, models_count=cnt)


@router.delete("/categories/{category_id}", status_code=204, response_model=None)
async def delete_category(
    category_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    obj = await db.get(CatalogCategory, category_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    cnt = (await db.execute(
        select(func.count(CatalogModel.id)).where(CatalogModel.category_id == category_id)
    )).scalar_one()
    if cnt:
        raise HTTPException(
            status_code=409,
            detail=f"Нельзя удалить: в категории {cnt} моделей. Сначала перенесите или удалите модели.",
        )
    await db.delete(obj)
    await db.commit()


# ============================================================================
# Brands
# ============================================================================


@router.get("/brands", response_model=list[BrandOut])
async def list_brands(
    include_hidden: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_active),
) -> list[BrandOut]:
    q = select(CatalogBrand).order_by(CatalogBrand.sort_order, CatalogBrand.display_name)
    if not include_hidden:
        q = q.where(CatalogBrand.is_visible.is_(True))
    rows = (await db.execute(q)).scalars().all()

    counts = dict((await db.execute(
        select(CatalogModel.brand_id, func.count(CatalogModel.id))
        .group_by(CatalogModel.brand_id)
    )).all())

    return [
        BrandOut(
            id=b.id, slug=b.slug, display_name=b.display_name, logo_url=b.logo_url,
            sort_order=b.sort_order, is_visible=b.is_visible,
            models_count=counts.get(b.id, 0),
            created_at=b.created_at, updated_at=b.updated_at,
        ) for b in rows
    ]


@router.post("/brands", response_model=BrandOut, status_code=201)
async def create_brand(
    payload: BrandIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> BrandOut:
    if await _brand_name_exists(db, payload.display_name, exclude_id=None):
        raise HTTPException(status_code=409, detail="Бренд с таким названием уже существует")
    obj = CatalogBrand(
        slug=(payload.slug or slugify(payload.display_name)) or "brand",
        display_name=payload.display_name,
        logo_url=payload.logo_url,
        sort_order=payload.sort_order,
        is_visible=payload.is_visible,
    )
    db.add(obj)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Бренд с таким slug уже существует")
    await db.refresh(obj)
    return _brand_out(obj, models_count=0)


@router.patch("/brands/{brand_id}", response_model=BrandOut)
async def update_brand(
    brand_id: str,
    payload: BrandPatch,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> BrandOut:
    obj = await db.get(CatalogBrand, brand_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Бренд не найден")
    data = payload.model_dump(exclude_unset=True)
    if "display_name" in data and await _brand_name_exists(
        db, data["display_name"], exclude_id=brand_id
    ):
        raise HTTPException(status_code=409, detail="Бренд с таким названием уже существует")
    old_brand_name = obj.display_name
    for k, v in data.items():
        setattr(obj, k, v)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Бренд с таким slug уже существует")

    # Синк denormalized данных при rename бренда (Codex r6).
    if "display_name" in data and obj.display_name != old_brand_name:
        # 1. Product.brand для всех товаров со ссылкой на любую модель этого бренда.
        await db.execute(text("""
            UPDATE products SET brand = :new_name
            WHERE model_id IN (SELECT id FROM catalog_models WHERE brand_id = :brand_id)
        """), {"new_name": obj.display_name, "brand_id": obj.id})

        # 2. catalog_photos.product_key для каждой модели этого бренда.
        # product_key = lower(brand|model|...), меняется prefix lower(brand)|.
        models_of_brand = (await db.execute(
            select(CatalogModel.display_name).where(CatalogModel.brand_id == obj.id)
        )).scalars().all()
        for m_name in models_of_brand:
            await _rekey_catalog_photos(
                db,
                src_brand_lower=old_brand_name.strip().lower(),
                src_model_lower=m_name.strip().lower(),
                dst_brand_lower=obj.display_name.strip().lower(),
                dst_model_lower=m_name.strip().lower(),
            )

    await db.commit()
    await db.refresh(obj)
    cnt = (await db.execute(
        select(func.count(CatalogModel.id)).where(CatalogModel.brand_id == obj.id)
    )).scalar_one()
    return _brand_out(obj, models_count=cnt)


@router.delete("/brands/{brand_id}", status_code=204, response_model=None)
async def delete_brand(
    brand_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    obj = await db.get(CatalogBrand, brand_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Бренд не найден")
    cnt = (await db.execute(
        select(func.count(CatalogModel.id)).where(CatalogModel.brand_id == brand_id)
    )).scalar_one()
    if cnt:
        raise HTTPException(
            status_code=409,
            detail=f"Нельзя удалить: у бренда {cnt} моделей. Сначала перенесите или удалите.",
        )
    await db.delete(obj)
    await db.commit()


# ============================================================================
# Models
# ============================================================================


@router.get("/models", response_model=ModelsListOut)
async def list_models(
    brand_id: str | None = Query(None),
    category_id: str | None = Query(None),
    q: str | None = Query(None, description="Поиск по display_name"),
    needs_review: bool = Query(False, description="Только скрытые (созданные импортом)"),
    include_hidden: bool = Query(True),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_active),
) -> ModelsListOut:
    """Список моделей с фильтрами, счётчиками товаров и общим total для пагинации.

    needs_review=true → принудительно is_visible=false (включает include_hidden).
    Сортировка по бренду+категории+названию для предсказуемости.
    """
    # Общий фильтр выносим в отдельный список — переиспользуем для page query и count.
    filters = []
    if brand_id:
        filters.append(CatalogModel.brand_id == brand_id)
    if category_id:
        filters.append(CatalogModel.category_id == category_id)
    if q:
        filters.append(CatalogModel.display_name.ilike(f"%{q.strip()}%"))
    if needs_review:
        filters.append(CatalogModel.is_visible.is_(False))
    elif not include_hidden:
        filters.append(CatalogModel.is_visible.is_(True))

    total = (await db.execute(
        select(func.count(CatalogModel.id)).where(*filters)
    )).scalar_one()

    if total == 0:
        return ModelsListOut(items=[], total=0, limit=limit, offset=offset)

    stmt = (
        select(CatalogModel, CatalogBrand, CatalogCategory)
        .join(CatalogBrand, CatalogBrand.id == CatalogModel.brand_id)
        .join(CatalogCategory, CatalogCategory.id == CatalogModel.category_id)
        .where(*filters)
        .order_by(CatalogBrand.display_name, CatalogCategory.display_name, CatalogModel.display_name)
        .limit(limit).offset(offset)
    )
    rows = (await db.execute(stmt)).all()

    model_ids = [m.id for m, _b, _c in rows]
    counts = dict((await db.execute(
        select(Product.model_id, func.count(Product.id))
        .where(Product.model_id.in_(model_ids))
        .group_by(Product.model_id)
    )).all()) if model_ids else {}

    items = [
        _model_out(m, brand=b, category=c, products_count=counts.get(m.id, 0))
        for m, b, c in rows
    ]
    return ModelsListOut(items=items, total=total, limit=limit, offset=offset)


@router.post("/models", response_model=ModelOut, status_code=201)
async def create_model(
    payload: ModelIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> ModelOut:
    brand = await db.get(CatalogBrand, payload.brand_id)
    if brand is None:
        raise HTTPException(status_code=404, detail="Бренд не найден")
    category = await db.get(CatalogCategory, payload.category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Категория не найдена")

    if await _model_name_exists_in_brand(
        db, payload.brand_id, payload.display_name, exclude_id=None
    ):
        raise HTTPException(
            status_code=409,
            detail="У этого бренда уже есть модель с таким названием",
        )

    obj = CatalogModel(
        brand_id=payload.brand_id,
        category_id=payload.category_id,
        slug=(payload.slug or slugify(payload.display_name)) or "model",
        display_name=payload.display_name,
        hero_image_url=payload.hero_image_url,
        sort_order=payload.sort_order,
        is_visible=payload.is_visible,
    )
    db.add(obj)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Модель с таким slug уже существует у этого бренда")
    await db.refresh(obj)
    return _model_out(obj, brand=brand, category=category, products_count=0)


@router.patch("/models/{model_id}", response_model=ModelOut)
async def update_model(
    model_id: str,
    payload: ModelPatch,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> ModelOut:
    obj = await db.get(CatalogModel, model_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Модель не найдена")
    data = payload.model_dump(exclude_unset=True)
    if "brand_id" in data and not await db.get(CatalogBrand, data["brand_id"]):
        raise HTTPException(status_code=404, detail="Бренд не найден")
    if "category_id" in data and not await db.get(CatalogCategory, data["category_id"]):
        raise HTTPException(status_code=404, detail="Категория не найдена")

    # Дубль (brand, name) после изменения → 409. Цель будущая модель — учитываем
    # новый brand_id если он меняется.
    if "display_name" in data or "brand_id" in data:
        target_brand = data.get("brand_id", obj.brand_id)
        target_name = data.get("display_name", obj.display_name)
        if await _model_name_exists_in_brand(
            db, target_brand, target_name, exclude_id=obj.id
        ):
            raise HTTPException(
                status_code=409,
                detail="У этого бренда уже есть модель с таким названием",
            )

    # Запоминаем старые denormalized значения для синхронизации Product/CatalogPhoto.
    old_brand = await db.get(CatalogBrand, obj.brand_id)
    old_brand_name = old_brand.display_name if old_brand else None
    old_model_name = obj.display_name

    for k, v in data.items():
        setattr(obj, k, v)
    # SQLAlchemy commit отдельно от sync — иначе flush-конфликт UNIQUE при IntegrityError
    # перепутает порядок. Поэтому сначала flush сюда, потом sync, потом commit.
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Модель с таким slug уже существует у этого бренда")

    brand = await db.get(CatalogBrand, obj.brand_id)
    category = await db.get(CatalogCategory, obj.category_id)

    # Синк denormalized данных, если изменилось то, что отображается на витрине.
    needs_sync = (
        "brand_id" in data
        or "category_id" in data
        or "display_name" in data
    )
    if needs_sync and brand and category:
        await _sync_products_for_model(
            db, model_id=obj.id, brand=brand, category=category, model=obj
        )
        if old_brand_name and old_model_name:
            await _rekey_catalog_photos(
                db,
                src_brand_lower=old_brand_name.strip().lower(),
                src_model_lower=old_model_name.strip().lower(),
                dst_brand_lower=brand.display_name.strip().lower(),
                dst_model_lower=obj.display_name.strip().lower(),
            )

    await db.commit()
    await db.refresh(obj)
    cnt = (await db.execute(
        select(func.count(Product.id)).where(Product.model_id == obj.id)
    )).scalar_one()
    return _model_out(obj, brand=brand, category=category, products_count=cnt)


@router.delete("/models/{model_id}", status_code=204, response_model=None)
async def delete_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    obj = await db.get(CatalogModel, model_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Модель не найдена")
    cnt = (await db.execute(
        select(func.count(Product.id)).where(Product.model_id == model_id)
    )).scalar_one()
    if cnt:
        raise HTTPException(
            status_code=409,
            detail=f"Нельзя удалить: на модель ссылаются {cnt} товаров. Используйте «Слить с другой моделью».",
        )
    await db.delete(obj)
    await db.commit()


@router.post("/models/{model_id}/merge", response_model=MergeResult)
async def merge_model(
    model_id: str,
    into: str = Query(..., description="ID целевой модели — куда переносим товары"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> MergeResult:
    """Слить дубль `model_id` в `into`: переписать products.model_id, удалить дубль.

    Часто нужно после импорта 1С — он мог создать «iPhone 17 Pro» и «iPhone 17 PRO»
    как разные строки. Продавец выбирает основную, остальные сливает.
    """
    if model_id == into:
        raise HTTPException(status_code=400, detail="Источник и цель совпадают")

    # Берём row-level lock на обе модели в детерминированном порядке (по id), чтобы
    # параллельные merge'ы двух админов не привели к гонке (UPDATE→DELETE неатомарны).
    lock_ids = sorted([model_id, into])
    locked = (await db.execute(
        select(CatalogModel).where(CatalogModel.id.in_(lock_ids)).with_for_update()
    )).scalars().all()
    by_id = {m.id: m for m in locked}
    src = by_id.get(model_id)
    dst = by_id.get(into)
    if src is None:
        raise HTTPException(status_code=404, detail="Источник не найден")
    if dst is None:
        raise HTTPException(status_code=404, detail="Целевая модель не найдена")

    # Запрещаем сливать кросс-бренд: товар «Apple iPhone 17» не должен уехать
    # в «Samsung Galaxy». Кросс-категорию тоже запрещаем: иначе после merge'а
    # счётчики /menu и фильтры окажутся неконсистентны (Qwen review).
    if src.brand_id != dst.brand_id:
        raise HTTPException(
            status_code=400,
            detail="Нельзя слить модели разных брендов. Сначала переместите товары вручную.",
        )
    if src.category_id != dst.category_id:
        raise HTTPException(
            status_code=400,
            detail="Нельзя слить модели разных категорий.",
        )

    # brand/category совпадают (проверено выше) → достаточно одного lookup.
    brand = await db.get(CatalogBrand, dst.brand_id)
    category = await db.get(CatalogCategory, dst.category_id)
    if brand is None or category is None:
        raise HTTPException(status_code=500, detail="Целостность каталога нарушена")

    # 1. Помимо model_id переписываем denormalized строковые поля Product.brand/model/
    # category на значения целевой модели. Иначе витрина продолжает рендерить старые
    # названия и группирует товары как разные карточки в /catalog?condition=new
    # (агрегация идёт по строкам brand|model|storage|color) — Codex review r1.
    res = await db.execute(
        update(Product)
        .where(Product.model_id == model_id)
        .values(
            model_id=into,
            brand=brand.display_name,
            model=dst.display_name,
            category=category.display_name,
        )
    )
    moved = getattr(res, "rowcount", 0) or 0

    # 2. Переносим catalog_photos с src.product_key prefix на dst (Codex review r2).
    # CatalogPhoto.product_key = lower(brand|model|storage[|color]); если не перенести
    # — после merge'а фото становятся недоступны через витрину.
    await _rekey_catalog_photos(
        db,
        src_brand_lower=brand.display_name.strip().lower(),  # brand общий для src/dst
        src_model_lower=src.display_name.strip().lower(),
        dst_brand_lower=brand.display_name.strip().lower(),
        dst_model_lower=dst.display_name.strip().lower(),
    )

    await db.delete(src)
    await db.commit()

    return MergeResult(moved_products=moved, deleted_model_id=model_id, target_model_id=into)


# ============================================================================
# Helpers
# ============================================================================


def _category_out(c: CatalogCategory, *, models_count: int) -> CategoryOut:
    return CategoryOut(
        id=c.id, slug=c.slug, display_name=c.display_name, icon_url=c.icon_url,
        sort_order=c.sort_order, is_visible=c.is_visible, models_count=models_count,
        created_at=c.created_at, updated_at=c.updated_at,
    )


def _brand_out(b: CatalogBrand, *, models_count: int) -> BrandOut:
    return BrandOut(
        id=b.id, slug=b.slug, display_name=b.display_name, logo_url=b.logo_url,
        sort_order=b.sort_order, is_visible=b.is_visible, models_count=models_count,
        created_at=b.created_at, updated_at=b.updated_at,
    )


def _model_out(
    m: CatalogModel, *, brand: CatalogBrand, category: CatalogCategory, products_count: int
) -> ModelOut:
    return ModelOut(
        id=m.id, brand_id=m.brand_id, brand_name=brand.display_name,
        category_id=m.category_id, category_name=category.display_name,
        slug=m.slug, display_name=m.display_name, hero_image_url=m.hero_image_url,
        sort_order=m.sort_order, is_visible=m.is_visible, products_count=products_count,
        created_at=m.created_at, updated_at=m.updated_at,
    )


# ============================================================================
# Name uniqueness checks (Codex review r4)
#
# resolve_catalog_refs() из импорта 1С ищет по case-insensitive display_name
# через scalar_one_or_none(). Если админ создаст две записи с одинаковым именем
# (но разными slug'ами) — следующий импорт упадёт MultipleResultsFound.
# ============================================================================


async def _category_name_exists(
    db: AsyncSession, name: str, *, exclude_id: str | None
) -> bool:
    # TRIM хранимого — иначе «Apple» и «Apple » пройдут (Codex r6).
    q = select(CatalogCategory.id).where(
        func.lower(func.trim(CatalogCategory.display_name)) == name.strip().lower()
    )
    if exclude_id:
        q = q.where(CatalogCategory.id != exclude_id)
    return (await db.execute(q)).first() is not None


async def _brand_name_exists(
    db: AsyncSession, name: str, *, exclude_id: str | None
) -> bool:
    q = select(CatalogBrand.id).where(
        func.lower(func.trim(CatalogBrand.display_name)) == name.strip().lower()
    )
    if exclude_id:
        q = q.where(CatalogBrand.id != exclude_id)
    return (await db.execute(q)).first() is not None


async def _model_name_exists_in_brand(
    db: AsyncSession, brand_id: str, name: str, *, exclude_id: str | None
) -> bool:
    """Уникальность модели по (brand_id, lower(trim(display_name))) — без category."""
    q = select(CatalogModel.id).where(
        CatalogModel.brand_id == brand_id,
        func.lower(func.trim(CatalogModel.display_name)) == name.strip().lower(),
    )
    if exclude_id:
        q = q.where(CatalogModel.id != exclude_id)
    return (await db.execute(q)).first() is not None


# ============================================================================
# Sync helpers — denormalized данные витрины (Codex review r2)
#
# При изменении/слиянии модели нужно синхронизировать:
#   1. Product.brand / model / category (рендер витрины + product_key для new)
#   2. CatalogPhoto.product_key (фото привязаны по lower(brand|model|storage[|color]))
# Иначе после переименования / merge'а витрина показывает старые названия и
# теряет фото до следующего импорта 1С.
# ============================================================================


async def _sync_products_for_model(
    db: AsyncSession,
    *,
    model_id: str,
    brand: CatalogBrand,
    category: CatalogCategory,
    model: CatalogModel,
    where_old_model_id: str | None = None,
) -> int:
    """Прописать Product.brand/model/category всем товарам с данным model_id.

    where_old_model_id — для merge: товары всё ещё имеют старый FK в момент
    вызова, и WHERE должен матчить по нему. Для update_model — по текущему.
    """
    target_model_id = where_old_model_id or model_id
    res = await db.execute(
        update(Product)
        .where(Product.model_id == target_model_id)
        .values(
            brand=brand.display_name,
            model=model.display_name,
            category=category.display_name,
        )
    )
    return getattr(res, "rowcount", 0) or 0


async def _rekey_catalog_photos(
    db: AsyncSession,
    *,
    src_brand_lower: str,
    src_model_lower: str,
    dst_brand_lower: str,
    dst_model_lower: str,
) -> int:
    """Перенести product_key в catalog_photos с (src_brand|src_model|...) на
    (dst_brand|dst_model|...). product_key хранится в lowercase, формат
    «brand|model|storage[|color]» — переписываем prefix через две первые секции.

    Если src_prefix == dst_prefix → no-op.
    """
    if src_brand_lower == dst_brand_lower and src_model_lower == dst_model_lower:
        return 0
    src_prefix = f"{src_brand_lower}|{src_model_lower}|"
    dst_prefix = f"{dst_brand_lower}|{dst_model_lower}|"
    res = await db.execute(text("""
        UPDATE catalog_photos
        SET product_key = :dst_prefix || SUBSTRING(product_key FROM :cut)
        WHERE product_key LIKE :like_pat
    """), {
        "dst_prefix": dst_prefix,
        "cut": len(src_prefix) + 1,   # PG SUBSTRING — 1-based
        "like_pat": src_prefix + "%",
    })
    return getattr(res, "rowcount", 0) or 0
