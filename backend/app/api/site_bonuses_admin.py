"""Админ-роутер: бонусные программы магазина (site_bonuses).

Используется React-админкой CRM (раздел «Магазин»).
Публичный роутер для сайтов-витрин — в sites.py, не трогать.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.access import can_modify_site_bonus, can_view_site_bonus
from app.api.auth import require_active
from app.core.database import get_db
from app.models.business import SiteBonus, User

router = APIRouter()


# ── Схемы ────────────────────────────────────────────────────────────────────


class SiteBonusOut(BaseModel):
    id: str
    store_id: str
    name: str
    description: Optional[str]
    rule_type: str
    accrual_percent: Optional[float]
    accrual_fixed: Optional[float]
    redemption_rate: Optional[float]
    min_balance_to_use: Optional[float]
    max_percent_of_order: Optional[float]
    expires_days: Optional[int]
    is_active: bool
    created_at: str
    updated_at: str


class SiteBonusCreate(BaseModel):
    store_id: Optional[str] = None  # admin может указать явно; staff берёт свой
    name: str
    description: Optional[str] = None
    rule_type: str  # cashback | accrual | signup | referral
    accrual_percent: Optional[float] = None
    accrual_fixed: Optional[float] = None
    redemption_rate: Optional[float] = None
    min_balance_to_use: Optional[float] = None
    max_percent_of_order: Optional[float] = None
    expires_days: Optional[int] = None
    is_active: bool = True


class SiteBonusUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    rule_type: Optional[str] = None
    accrual_percent: Optional[float] = None
    accrual_fixed: Optional[float] = None
    redemption_rate: Optional[float] = None
    min_balance_to_use: Optional[float] = None
    max_percent_of_order: Optional[float] = None
    expires_days: Optional[int] = None
    is_active: Optional[bool] = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _bonus_out(b: SiteBonus) -> SiteBonusOut:
    return SiteBonusOut(
        id=b.id,
        store_id=b.store_id,
        name=b.name,
        description=b.description,
        rule_type=b.rule_type,
        accrual_percent=float(b.accrual_percent) if b.accrual_percent else None,
        accrual_fixed=float(b.accrual_fixed) if b.accrual_fixed else None,
        redemption_rate=float(b.redemption_rate) if b.redemption_rate else None,
        min_balance_to_use=float(b.min_balance_to_use) if b.min_balance_to_use else None,
        max_percent_of_order=float(b.max_percent_of_order) if b.max_percent_of_order else None,
        expires_days=b.expires_days,
        is_active=b.is_active,
        created_at=b.created_at.isoformat(),
        updated_at=b.updated_at.isoformat(),
    )


_VALID_RULE_TYPES = {"cashback", "accrual", "signup", "referral"}


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/")
async def list_bonuses(
    store_id: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Список бонусных программ. Staff — только свой магазин. Admin — все."""
    query = select(SiteBonus)

    if current_user.role == "staff":
        query = query.where(SiteBonus.store_id == current_user.store_id)
    elif store_id:
        query = query.where(SiteBonus.store_id == store_id)

    if is_active is not None:
        query = query.where(SiteBonus.is_active == is_active)

    query = query.order_by(SiteBonus.created_at.desc())
    rows = (await db.execute(query)).scalars().all()
    return {"items": [_bonus_out(b) for b in rows], "total": len(rows)}


@router.get("/{bonus_id}", response_model=SiteBonusOut)
async def get_bonus(
    bonus_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    bonus = await db.get(SiteBonus, bonus_id)
    if not bonus:
        raise HTTPException(status_code=404, detail="Бонусная программа не найдена")
    if not can_view_site_bonus(current_user, bonus):
        raise HTTPException(status_code=403, detail="Нет доступа к бонусной программе")
    return _bonus_out(bonus)


@router.post("/")
async def create_bonus(
    body: SiteBonusCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Роль «Инфо» не может создавать бонусные программы")

    if current_user.role == "staff":
        effective_store_id = current_user.store_id
    else:
        if not body.store_id:
            raise HTTPException(status_code=400, detail="store_id обязателен для администратора")
        effective_store_id = body.store_id

    if body.rule_type not in _VALID_RULE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый rule_type. Допустимые: {_VALID_RULE_TYPES}",
        )

    bonus = SiteBonus(
        store_id=effective_store_id,
        name=body.name,
        description=body.description,
        rule_type=body.rule_type,
        accrual_percent=body.accrual_percent,
        accrual_fixed=body.accrual_fixed,
        redemption_rate=body.redemption_rate,
        min_balance_to_use=body.min_balance_to_use,
        max_percent_of_order=body.max_percent_of_order,
        expires_days=body.expires_days,
        is_active=body.is_active,
    )
    db.add(bonus)
    await db.commit()
    await db.refresh(bonus)
    return {"status": "created", "id": bonus.id}


@router.patch("/{bonus_id}")
async def update_bonus(
    bonus_id: str,
    body: SiteBonusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    bonus = await db.get(SiteBonus, bonus_id)
    if not bonus:
        raise HTTPException(status_code=404, detail="Бонусная программа не найдена")
    if not can_modify_site_bonus(current_user, bonus):
        raise HTTPException(status_code=403, detail="Нет доступа к редактированию бонусной программы")

    if body.rule_type is not None and body.rule_type not in _VALID_RULE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый rule_type. Допустимые: {_VALID_RULE_TYPES}",
        )

    if body.name is not None:
        bonus.name = body.name
    if body.description is not None:
        bonus.description = body.description or None
    if body.rule_type is not None:
        bonus.rule_type = body.rule_type
    if body.accrual_percent is not None:
        bonus.accrual_percent = body.accrual_percent
    if body.accrual_fixed is not None:
        bonus.accrual_fixed = body.accrual_fixed
    if body.redemption_rate is not None:
        bonus.redemption_rate = body.redemption_rate
    if body.min_balance_to_use is not None:
        bonus.min_balance_to_use = body.min_balance_to_use
    if body.max_percent_of_order is not None:
        bonus.max_percent_of_order = body.max_percent_of_order
    if body.expires_days is not None:
        bonus.expires_days = body.expires_days
    if body.is_active is not None:
        bonus.is_active = body.is_active

    bonus.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "updated", "id": bonus.id}


@router.delete("/{bonus_id}")
async def delete_bonus(
    bonus_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Soft-delete: is_active=False."""
    bonus = await db.get(SiteBonus, bonus_id)
    if not bonus:
        raise HTTPException(status_code=404, detail="Бонусная программа не найдена")
    if not can_modify_site_bonus(current_user, bonus):
        raise HTTPException(status_code=403, detail="Нет доступа к удалению бонусной программы")

    bonus.is_active = False
    bonus.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "deactivated", "id": bonus.id}
