import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.access import can_modify_product
from app.api.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.business import Product, ProductPhoto, User

router = APIRouter()

ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def _ensure_product_write(product: Product, user: User) -> None:
    if not can_modify_product(user, product):
        raise HTTPException(status_code=403, detail="Нет доступа к редактированию товаров другого магазина")


def _reject_info(user: User) -> None:
    if user.role == "info":
        raise HTTPException(status_code=403, detail="Недоступно для роли «Инфо»")


@router.post("/product/{product_id}")
async def upload_product_photo(
    product_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    _reject_info(current_user)
    _ensure_product_write(product, current_user)
    if product.is_sold and current_user.role != "admin":
        raise HTTPException(status_code=400, detail="Нельзя добавлять фото к проданному товару")

    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Разрешены только изображения: JPEG, PNG, WEBP",
        )

    raw = await file.read()
    max_b = settings.MAX_PHOTO_SIZE_MB * 1024 * 1024
    if len(raw) > max_b:
        raise HTTPException(status_code=400, detail=f"Файл больше {settings.MAX_PHOTO_SIZE_MB} МБ")

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except OSError:
        raise HTTPException(status_code=400, detail="Не удалось прочитать изображение")

    ext = { "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(ct, ".jpg")
    if img.mode in ("RGBA", "P") and ct == "image/jpeg":
        img = img.convert("RGB")

    media_root = Path(settings.MEDIA_ROOT)
    rel_dir = product.store_id.replace("..", "")
    out_dir = media_root / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{ext}"
    out_path = out_dir / fname

    if ct == "image/jpeg":
        rgb = img.convert("RGB") if img.mode in ("RGBA", "P", "LA") else img
        if rgb.mode != "RGB":
            rgb = rgb.convert("RGB")
        rgb.save(out_path, format="JPEG", quality=88, optimize=True)
    elif ct == "image/png":
        img.save(out_path, format="PNG", optimize=True)
    else:
        img.save(out_path, format="WEBP", quality=88, method=4)

    rel_path = f"{rel_dir}/{fname}".replace("\\", "/")

    existing = (
        await db.execute(
            select(func.count()).select_from(ProductPhoto).where(ProductPhoto.product_id == product_id)
        )
    ).scalar() or 0
    is_main = existing == 0

    photo = ProductPhoto(
        product_id=product_id,
        uploaded_by=current_user.id,
        file_path=rel_path,
        file_size=out_path.stat().st_size,
        is_main=is_main,
    )
    db.add(photo)
    await db.commit()
    await db.refresh(photo)

    return {
        "id": photo.id,
        "url": f"/media/{rel_path}",
        "is_main": photo.is_main,
    }


@router.delete("/{photo_id}")
async def delete_product_photo(
    photo_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    photo = await db.get(ProductPhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Фото не найдено")

    product = await db.get(Product, photo.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    _reject_info(current_user)
    _ensure_product_write(product, current_user)
    if product.is_sold and current_user.role != "admin":
        raise HTTPException(status_code=400, detail="Нельзя удалять фото у проданного товара")

    was_main = photo.is_main
    full = Path(settings.MEDIA_ROOT) / photo.file_path

    await db.delete(photo)
    await db.flush()

    if was_main:
        res_other = await db.execute(
            select(ProductPhoto)
            .where(ProductPhoto.product_id == product.id)
            .order_by(ProductPhoto.created_at.asc())
            .limit(1)
        )
        other = res_other.scalars().first()
        if other:
            other.is_main = True

    await db.commit()

    try:
        if full.is_file():
            full.unlink()
    except OSError:
        pass

    return {"status": "deleted", "id": photo_id}
