"""Админ-роутер: главная страница сайта-витрины (home_sections + home_cards).

CMS для главной — продавец / админ редактирует hero, highlight, scroller'ы.
Публичный endpoint для сайта — в sites.py (`GET /api/sites/{store_id}/home-blocks`).
"""
import asyncio
import io
import os
import re
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_active
from app.core.config import settings
from app.core.database import get_db
from app.models.business import HomeCard, HomeSection, Store, User

router = APIRouter()

# Допустимые ключи секций — фронт mobileax-next умеет только эти 4 шаблона.
ALLOWED_SECTION_KEYS = {"hero_dual", "highlight_dual", "shop_latest", "discover_scroll"}

# Bg-пресеты для карточек. Меняется в синхроне с mobileax-next /lib/home-presets.ts —
# валидация на бэке защищает от ввода произвольных значений из админки.
ALLOWED_BG_PRESETS = {
    "dark", "light", "black", "apple-blue", "apple-pro-dark",
    "trade-in-blue", "trade-in-orange", "samsung-blue", "samsung-dark",
}

ALLOWED_CTA_COLORS = {"primary", "dark", "gradient-orange", "gradient-blue"}

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


# ── Schemas ──────────────────────────────────────────────────────────────────


class HomeCardOut(BaseModel):
    id: str
    section_id: str
    sort_order: int
    enabled: bool
    eyebrow: Optional[str]
    title: Optional[str]
    subtitle: Optional[str]
    image_path: Optional[str]
    image_url: Optional[str]  # вычисляемое /media/...
    bg_preset: str
    text_dark: bool
    cta_label: Optional[str]
    cta_href: Optional[str]
    cta_color: str


class HomeSectionOut(BaseModel):
    id: str
    store_id: str
    key: str
    title: Optional[str]
    enabled: bool
    sort_order: int
    cards: List[HomeCardOut]


class HomeCardCreate(BaseModel):
    section_id: str
    eyebrow: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    image_path: Optional[str] = None
    bg_preset: str = "dark"
    text_dark: bool = False
    cta_label: Optional[str] = None
    cta_href: Optional[str] = None
    cta_color: str = "primary"


class HomeCardUpdate(BaseModel):
    eyebrow: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    image_path: Optional[str] = None
    bg_preset: Optional[str] = None
    text_dark: Optional[bool] = None
    cta_label: Optional[str] = None
    cta_href: Optional[str] = None
    cta_color: Optional[str] = None
    enabled: Optional[bool] = None


class SortItem(BaseModel):
    id: str
    sort_order: int


class SortPayload(BaseModel):
    items: List[SortItem]


class SectionUpdate(BaseModel):
    title: Optional[str] = None
    enabled: Optional[bool] = None
    sort_order: Optional[int] = None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _media_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if path.startswith(("http://", "https://", "/themes/")):
        return path
    return f"/media/{path.lstrip('/')}"


def _card_out(c: HomeCard) -> HomeCardOut:
    return HomeCardOut(
        id=c.id,
        section_id=c.section_id,
        sort_order=c.sort_order,
        enabled=c.enabled,
        eyebrow=c.eyebrow,
        title=c.title,
        subtitle=c.subtitle,
        image_path=c.image_path,
        image_url=_media_url(c.image_path),
        bg_preset=c.bg_preset,
        text_dark=c.text_dark,
        cta_label=c.cta_label,
        cta_href=c.cta_href,
        cta_color=c.cta_color,
    )


def _check_store_access(user: User, store_id: str) -> None:
    """staff видит только свой магазин; admin — любой; info — read-only (в admin запрещён)."""
    if user.role == "info":
        raise HTTPException(status_code=403, detail="Роль «Инфо» не имеет доступа к настройке главной")
    if user.role != "admin" and user.store_id != store_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому магазину")


def _validate_card_payload(
    bg_preset: Optional[str],
    cta_color: Optional[str],
    cta_label: Optional[str],
    cta_href: Optional[str],
) -> None:
    if bg_preset is not None and bg_preset not in ALLOWED_BG_PRESETS:
        raise HTTPException(status_code=400, detail=f"Недопустимый bg_preset. Доступные: {sorted(ALLOWED_BG_PRESETS)}")
    if cta_color is not None and cta_color not in ALLOWED_CTA_COLORS:
        raise HTTPException(status_code=400, detail=f"Недопустимый cta_color. Доступные: {sorted(ALLOWED_CTA_COLORS)}")
    # CTA label без href — некликабельная кнопка. Разрешаем, продавец может временно убрать href.
    # CTA href без label — точно ошибка (кнопка-призрак).
    if cta_href and not cta_label:
        raise HTTPException(status_code=400, detail="Если задан cta_href, должен быть cta_label")


async def _get_section_or_404(db: AsyncSession, section_id: str) -> HomeSection:
    section = await db.get(HomeSection, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Секция не найдена")
    return section


# ── Sections ─────────────────────────────────────────────────────────────────


@router.get("/sections")
async def list_sections(
    store_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Все секции магазина с карточками. Один запрос — вся CMS-страница."""
    _check_store_access(current_user, store_id)

    sections = (
        (
            await db.execute(
                select(HomeSection)
                .where(HomeSection.store_id == store_id)
                .order_by(HomeSection.sort_order, HomeSection.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not sections:
        return []

    section_ids = [s.id for s in sections]
    cards = (
        (
            await db.execute(
                select(HomeCard)
                .where(HomeCard.section_id.in_(section_ids))
                .order_by(HomeCard.sort_order, HomeCard.created_at)
            )
        )
        .scalars()
        .all()
    )
    cards_by_section: dict[str, list[HomeCard]] = {}
    for c in cards:
        cards_by_section.setdefault(c.section_id, []).append(c)

    return [
        HomeSectionOut(
            id=s.id,
            store_id=s.store_id,
            key=s.key,
            title=s.title,
            enabled=s.enabled,
            sort_order=s.sort_order,
            cards=[_card_out(c) for c in cards_by_section.get(s.id, [])],
        )
        for s in sections
    ]


@router.patch("/sections/{section_id}")
async def update_section(
    section_id: str,
    body: SectionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    section = await _get_section_or_404(db, section_id)
    _check_store_access(current_user, section.store_id)

    if body.title is not None:
        section.title = body.title
    if body.enabled is not None:
        section.enabled = body.enabled
    if body.sort_order is not None:
        section.sort_order = body.sort_order
    await db.commit()
    return {"status": "ok"}


# ── Cards ────────────────────────────────────────────────────────────────────


@router.post("/cards")
async def create_card(
    body: HomeCardCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    section = await _get_section_or_404(db, body.section_id)
    _check_store_access(current_user, section.store_id)

    _validate_card_payload(body.bg_preset, body.cta_color, body.cta_label, body.cta_href)

    # sort_order = max + 1, чтобы новая карточка попадала в конец секции.
    max_sort = (
        await db.execute(
            select(HomeCard.sort_order)
            .where(HomeCard.section_id == body.section_id)
            .order_by(HomeCard.sort_order.desc())
            .limit(1)
        )
    ).scalar() or -1

    card = HomeCard(
        section_id=body.section_id,
        sort_order=max_sort + 1,
        enabled=True,
        eyebrow=body.eyebrow,
        title=body.title,
        subtitle=body.subtitle,
        image_path=body.image_path,
        bg_preset=body.bg_preset,
        text_dark=body.text_dark,
        cta_label=body.cta_label,
        cta_href=body.cta_href,
        cta_color=body.cta_color,
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return _card_out(card)


@router.patch("/cards/{card_id}")
async def update_card(
    card_id: str,
    body: HomeCardUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    card = await db.get(HomeCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    section = await _get_section_or_404(db, card.section_id)
    _check_store_access(current_user, section.store_id)

    _validate_card_payload(
        body.bg_preset,
        body.cta_color,
        body.cta_label if body.cta_label is not None else card.cta_label,
        body.cta_href if body.cta_href is not None else card.cta_href,
    )

    for field in ("eyebrow", "title", "subtitle", "image_path",
                  "bg_preset", "text_dark", "cta_label", "cta_href", "cta_color", "enabled"):
        v = getattr(body, field)
        if v is not None:
            setattr(card, field, v)
    await db.commit()
    await db.refresh(card)
    return _card_out(card)


@router.delete("/cards/{card_id}")
async def delete_card(
    card_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    card = await db.get(HomeCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    section = await _get_section_or_404(db, card.section_id)
    _check_store_access(current_user, section.store_id)
    await db.delete(card)
    await db.commit()
    return {"status": "deleted"}


@router.post("/sections/{section_id}/sort")
async def sort_cards(
    section_id: str,
    body: SortPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Reorder карточек внутри секции. Принимает массив {id, sort_order}."""
    section = await _get_section_or_404(db, section_id)
    _check_store_access(current_user, section.store_id)

    cards_in_section = (
        (await db.execute(select(HomeCard).where(HomeCard.section_id == section_id)))
        .scalars()
        .all()
    )
    by_id = {c.id: c for c in cards_in_section}
    for item in body.items:
        if item.id in by_id:
            by_id[item.id].sort_order = item.sort_order
    await db.commit()
    return {"status": "ok"}


# ── Image upload ─────────────────────────────────────────────────────────────


@router.post("/cards/{card_id}/image")
async def upload_card_image(
    card_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Загрузить картинку карточки. Сохраняет в /media/home/{store_id}/{uuid}.{ext}."""
    card = await db.get(HomeCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Карточка не найдена")
    section = await _get_section_or_404(db, card.section_id)
    _check_store_access(current_user, section.store_id)
    store = await db.get(Store, section.store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Разрешены только изображения: JPEG, PNG, WEBP")

    raw = await file.read()
    max_b = settings.MAX_PHOTO_SIZE_MB * 1024 * 1024
    if len(raw) > max_b:
        raise HTTPException(status_code=400, detail=f"Файл больше {settings.MAX_PHOTO_SIZE_MB} МБ")

    if not re.match(r"^[a-f0-9\-]+$", section.store_id):
        raise HTTPException(status_code=400, detail="Недопустимый store_id")

    media_root = Path(settings.MEDIA_ROOT).resolve()
    rel_dir = f"home/{section.store_id}"
    out_dir = (media_root / rel_dir).resolve()
    # Защита от path traversal: out_dir должен быть строго внутри media_root.
    # `startswith(str(media_root))` без separator-боундари ловится `/media_evil`,
    # поэтому добавляем os.sep — иначе `/media` префиксно матчит `/media_evil`.
    if not str(out_dir).startswith(str(media_root) + os.sep) and str(out_dir) != str(media_root):
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
    card.image_path = rel_path
    await db.commit()
    await db.refresh(card)

    return {
        "image_path": card.image_path,
        "image_url": _media_url(card.image_path),
    }


# ── Presets feed ─────────────────────────────────────────────────────────────


@router.get("/presets")
async def get_presets(current_user: User = Depends(require_active)):
    """Список доступных bg/cta пресетов — для рендера выпадающих списков в админке."""
    return {
        "bg_presets": sorted(ALLOWED_BG_PRESETS),
        "cta_colors": sorted(ALLOWED_CTA_COLORS),
        "section_keys": sorted(ALLOWED_SECTION_KEYS),
    }
