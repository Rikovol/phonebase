"""
Stage 3 catalog migration: brand-specific cats → 7 product-type cats.

Создаёт 7 новых product-type категорий (Смартфоны / Планшеты / Ноутбуки /
Часы / Аудио / Приставки / Для волос) + 5 новых брендов (Dyson / JBL /
Hopestar / Nintendo / Valve), переассигнит 337 моделей со старых
brand-specific категорий на новые, скрывает старые, синхронизирует
Product.category snapshot.

Idempotent: можно прогонять много раз (UPSERT по slug). Безопасно по
отношению к Sony brand (visible) — оставляет как есть.

Spec: docs/superpowers/specs/2026-05-31-stage3-redesign.md

Запуск:
    DRY-RUN (no commit):
        docker compose exec backend python scripts/migrate_categories_stage3.py --dry-run
    APPLY (commit transaction):
        docker compose exec backend python scripts/migrate_categories_stage3.py --apply

В deployment runbook celery-beat должен быть остановлен на время APPLY
(защита от auto_import race condition с _find_or_create_category).
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Backend root в path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update, text  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.models.business import (  # noqa: E402
    CatalogCategory,
    CatalogBrand,
    CatalogModel,
)


# ─── Целевая структура каталога (Stage 3) ─────────────────────────────────────

NEW_CATEGORIES = [
    # (slug, display_name, sort_order)
    ('smartphones', 'Смартфоны', 1),
    ('tablets', 'Планшеты', 2),
    ('laptops', 'Ноутбуки', 3),
    ('watches', 'Часы', 4),
    ('audio', 'Аудио', 5),
    ('consoles', 'Приставки', 6),
    ('hair', 'Для волос', 7),
]

NEW_BRANDS = [
    # (slug, display_name, sort_order)
    ('dyson', 'Dyson', 30),
    ('jbl', 'JBL', 31),
    ('hopestar', 'Hopestar', 32),
    ('nintendo', 'Nintendo', 33),
    ('valve', 'Valve', 34),
]

# Старый brand-specific slug → новый product-type slug.
# Категория-источник остаётся в БД (is_visible=false), модели мигрируют.
CATEGORY_MAP: dict[str, str] = {
    'iphone': 'smartphones',
    'samsung-mob': 'smartphones',
    'xiaomi': 'smartphones',
    'prochie-tel': 'smartphones',
    'ipad': 'tablets',
    'planshety': 'tablets',
    'macbook': 'laptops',
    'imac': 'laptops',
    'noutbuki-i-kompyutery': 'laptops',
    'iwatch': 'watches',
    'samsung-watch': 'watches',
    'umnye-chasy-i-braslety': 'watches',
    'airpods': 'audio',
    'polnorazmernye-naushniki': 'audio',
    'sony-ps': 'consoles',
}


async def _ensure_categories(db, dry_run: bool) -> dict[str, str]:
    """Создать/обновить 7 новых категорий. Возвращает slug → id."""
    ids: dict[str, str] = {}
    for slug, name, order in NEW_CATEGORIES:
        existing = (await db.execute(
            select(CatalogCategory).where(CatalogCategory.slug == slug)
        )).scalar_one_or_none()
        if existing:
            existing.is_visible = True
            existing.sort_order = order
            existing.display_name = name
            print(f'  ~ update category {slug} → {name} (sort={order})')
            ids[slug] = existing.id
        else:
            obj = CatalogCategory(
                slug=slug, display_name=name, sort_order=order, is_visible=True
            )
            db.add(obj)
            if not dry_run:
                await db.flush()
                ids[slug] = obj.id
            else:
                ids[slug] = f'<new-{slug}>'
            print(f'  + create category {slug} → {name} (sort={order})')
    return ids


async def _ensure_brands(db, dry_run: bool) -> dict[str, str]:
    """Создать/обновить 5 новых брендов. Возвращает slug → id."""
    ids: dict[str, str] = {}
    for slug, name, order in NEW_BRANDS:
        existing = (await db.execute(
            select(CatalogBrand).where(CatalogBrand.slug == slug)
        )).scalar_one_or_none()
        if existing:
            existing.is_visible = True
            existing.sort_order = order
            existing.display_name = name
            print(f'  ~ update brand {slug} → {name} (sort={order})')
            ids[slug] = existing.id
        else:
            obj = CatalogBrand(
                slug=slug, display_name=name, sort_order=order, is_visible=True
            )
            db.add(obj)
            if not dry_run:
                await db.flush()
                ids[slug] = obj.id
            else:
                ids[slug] = f'<new-{slug}>'
            print(f'  + create brand {slug} → {name} (sort={order})')
    return ids


async def _reassign_models(
    db,
    new_cat_ids: dict[str, str],
    dry_run: bool,
) -> tuple[int, int]:
    """Перенести модели со старых категорий на новые.

    Возвращает (moved_models, products_cascaded).
    """
    moved_models = 0
    products_cascaded = 0

    for old_slug, new_slug in CATEGORY_MAP.items():
        old_cat = (await db.execute(
            select(CatalogCategory).where(CatalogCategory.slug == old_slug)
        )).scalar_one_or_none()
        if old_cat is None:
            print(f'  skip {old_slug}: category not found (already migrated?)')
            continue

        # Count models to move (только те, что ещё не в новой category — idempotent)
        models_count = (await db.execute(
            select(text('COUNT(*)'))
            .select_from(CatalogModel)
            .where(CatalogModel.category_id == old_cat.id)
        )).scalar_one()

        if models_count == 0:
            print(f'  {old_slug} → {new_slug}: 0 models (already empty)')
            # Hide старая всё равно (idempotent)
            if old_cat.is_visible:
                old_cat.is_visible = False
                print(f'    ~ hide empty category {old_slug}')
            continue

        new_cat_id = new_cat_ids[new_slug]
        new_cat_name = next(name for s, name, _ in NEW_CATEGORIES if s == new_slug)

        print(f'  {old_slug} → {new_slug}: {models_count} models')

        if not dry_run:
            # 1. Reassign catalog_models.category_id
            await db.execute(
                update(CatalogModel)
                .where(CatalogModel.category_id == old_cat.id)
                .values(category_id=new_cat_id)
            )
            # 2. Cascade на Product.category snapshot (видно в каталоге как facet)
            result = await db.execute(text("""
                UPDATE products
                SET category = :new_name
                WHERE model_id IN (
                    SELECT id FROM catalog_models WHERE category_id = :new_cat_id
                )
                  AND (category IS DISTINCT FROM :new_name)
            """), {'new_name': new_cat_name, 'new_cat_id': new_cat_id})
            cascaded = result.rowcount or 0
            products_cascaded += cascaded
            print(f'    + cascaded Product.category for {cascaded} products')
            # 3. Hide old category
            old_cat.is_visible = False
            print(f'    ~ hide old category {old_slug}')

        moved_models += models_count

    return moved_models, products_cascaded


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true', help='Show plan, no commit')
    group.add_argument('--apply', action='store_true', help='Apply migration in transaction')
    args = parser.parse_args()
    dry_run = args.dry_run

    print('=' * 72)
    print(f'Stage 3 catalog migration — {"DRY-RUN" if dry_run else "APPLY"}')
    print('=' * 72)
    print()

    async with AsyncSessionLocal() as db:
        try:
            # Before snapshot
            before_visible = (await db.execute(
                select(text('COUNT(*)'))
                .select_from(CatalogCategory)
                .where(CatalogCategory.is_visible.is_(True))
            )).scalar_one()
            print(f'BEFORE: {before_visible} visible categories')
            print()

            print('--- Step 1: ensure 7 new categories ---')
            new_cat_ids = await _ensure_categories(db, dry_run)
            print()

            print('--- Step 2: ensure 5 new brands ---')
            await _ensure_brands(db, dry_run)
            print()

            print('--- Step 3: reassign models + cascade Product.category + hide old cats ---')
            moved, cascaded = await _reassign_models(db, new_cat_ids, dry_run)
            print()

            # After snapshot (within transaction)
            if not dry_run:
                await db.flush()
            after_visible = (await db.execute(
                select(text('COUNT(*)'))
                .select_from(CatalogCategory)
                .where(CatalogCategory.is_visible.is_(True))
            )).scalar_one()

            print('=' * 72)
            print(f'Summary: {moved} models would be moved, {cascaded} products cascaded')
            print(f'AFTER (visible): {before_visible} → {after_visible} categories')
            print('=' * 72)

            if dry_run:
                await db.rollback()
                print('\nDRY-RUN — rolled back, no changes committed.')
            else:
                await db.commit()
                print('\nAPPLIED — transaction committed.')

        except Exception as exc:
            await db.rollback()
            print(f'\nERROR (rolled back): {exc!r}', file=sys.stderr)
            raise


if __name__ == '__main__':
    asyncio.run(main())
