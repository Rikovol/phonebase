"""
Скрипт очистки дублей каталожных фото.

Для каждого (store_id, product_key) оставляет только уникальные файлы
(по имени файла), удаляет лишние записи из БД и файлы с диска.

Запуск:
    cd /opt/phonebase && docker compose exec backend python scripts/cleanup_duplicate_photos.py

Или локально:
    cd backend && python scripts/cleanup_duplicate_photos.py
"""
import asyncio
import sys
from pathlib import Path

# Добавляем корень backend в path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func
from app.core.database import AsyncSessionLocal
from app.core.config import settings
from app.models.business import CatalogPhoto


async def cleanup():
    media_root = Path(settings.MEDIA_ROOT)
    total_deleted = 0
    files_to_remove = []  # собираем пути, удаляем после коммита

    async with AsyncSessionLocal() as db:
        # Находим все (store_id, product_key) с более чем 1 фото
        groups = (await db.execute(
            select(CatalogPhoto.store_id, CatalogPhoto.product_key, func.count(CatalogPhoto.id))
            .group_by(CatalogPhoto.store_id, CatalogPhoto.product_key)
            .having(func.count(CatalogPhoto.id) > 1)
        )).all()

        print(f"Найдено {len(groups)} групп с потенциальными дублями")

        for store_id, product_key, count in groups:
            photos = (await db.execute(
                select(CatalogPhoto)
                .where(
                    CatalogPhoto.store_id == store_id,
                    CatalogPhoto.product_key == product_key,
                )
                .order_by(CatalogPhoto.is_main.desc(), CatalogPhoto.created_at.asc())
            )).scalars().all()

            # Дедупликация по имени файла
            seen_filenames = set()
            keep = []
            dupes = []

            for photo in photos:
                fname = Path(photo.file_path).name
                if fname in seen_filenames:
                    dupes.append(photo)
                else:
                    seen_filenames.add(fname)
                    keep.append(photo)

            if not dupes:
                continue

            print(f"  {product_key} (store={str(store_id)[:8]}...): "
                  f"{len(photos)} фото -> оставляем {len(keep)}, удаляем {len(dupes)}")

            for dupe in dupes:
                files_to_remove.append(media_root / dupe.file_path)
                await db.delete(dupe)
                total_deleted += 1

            # Убедиться что хотя бы одно фото is_main
            has_main = any(p.is_main for p in keep)
            if not has_main and keep:
                keep[0].is_main = True

        await db.commit()

    # Удаляем файлы с диска ПОСЛЕ успешного коммита
    files_removed = 0
    for path in files_to_remove:
        if path.is_file():
            path.unlink()
            files_removed += 1

    print(f"\nИтого: удалено {total_deleted} дублей из БД, "
          f"удалено {files_removed} файлов с диска")


if __name__ == "__main__":
    asyncio.run(cleanup())
