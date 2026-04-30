"""Резолвер каталожных ссылок для импорта 1С.

Принимает строки brand/category/model из импорта, возвращает FK на нормализованные
catalog_categories / catalog_brands / catalog_models. Если записи нет — создаёт
скрытыми (is_visible=false), чтобы продавец одобрил их в админке («Требуют проверки»).

Case-insensitive по display_name (чтобы «Apple» и «apple» не плодили дубли).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.slug import slugify
from app.models.business import CatalogBrand, CatalogCategory, CatalogModel

logger = logging.getLogger(__name__)


@dataclass
class CatalogRefs:
    brand_id: str
    category_id: str
    model_id: str
    created: list[str]  # человекочитаемые строки про авто-созданные записи (для ImportLog)


async def resolve_catalog_refs(
    db: AsyncSession,
    *,
    brand: str | None,
    category: str | None,
    model: str | None,
) -> CatalogRefs | None:
    """Возвращает CatalogRefs, либо None если хотя бы одно из полей пустое.

    NOTE: коммит не делает — вызывающий код решает когда коммитить.
    """
    if not (brand and category and model):
        return None

    brand = brand.strip()
    category = category.strip()
    model = model.strip()
    if not (brand and category and model):
        return None

    created: list[str] = []

    brand_obj = await _find_or_create_brand(db, brand, created)
    category_obj = await _find_or_create_category(db, category, created)
    model_obj = await _find_or_create_model(
        db, brand_obj=brand_obj, category_obj=category_obj, model_name=model, created=created
    )

    return CatalogRefs(
        brand_id=brand_obj.id,
        category_id=category_obj.id,
        model_id=model_obj.id,
        created=created,
    )


async def _find_or_create_brand(db: AsyncSession, name: str, created: list[str]) -> CatalogBrand:
    """Race-safe upsert: SAVEPOINT + IntegrityError → повторный SELECT.

    Без savepoint параллельные импорты могут оба пройти SELECT и упасть на UNIQUE.
    """
    found = await _select_brand_ci(db, name)
    if found is not None:
        return found

    base = slugify(name) or "brand"
    slug = await _unique_slug_brand(db, base)
    try:
        async with db.begin_nested():
            obj = CatalogBrand(slug=slug, display_name=name, is_visible=False)
            db.add(obj)
            await db.flush()
    except IntegrityError:
        # Параллельный импорт уже вставил тот же бренд — читаем существующую запись.
        existing = await _select_brand_ci(db, name)
        if existing is not None:
            return existing
        raise
    created.append(f"бренд «{name}»")
    logger.info("catalog_refs: создан скрытый бренд «%s» (%s)", name, obj.id)
    return obj


async def _select_brand_ci(db: AsyncSession, name: str) -> CatalogBrand | None:
    # TRIM хранимого display_name — admin UI может сохранить «Apple » с пробелом,
    # без trim сравнение упустит запись и импорт создаст дубль (Codex r5).
    return (await db.execute(
        select(CatalogBrand).where(
            func.lower(func.trim(CatalogBrand.display_name)) == name.strip().lower()
        )
    )).scalar_one_or_none()


async def _find_or_create_category(db: AsyncSession, name: str, created: list[str]) -> CatalogCategory:
    found = await _select_category_ci(db, name)
    if found is not None:
        return found

    base = slugify(name) or "category"
    slug = await _unique_slug_category(db, base)
    try:
        async with db.begin_nested():
            obj = CatalogCategory(slug=slug, display_name=name, is_visible=False)
            db.add(obj)
            await db.flush()
    except IntegrityError:
        existing = await _select_category_ci(db, name)
        if existing is not None:
            return existing
        raise
    created.append(f"категория «{name}»")
    logger.info("catalog_refs: создана скрытая категория «%s» (%s)", name, obj.id)
    return obj


async def _select_category_ci(db: AsyncSession, name: str) -> CatalogCategory | None:
    return (await db.execute(
        select(CatalogCategory).where(
            func.lower(func.trim(CatalogCategory.display_name)) == name.strip().lower()
        )
    )).scalar_one_or_none()


async def _find_or_create_model(
    db: AsyncSession,
    *,
    brand_obj: CatalogBrand,
    category_obj: CatalogCategory,
    model_name: str,
    created: list[str],
) -> CatalogModel:
    # Уникальность модели — (brand, lower(display_name)) БЕЗ category. Storefront
    # агрегирует товары по product_key = brand|model|storage|color без категории
    # (sites.py::_catalog_new). Если разрешить две модели «iPhone 17» в разных
    # категориях — на витрине они слипнутся в одну карточку и фото будут общие
    # (Codex review r3 — отмена предыдущего фикса по Qwen).
    found = await _select_model_ci(db, brand_obj.id, model_name)
    if found is not None:
        if found.category_id != category_obj.id:
            logger.warning(
                "catalog_refs: модель «%s / %s» уже существует в категории «%s», "
                "но импорт пришёл в «%s» — оставляем первую (data error в 1С)",
                brand_obj.display_name, model_name, found.category_id, category_obj.id,
            )
        return found

    base = slugify(model_name) or "model"
    slug = await _unique_slug_model(db, brand_obj.id, base)
    try:
        async with db.begin_nested():
            obj = CatalogModel(
                brand_id=brand_obj.id,
                category_id=category_obj.id,
                slug=slug,
                display_name=model_name,
                is_visible=False,
            )
            db.add(obj)
            await db.flush()
    except IntegrityError:
        existing = await _select_model_ci(db, brand_obj.id, model_name)
        if existing is not None:
            return existing
        raise
    created.append(f"модель «{brand_obj.display_name} / {model_name}»")
    logger.info("catalog_refs: создана скрытая модель «%s / %s» (%s)", brand_obj.display_name, model_name, obj.id)
    return obj


async def _select_model_ci(
    db: AsyncSession, brand_id: str, name: str
) -> CatalogModel | None:
    """Поиск модели по (brand, display_name) case-insensitive — БЕЗ category.

    См. _find_or_create_model: storefront агрегирует без category, поэтому
    модель уникальна по brand+name.
    """
    return (await db.execute(
        select(CatalogModel).where(
            CatalogModel.brand_id == brand_id,
            func.lower(func.trim(CatalogModel.display_name)) == name.strip().lower(),
        )
    )).scalar_one_or_none()


# ----- slug uniqueness ------------------------------------------------------

async def _unique_slug_brand(db: AsyncSession, base: str) -> str:
    """slug может конфликтовать если две разных строки слугифицируются одинаково
    («iPhone 17 Pro» и «iPhone-17-Pro» → 'iphone-17-pro'). При конфликте даём
    хвост -2, -3, … — это редко, поэтому inline lookup приемлем по производительности.
    """
    return await _next_free_slug(db, CatalogBrand, base, extra_filter=None)


async def _unique_slug_category(db: AsyncSession, base: str) -> str:
    return await _next_free_slug(db, CatalogCategory, base, extra_filter=None)


async def _unique_slug_model(db: AsyncSession, brand_id: str, base: str) -> str:
    return await _next_free_slug(
        db, CatalogModel, base, extra_filter=(CatalogModel.brand_id == brand_id)
    )


async def _next_free_slug(db: AsyncSession, table, base: str, *, extra_filter) -> str:
    candidate = base
    attempt = 1
    while True:
        filters = [table.slug == candidate]
        if extra_filter is not None:
            filters.append(extra_filter)
        exists = (await db.execute(select(table.id).where(and_(*filters)))).scalar_one_or_none()
        if exists is None:
            return candidate
        attempt += 1
        candidate = f"{base}-{attempt}"
