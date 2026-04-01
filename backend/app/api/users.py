"""
Управление пользователями (только администраторы).
У каждого пользователя ровно одно поле role — одна роль из: admin, staff, info.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import pwd_ctx, require_admin
from app.core.database import get_db
from app.models.business import Store, User

router = APIRouter()

ROLE_VALUES = frozenset({"admin", "staff", "info"})


class UserOut(BaseModel):
    id: str
    username: str
    full_name: Optional[str] = None
    role: str
    store_id: Optional[str] = None
    store_name: Optional[str] = None
    is_active: bool
    must_change_password: bool
    created_at: str


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = None
    role: str = Field(..., description="Одна роль: admin | staff | info")
    store_id: Optional[str] = Field(
        None,
        description="Магазин только для staff; admin и info без привязки к магазину",
    )


class UserUpdate(BaseModel):
    username: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = Field(None, description="Одна роль; нельзя назначить несколько ролей")
    store_id: Optional[str] = None
    is_active: Optional[bool] = None


class PasswordResetBody(BaseModel):
    new_password: str = Field(..., min_length=8)


@router.get("/", response_model=dict)
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    result = await db.execute(
        select(User, Store.name.label("store_name"))
        .outerjoin(Store, User.store_id == Store.id)
        .order_by(User.username)
    )
    items = []
    for u, store_name in result.all():
        no_store = u.role in ("admin", "info")
        items.append(
            UserOut(
                id=str(u.id),
                username=u.username,
                full_name=u.full_name,
                role=u.role,
                store_id=None if no_store else (str(u.store_id) if u.store_id else None),
                store_name=None if no_store else store_name,
                is_active=u.is_active,
                must_change_password=u.must_change_password,
                created_at=u.created_at.isoformat(),
            ).model_dump()
        )
    return {"items": items, "viewer_id": str(current_admin.id)}


@router.post("/", response_model=dict)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    if body.role not in ROLE_VALUES:
        raise HTTPException(status_code=400, detail=f"Роль должна быть одной из: {', '.join(sorted(ROLE_VALUES))}")
    if body.role == "staff":
        if not body.store_id:
            raise HTTPException(status_code=400, detail="Для сотрудника нужно указать магазин")
        st = await db.get(Store, body.store_id)
        if not st:
            raise HTTPException(status_code=400, detail="Магазин не найден")
        store_id_val = body.store_id
    else:
        # admin, info — без привязки к магазину
        store_id_val = None

    exists = (await db.execute(select(User.id).where(User.username == body.username))).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail="Такой логин уже есть")

    user = User(
        username=body.username.strip(),
        password_hash=pwd_ctx.hash(body.password),
        full_name=(body.full_name or "").strip() or None,
        role=body.role,
        store_id=store_id_val,
        must_change_password=True,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    store_name = None
    if user.store_id:
        s = await db.get(Store, user.store_id)
        store_name = s.name if s else None
    return {
        "id": str(user.id),
        "username": user.username,
        "message": "Пользователь создан",
        "store_name": store_name,
    }


@router.patch("/{user_id}", response_model=dict)
async def update_user(
    user_id: str,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    if user_id == current_admin.id and body.is_active is False:
        raise HTTPException(status_code=400, detail="Нельзя деактивировать себя")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if body.username is not None:
        new_username = body.username.strip()
        if not new_username or len(new_username) < 3:
            raise HTTPException(status_code=400, detail="Логин должен быть не менее 3 символов")
        if new_username != user.username:
            existing = (await db.execute(
                select(User.id).where(User.username == new_username, User.id != user.id)
            )).scalar_one_or_none()
            if existing:
                raise HTTPException(status_code=400, detail="Этот логин уже занят")
            user.username = new_username
    if body.full_name is not None:
        user.full_name = body.full_name.strip() or None
    if body.is_active is not None:
        user.is_active = body.is_active

    if body.role is not None:
        if body.role not in ROLE_VALUES:
            raise HTTPException(status_code=400, detail="Недопустимая роль")
        user.role = body.role

    if body.store_id is not None and user.role not in ("admin", "info"):
        if body.store_id == "":
            user.store_id = None
        else:
            st = await db.get(Store, body.store_id)
            if not st:
                raise HTTPException(status_code=400, detail="Магазин не найден")
            user.store_id = body.store_id

    if user.role in ("admin", "info"):
        user.store_id = None

    role = user.role
    sid = str(user.store_id) if user.store_id else None
    if role == "staff" and not sid:
        raise HTTPException(status_code=400, detail="Для сотрудника нужно указать магазин")

    await db.commit()
    return {"status": "ok", "id": user_id}


@router.post("/{user_id}/password", response_model=dict)
async def reset_user_password(
    user_id: str,
    body: PasswordResetBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            password_hash=pwd_ctx.hash(body.new_password),
            must_change_password=True,
        )
    )
    await db.commit()
    return {"status": "ok", "message": "Пароль задан, пользователь должен сменить его при входе"}


@router.delete("/{user_id}", response_model=dict)
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    if user_id == current_admin.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить себя")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    await db.delete(user)
    await db.commit()
    return {"status": "deleted", "id": user_id}
