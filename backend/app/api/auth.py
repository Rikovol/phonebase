"""
Авторизация: JWT access + refresh токены, смена пароля при первом входе.

Логика доступа к магазинам (по полю user.role; без дублирующих флагов в JSON):
  staff   → каталог всех магазинов; учётная цена и прибыль в API — только по своему магазину;
             карточка товара, фото, документы закупки и правки — только по товарам своего магазина (user.store_id);
             список можно сузить store=
  info    → каталог по всем магазинам, без привязки к магазину; без учётной/прибыли, без фото и документов закупки, без редактирования
  admin   → полный доступ; не привязан к магазину; управление пользователями и ролями
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.models.business import User, Store
from app.services.import_configured import is_import_source_configured, run_configured_import

logger = logging.getLogger(__name__)

router = APIRouter()
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# ── Схемы ─────────────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    must_change_password: bool
    user: dict

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class RefreshRequest(BaseModel):
    refresh_token: str


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    username: Optional[str] = None


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _make_token(data: dict, expires_delta: timedelta) -> str:
    payload = {**data, "exp": datetime.now(timezone.utc) + expires_delta}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

def make_access_token(user_id: str, role: str) -> str:
    return _make_token(
        {"sub": user_id, "role": role, "type": "access"},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )

def make_refresh_token(user_id: str) -> str:
    return _make_token(
        {"sub": user_id, "type": "refresh"},
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Токен недействителен")

# ── Зависимости для роутеров ──────────────────────────────────────────────────

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Неверный тип токена")

    user = await db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user

async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    return user

async def require_active(user: User = Depends(get_current_user)) -> User:
    """Не пускает если пароль не сменён после первого входа."""
    if user.must_change_password:
        raise HTTPException(
            status_code=403,
            detail="Необходимо сменить временный пароль",
            headers={"X-Must-Change-Password": "true"},
        )
    return user

# ── Фоновая синхронизация 1С после входа ──────────────────────────────────────

async def _sync_1c_after_login(user_id: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            if not await is_import_source_configured(session):
                return
            await run_configured_import(session, user_id, auto_label="авто при входе")
    except Exception:
        logger.exception("Синхронизация выгрузки 1С после входа не выполнена")


# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    background_tasks: BackgroundTasks,
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.username == form.username, User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user or not pwd_ctx.verify(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    # Обновляем время последнего входа
    await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(last_login_at=datetime.now(timezone.utc))
    )
    await db.commit()

    background_tasks.add_task(_sync_1c_after_login, str(user.id))

    # Магазин в ответе только для staff (admin и info не привязаны к точке)
    store_name = None
    store_id_out = None
    if user.role == "staff" and user.store_id:
        store = await db.get(Store, user.store_id)
        store_name = store.name if store else None
        store_id_out = str(user.store_id)

    return TokenResponse(
        access_token=make_access_token(str(user.id), user.role),
        refresh_token=make_refresh_token(str(user.id)),
        must_change_password=user.must_change_password,
        user={
            "id":         str(user.id),
            "username":   user.username,
            "full_name":  user.full_name,
            "role":       user.role,
            "store_id":   store_id_out,
            "store_name": store_name,
        },
    )

@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Неверный тип токена")

    user = await db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    store_name = None
    store_id_out = None
    if user.role == "staff" and user.store_id:
        store = await db.get(Store, user.store_id)
        store_name = store.name if store else None
        store_id_out = str(user.store_id)

    return TokenResponse(
        access_token=make_access_token(str(user.id), user.role),
        refresh_token=make_refresh_token(str(user.id)),
        must_change_password=user.must_change_password,
        user={
            "id": str(user.id), "username": user.username,
            "full_name": user.full_name, "role": user.role,
            "store_id": store_id_out,
            "store_name": store_name,
        },
    )

@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),   # не require_active — можно до смены
):
    if not pwd_ctx.verify(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Текущий пароль неверен")

    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен быть не менее 8 символов")

    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="Новый пароль совпадает со старым")

    await db.execute(
        update(User)
        .where(User.id == current_user.id)
        .values(
            password_hash=pwd_ctx.hash(body.new_password),
            must_change_password=False,
        )
    )
    await db.commit()
    return {"status": "ok", "message": "Пароль изменён"}


async def _user_me_payload(user: User, db: AsyncSession) -> dict:
    store_name = None
    store_id_out = None
    if user.role == "staff" and user.store_id:
        store = await db.get(Store, user.store_id)
        store_name = store.name if store else None
        store_id_out = str(user.store_id)
    return {
        "id":                  str(user.id),
        "username":            user.username,
        "full_name":           user.full_name,
        "role":                user.role,
        "store_id":            store_id_out,
        "store_name":          store_name,
        "must_change_password": user.must_change_password,
        "last_login_at":       user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.get("/me")
async def me(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _user_me_payload(current_user, db)


@router.patch("/me")
async def update_me(
    body: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Смена отображаемого имени и/или логина (своего профиля)."""
    vals = {}
    if body.full_name is not None:
        vals["full_name"] = body.full_name.strip() or None
    if body.username is not None:
        new_username = body.username.strip()
        if not new_username or len(new_username) < 3:
            raise HTTPException(status_code=400, detail="Логин должен быть не менее 3 символов")
        if new_username != current_user.username:
            existing = (await db.execute(
                select(User).where(User.username == new_username, User.id != current_user.id)
            )).scalar_one_or_none()
            if existing:
                raise HTTPException(status_code=400, detail="Этот логин уже занят")
            vals["username"] = new_username
    if vals:
        for k, v in vals.items():
            setattr(current_user, k, v)
        db.add(current_user)
        await db.commit()
        await db.refresh(current_user)
    return await _user_me_payload(current_user, db)
