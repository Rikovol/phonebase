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

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
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


@router.post("/counts")
async def catalog_photo_counts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Batch-подсчёт каталожных фото для списка наименований.

    Принимает JSON-массив объектов {store_id, brand, model, storage, color}.
    Возвращает {counts: {"store|brand|model|storage|color": N, ...}}.
    Один запрос вместо десятков /by-key — не попадает под rate-limit.
    """
    raw = await request.json()
    if not isinstance(raw, list):
        return {"counts": {}}

    keys = [item for item in raw if isinstance(item, dict)]
    if not keys:
        return {"counts": {}}
    if len(keys) > 500:
        raise HTTPException(status_code=400, detail="Максимум 500 ключей за запрос")

    # Строим ключи: color-specific + no-color fallback
    # front_key -> (product_key, pk_no_color, store_id)
    lookup: dict[str, tuple[str, str, str]] = {}
    all_check_keys: set[str] = set()
    all_store_ids: set[str] = set()

    for item in keys:
        sid = item.get("store_id", "")
        b = item.get("brand", "")
        m = item.get("model", "")
        s = item.get("storage", "")
        c = item.get("color", "")
        if not sid:
            continue
        pk = make_product_key(b, m, s, c)
        pk_nc = make_product_key_no_color(b, m, s)
        front_key = f"{sid}|{b.lower()}|{m.lower()}|{s.lower()}|{c.lower()}"
        lookup[front_key] = (pk, pk_nc, sid)
        all_check_keys.add(pk)
        if c.strip():
            all_check_keys.add(pk_nc)
        all_store_ids.add(sid)

    if not all_store_ids:
        return {"counts": {}}

    # Один SQL-запрос: count по (store_id, product_key)
    rows = (await db.execute(
        select(CatalogPhoto.store_id, CatalogPhoto.product_key, func.count(CatalogPhoto.id))
        .where(
            CatalogPhoto.store_id.in_(list(all_store_ids)),
            CatalogPhoto.product_key.in_(list(all_check_keys)),
        )
        .group_by(CatalogPhoto.store_id, CatalogPhoto.product_key)
    )).all()

    # Индекс: (store_id, product_key) -> count
    db_counts: dict[tuple[str, str], int] = {(sid, pk): cnt for sid, pk, cnt in rows}

    # Маппинг обратно на frontend-ключи (с fallback на no-color)
    result: dict[str, int] = {}
    for front_key, (pk, pk_nc, sid) in lookup.items():
        cnt = db_counts.get((sid, pk), 0)
        if cnt == 0 and pk != pk_nc:
            cnt = db_counts.get((sid, pk_nc), 0)
        result[front_key] = cnt

    return {"counts": result}


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
