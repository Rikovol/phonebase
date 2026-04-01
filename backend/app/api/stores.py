from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user, require_admin
from app.core.database import get_db
from app.models.business import Store, User

router = APIRouter()


class StoreOut(BaseModel):
    id: str
    name: str
    city: Optional[str]
    address: Optional[str]
    is_active: bool = True
    avito_phone: Optional[str]
    avito_address: Optional[str]
    avito_manager_name: Optional[str]
    avito_configured: bool = False
    website_url: Optional[str] = None
    website_feed_enabled: bool = False


class StoreUpdate(BaseModel):
    avito_phone: Optional[str] = None
    avito_address: Optional[str] = None
    avito_manager_name: Optional[str] = None
    website_url: Optional[str] = None
    website_feed_enabled: Optional[bool] = None


@router.get("/")
async def list_stores(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    include_inactive: bool = Query(
        False,
        description="Только для admin: вернуть и неактивные магазины (для привязки пользователей и т.п.)",
    ),
):
    """По умолчанию — только активные магазины. С `include_inactive=true` (только admin) — все."""
    admin = current_user.role == "admin"
    if not admin:
        include_inactive = False
    if include_inactive:
        result = await db.execute(select(Store).order_by(Store.is_active.desc(), Store.name))
    else:
        result = await db.execute(select(Store).where(Store.is_active == True).order_by(Store.name))  # noqa: E712
    stores = result.scalars().all()
    return {
        "items": [
            StoreOut(
                id=s.id,
                name=s.name,
                city=s.city,
                address=s.address,
                is_active=s.is_active,
                avito_phone=s.avito_phone if admin else None,
                avito_address=s.avito_address,
                avito_manager_name=s.avito_manager_name,
                avito_configured=bool(s.avito_client_id),
                website_url=s.website_url,
                website_feed_enabled=s.website_feed_enabled,
            )
            for s in stores
        ]
    }



@router.patch("/{store_id}")
async def update_store_avito(
    store_id: str,
    body: StoreUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    if body.avito_phone is not None:
        store.avito_phone = body.avito_phone
    if body.avito_address is not None:
        store.avito_address = body.avito_address
    if body.avito_manager_name is not None:
        store.avito_manager_name = body.avito_manager_name
    if body.website_url is not None:
        store.website_url = body.website_url.strip() or None
    if body.website_feed_enabled is not None:
        store.website_feed_enabled = body.website_feed_enabled

    await db.commit()
    await db.refresh(store)
    return StoreOut(
        id=store.id,
        name=store.name,
        city=store.city,
        address=store.address,
        is_active=store.is_active,
        avito_phone=store.avito_phone,
        avito_address=store.avito_address,
        avito_manager_name=store.avito_manager_name,
        avito_configured=bool(store.avito_client_id),
        website_url=store.website_url,
        website_feed_enabled=store.website_feed_enabled,
    )
