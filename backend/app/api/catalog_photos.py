"""
API каталожных фото для новых товаров.

Фото привязаны к наименованию (product_key = lower(brand|model|storage|color)),
а не к конкретному IMEI. Одно фото на конкретный цвет модели.
При поиске: сначала точный ключ с цветом, fallback — без цвета.
"""
import asyncio
import io
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.business import CatalogPhoto, Product, StaffActionLog, Store, User

router = APIRouter()

ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def make_product_key(brand: str, model: str, storage: str, color: str = "") -> str:
    """Нормализованный ключ наименования: lower(brand|model|storage|color).

    Если color пустой — ключ без цвета (обратная совместимость).
    """
    parts = [
        (brand or "").strip(),
        (model or "").strip(),
        (storage or "").strip(),
    ]
    c = (color or "").strip()
    if c:
        parts.append(c)
    return "|".join(parts).lower()


def make_product_key_no_color(brand: str, model: str, storage: str) -> str:
    """Ключ без цвета — для fallback-поиска и обратной совместимости."""
    return f"{(brand or '').strip()}|{(model or '').strip()}|{(storage or '').strip()}".lower()


def _reject_info(user: User) -> None:
    if user.role == "info":
        raise HTTPException(status_code=403, detail="Недоступно для роли «Инфо»")


@router.get("/by-key")
async def list_catalog_photos(
    store_id: str = Query(...),
    brand: str = Query(""),
    model: str = Query(""),
    storage: str = Query(""),
    color: str = Query(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Список каталожных фото по наименованию товара.

    Поиск: сначала точный ключ с цветом, fallback — без цвета.
    """
    key = make_product_key(brand, model, storage, color)
    result = await db.execute(
        select(CatalogPhoto)
        .where(CatalogPhoto.store_id == store_id, CatalogPhoto.product_key == key)
        .order_by(CatalogPhoto.is_main.desc(), CatalogPhoto.created_at.asc())
    )
    photos = result.scalars().all()

    # Fallback: если с цветом не нашли — ищем без цвета
    if not photos and color:
        key_no_color = make_product_key_no_color(brand, model, storage)
        result = await db.execute(
            select(CatalogPhoto)
            .where(CatalogPhoto.store_id == store_id, CatalogPhoto.product_key == key_no_color)
            .order_by(CatalogPhoto.is_main.desc(), CatalogPhoto.created_at.asc())
        )
        photos = result.scalars().all()
        if photos:
            key = key_no_color

    return {
        "product_key": key,
        "photos": [
            {
                "id": p.id,
                "url": f"/media/{p.file_path}",
                "is_main": p.is_main,
            }
            for p in photos
        ],
    }


@router.post("/upload")
async def upload_catalog_photo(
    store_id: str = Query(...),
    brand: str = Query(""),
    model: str = Query(""),
    storage: str = Query(""),
    color: str = Query(""),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Загрузить каталожное фото для наименования товара."""
    _reject_info(current_user)

    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    if current_user.role != "admin" and current_user.store_id != store_id:
        raise HTTPException(status_code=403, detail="Нет доступа к данному магазину")

    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Разрешены только изображения: JPEG, PNG, WEBP")

    raw = await file.read()
    max_b = settings.MAX_PHOTO_SIZE_MB * 1024 * 1024
    if len(raw) > max_b:
        raise HTTPException(status_code=400, detail=f"Файл больше {settings.MAX_PHOTO_SIZE_MB} МБ")

    if not re.match(r'^[a-f0-9\-]+$', store_id):
        raise HTTPException(status_code=400, detail="Недопустимый store_id")
    media_root = Path(settings.MEDIA_ROOT).resolve()
    rel_dir = f"catalog/{store_id}"
    out_dir = (media_root / rel_dir).resolve()
    if not str(out_dir).startswith(str(media_root)):
        raise HTTPException(status_code=400, detail="Недопустимый путь")
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = uuid.uuid4().hex
    out_path_base = out_dir / fname

    def _process() -> tuple[Path, str]:
        try:
            img = Image.open(io.BytesIO(raw))
            img.load()
        except OSError as exc:
            raise ValueError("Не удалось прочитать изображение") from exc
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(ct, ".jpg")
        out = Path(str(out_path_base) + ext)
        if ct == "image/jpeg":
            rgb = img.convert("RGB") if img.mode in ("RGBA", "P", "LA") else img
            if rgb.mode != "RGB":
                rgb = rgb.convert("RGB")
            rgb.save(out, format="JPEG", quality=88, optimize=True)
        elif ct == "image/png":
            img.save(out, format="PNG", optimize=True)
        else:
            img.save(out, format="WEBP", quality=88, method=4)
        return out, ext

    try:
        out_path, ext = await asyncio.get_running_loop().run_in_executor(None, _process)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    rel_path = f"{rel_dir}/{fname}{ext}".replace("\\", "/")
    key = make_product_key(brand, model, storage, color)

    existing_count = (
        await db.execute(
            select(func.count()).select_from(CatalogPhoto)
            .where(CatalogPhoto.store_id == store_id, CatalogPhoto.product_key == key)
        )
    ).scalar() or 0
    is_main = existing_count == 0

    photo = CatalogPhoto(
        store_id=store_id,
        product_key=key,
        uploaded_by=current_user.id,
        file_path=rel_path,
        file_size=out_path.stat().st_size,
        is_main=is_main,
    )
    db.add(photo)
    db.add(StaffActionLog(
        user_id=str(current_user.id),
        action="catalog_photo_upload",
        target_id=key,
        details=f"Каталожное фото: {model} {storage}",
        store_name=store.name,
    ))
    await db.commit()
    await db.refresh(photo)

    return {
        "id": photo.id,
        "url": f"/media/{rel_path}",
        "is_main": photo.is_main,
    }


@router.delete("/{photo_id}")
async def delete_catalog_photo(
    photo_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Удалить каталожное фото."""
    _reject_info(current_user)

    photo = await db.get(CatalogPhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Фото не найдено")

    if current_user.role != "admin" and current_user.store_id != photo.store_id:
        raise HTTPException(status_code=403, detail="Нет доступа к данному магазину")

    was_main = photo.is_main
    store_id = photo.store_id
    product_key = photo.product_key

    store_row = (await db.execute(select(Store).where(Store.id == store_id))).scalar_one_or_none()
    db.add(StaffActionLog(
        user_id=str(current_user.id),
        action="catalog_photo_delete",
        target_id=product_key,
        details=f"Удаление каталожного фото",
        store_name=store_row.name if store_row else None,
    ))

    await db.delete(photo)
    await db.flush()

    if was_main:
        res_other = await db.execute(
            select(CatalogPhoto)
            .where(CatalogPhoto.store_id == store_id, CatalogPhoto.product_key == product_key)
            .order_by(CatalogPhoto.created_at.asc())
            .limit(1)
        )
        other = res_other.scalars().first()
        if other:
            other.is_main = True

    await db.commit()

    full = Path(settings.MEDIA_ROOT) / photo.file_path
    try:
        if full.is_file():
            full.unlink()
    except OSError:
        pass

    return {"status": "deleted", "id": photo_id}


@router.post("/scrape-biggeek")
async def scrape_biggeek_images(
    store_id: str = Query(...),
    brand: str = Query(""),
    model: str = Query(""),
    storage: str = Query(""),
    color: str = Query(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Парсинг изображений товара с biggeek.ru (прямой HTTP)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Только для администраторов")

    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    from app.services.scrape_biggeek import scrape_product_images, download_and_save_image

    try:
        image_urls = await scrape_product_images(brand, model, storage, color)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка парсинга: {exc}")

    if not image_urls:
        return {"saved": 0, "message": "Изображения не найдены на biggeek.ru"}

    key = make_product_key(brand, model, storage, color)

    existing_count = (
        await db.execute(
            select(func.count()).select_from(CatalogPhoto)
            .where(CatalogPhoto.store_id == store_id, CatalogPhoto.product_key == key)
        )
    ).scalar() or 0

    saved = 0
    for url in image_urls:
        result = await download_and_save_image(
            url, store_id, key, settings.MEDIA_ROOT,
        )
        if not result:
            continue

        rel_path, file_size = result
        is_main = existing_count == 0 and saved == 0

        photo = CatalogPhoto(
            store_id=store_id,
            product_key=key,
            uploaded_by=current_user.id,
            file_path=rel_path,
            file_size=file_size,
            is_main=is_main,
        )
        db.add(photo)
        saved += 1

    if saved > 0:
        db.add(StaffActionLog(
            user_id=str(current_user.id),
            action="catalog_photo_scrape_biggeek",
            target_id=key,
            details=f"Парсинг biggeek.ru: {model} {storage}, загружено {saved} фото",
            store_name=store.name,
        ))
        await db.commit()

    return {"saved": saved, "found": len(image_urls), "product_key": key}


@router.post("/scrape-biggeek-all")
async def scrape_biggeek_all(
    store_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Массовый парсинг фото с biggeek.ru для всех новых товаров без каталожных фото."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Только для администраторов")

    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    # Все уникальные наименования новых товаров в этом магазине (с цветом)
    from sqlalchemy import and_
    rows = (await db.execute(
        select(
            Product.brand,
            Product.model,
            Product.storage,
            Product.color,
        )
        .where(and_(
            Product.store_id == store_id,
            Product.is_new == True,  # noqa: E712
            Product.is_sold == False,  # noqa: E712
        ))
        .group_by(Product.brand, Product.model, Product.storage, Product.color)
    )).all()

    if not rows:
        return {"processed": 0, "scraped": 0, "skipped": 0, "message": "Нет новых товаров"}

    # Дедупликация по product_key (NULL и "" нормализуются одинаково)
    unique_keys: dict[str, tuple[str, str, str, str]] = {}
    for brand, model, storage, color in rows:
        key = make_product_key(brand or "", model or "", storage or "", color or "")
        if key not in unique_keys:
            unique_keys[key] = (brand or "", model or "", storage or "", color or "")

    # Проверяем существующие фото: color-specific + legacy (без цвета)
    all_check_keys = set(unique_keys.keys())
    for brand, model, storage, color in unique_keys.values():
        all_check_keys.add(make_product_key_no_color(brand, model, storage))

    existing_keys = set()
    if all_check_keys:
        existing_rows = (await db.execute(
            select(CatalogPhoto.product_key)
            .where(CatalogPhoto.store_id == store_id, CatalogPhoto.product_key.in_(list(all_check_keys)))
            .distinct()
        )).scalars().all()
        existing_keys = set(existing_rows)

    keys_to_scrape = [
        (brand, model, storage, color, key)
        for key, (brand, model, storage, color) in unique_keys.items()
        if key not in existing_keys
        and make_product_key_no_color(brand, model, storage) not in existing_keys
    ]

    if not keys_to_scrape:
        return {"processed": len(rows), "scraped": 0, "skipped": len(rows), "message": "У всех товаров уже есть фото"}

    from app.services.scrape_biggeek import scrape_product_images, download_and_save_image

    total_saved = 0
    scraped_count = 0
    errors = []

    for i, (brand, model, storage, color, key) in enumerate(keys_to_scrape):
        if i > 0:
            await asyncio.sleep(1)  # Rate-limit: 1 сек между товарами
        try:
            image_urls = await scrape_product_images(brand, model, storage, color)
        except Exception as exc:
            errors.append(f"{brand} {model} {storage} {color}: {exc}")
            continue

        if not image_urls:
            continue

        saved = 0
        for url in image_urls:
            result = await download_and_save_image(
                url, store_id, key, settings.MEDIA_ROOT,
            )
            if not result:
                continue
            rel_path, file_size = result
            photo = CatalogPhoto(
                store_id=store_id,
                product_key=key,
                uploaded_by=current_user.id,
                file_path=rel_path,
                file_size=file_size,
                is_main=(saved == 0),
            )
            db.add(photo)
            saved += 1

        if saved > 0:
            total_saved += saved
            scraped_count += 1

    db.add(StaffActionLog(
        user_id=str(current_user.id),
        action="catalog_photo_scrape_biggeek_all",
        target_id=store_id,
        details=f"Массовый парсинг biggeek.ru: {scraped_count}/{len(keys_to_scrape)} товаров, {total_saved} фото, ошибок {len(errors)}",
        store_name=store.name,
    ))
    await db.commit()

    return {
        "processed": len(unique_keys),
        "scraped": scraped_count,
        "total_saved": total_saved,
        "skipped": len(existing_keys),
        "errors": errors[:10] if errors else [],
    }


@router.post("/{photo_id}/rotate")
async def rotate_catalog_photo(
    photo_id: str,
    degrees: int = Query(90, description="Угол поворота (90 или -90)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Повернуть каталожное фото."""
    if degrees not in (90, -90, 180, -180, 270, -270):
        raise HTTPException(status_code=400, detail="Допустимые углы: 90, -90, 180, -180, 270, -270")
    _reject_info(current_user)

    photo = await db.get(CatalogPhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Фото не найдено")

    if current_user.role != "admin" and current_user.store_id != photo.store_id:
        raise HTTPException(status_code=403, detail="Нет доступа к данному магазину")

    full = (Path(settings.MEDIA_ROOT) / photo.file_path).resolve()
    if not full.is_file():
        raise HTTPException(status_code=404, detail="Файл изображения не найден на диске")

    def _rotate() -> None:
        img = Image.open(full)
        img.load()
        rotated = img.rotate(-degrees, expand=True)
        ext = full.suffix.lower()
        tmp = full.with_suffix(".tmp" + full.suffix)
        try:
            if ext in (".jpg", ".jpeg"):
                rgb = rotated if rotated.mode == "RGB" else rotated.convert("RGB")
                rgb.save(tmp, format="JPEG", quality=88, optimize=True)
            elif ext == ".png":
                rotated.save(tmp, format="PNG", optimize=True)
            else:
                rotated.save(tmp, format="WEBP", quality=88, method=4)
            tmp.replace(full)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    try:
        await asyncio.get_running_loop().run_in_executor(None, _rotate)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Не удалось обработать изображение") from exc

    return {"status": "rotated", "id": photo_id}
