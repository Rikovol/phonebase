"""
OAuth-авторизация посетителей сайтов-витрин (VK ID + Telegram Login Widget).

Эндпоинты (все под префиксом /api/sites/{store_id}/auth/):
  GET  vk/start            — PKCE-редирект на id.vk.com
  GET  vk/callback         — обмен code, upsert SiteVisitor, выдача cookie
  POST telegram/callback   — верификация HMAC, upsert SiteVisitor, выдача cookie
  POST logout              — удаление cookie site_session
  GET  me                  — текущий авторизованный visitor

JWT-cookie site_session: HttpOnly, SameSite=Lax, aud="site_visitor" (изолирован от CRM-JWT).
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from jose import jwt
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sites import get_active_store, get_site_visitor, require_site_visitor
from app.core.config import settings
from app.core.database import get_db
from app.models.business import SiteVisitor, Store

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Константы ─────────────────────────────────────────────────────────────────

COOKIE_SESSION = "site_session"
COOKIE_VK_STATE = "vk_oauth_state"
SITE_VISITOR_AUDIENCE = "site_visitor"
VK_AUTHORIZE_URL = "https://id.vk.com/authorize"
VK_TOKEN_URL = "https://id.vk.com/oauth2/auth"
VK_USERINFO_URL = "https://id.vk.com/oauth2/user_info"

# ── Pydantic-схемы ────────────────────────────────────────────────────────────


class TelegramAuthIn(BaseModel):
    """Поля, которые Telegram Login Widget передаёт в callback."""

    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str

    @field_validator("auth_date")
    @classmethod
    def check_auth_date_freshness(cls, v: int) -> int:
        """Блокируем устаревшие данные старше 24 часов."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if now_ts - v > 86400:
            raise ValueError("auth_date устарел: данные старше 24 часов")
        return v


class AuthCallbackOut(BaseModel):
    """Ответ на успешный OAuth-callback."""

    id: str
    display_name: Optional[str]
    is_new: bool


class VisitorMeOut(BaseModel):
    """Текущий авторизованный посетитель сайта."""

    id: str
    store_id: str
    display_name: Optional[str]
    avatar_url: Optional[str]
    auth_provider: Optional[str]
    total_messages_count: int
    first_seen_at: datetime
    last_seen_at: datetime


# ── Внутренние helper-функции ─────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_site_jwt(
    visitor: SiteVisitor,
    store_id: str,
    provider: str,
    provider_user_id: str,
) -> str:
    """Генерирует JWT site_session с аудиторией site_visitor (изолирован от CRM)."""
    session_days = getattr(settings, "SITE_SESSION_DAYS", 30)
    exp = _now() + timedelta(days=session_days)
    payload = {
        "sub": visitor.id,
        "aud": SITE_VISITOR_AUDIENCE,
        "store_id": store_id,
        "provider": provider,
        "provider_user_id": provider_user_id,
        "exp": exp,
        "iat": _now(),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def _set_session_cookie(response: Response, token: str) -> None:
    """Устанавливает HttpOnly cookie site_session."""
    session_days = getattr(settings, "SITE_SESSION_DAYS", 30)
    cookie_domain = getattr(settings, "SITE_SESSION_COOKIE_DOMAIN", None)
    is_prod = getattr(settings, "ENVIRONMENT", "development") == "production"

    kwargs: dict = {
        "key": COOKIE_SESSION,
        "value": token,
        "httponly": True,
        "samesite": "lax",
        "max_age": session_days * 86400,
        "path": "/api/sites",
    }
    if is_prod:
        kwargs["secure"] = True
    if cookie_domain:
        kwargs["domain"] = cookie_domain

    response.set_cookie(**kwargs)


def _delete_session_cookie(response: Response) -> None:
    """Удаляет cookie site_session."""
    response.delete_cookie(
        key=COOKIE_SESSION,
        path="/api/sites",
    )


def _verify_telegram_hash(data: dict, bot_token: str) -> bool:
    """
    Проверяет HMAC-подпись от Telegram Login Widget.

    Алгоритм по документации Telegram:
      data_check_string = "\n".join("key=value") — все поля кроме hash, отсортированные
      secret_key = SHA256(bot_token)
      expected = HMAC-SHA256(secret_key, data_check_string).hexdigest()
    """
    received_hash = data.get("hash", "")
    # Строим data_check_string из всех полей кроме hash
    fields = {k: str(v) for k, v in data.items() if k != "hash" and v is not None}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))

    secret_key = hashlib.sha256(bot_token.encode()).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    return hmac.compare_digest(expected_hash, received_hash)


def _build_vk_state(store_id: str) -> str:
    """Генерирует CSRF-state через HMAC(store_id + nonce + now, SECRET_KEY)."""
    nonce = secrets.token_hex(16)
    ts = str(int(_now().timestamp()))
    message = f"{store_id}:{nonce}:{ts}"
    sig = hmac.new(
        settings.SECRET_KEY.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    # Кодируем state как JSON-base64 чтобы можно было восстановить message для сверки через cookie
    payload = {"m": message, "s": sig}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _verify_vk_state(state_from_url: str, state_from_cookie: str) -> bool:
    """
    Сверяет state из redirect URL с cookie и проверяет свежесть timestamp.
    Формат state: base64url({"m": "store_id:nonce:ts", "s": sig})
    """
    if not hmac.compare_digest(state_from_url, state_from_cookie):
        return False
    # Проверка свежести timestamp (не старше 600 секунд)
    try:
        padding = 4 - len(state_from_url) % 4
        padded = state_from_url + ("=" * padding if padding != 4 else "")
        payload = json.loads(base64.urlsafe_b64decode(padded))
        # Формат message: "store_id:nonce:ts"
        ts = int(payload["m"].split(":")[-1])
        if time.time() - ts > 600:
            return False
    except Exception:
        return False
    return True


# ── Эндпоинты ─────────────────────────────────────────────────────────────────


@router.get("/{store_id}/auth/vk/start")
async def vk_auth_start(
    store: Store = Depends(get_active_store),
) -> RedirectResponse:
    """
    Шаг 1 VK ID OAuth: генерируем PKCE code_challenge + state,
    сохраняем verifier+state в cookie, редиректим на id.vk.com.
    """
    vk_app_id = getattr(settings, "VK_APP_ID", None)
    vk_redirect_uri = getattr(settings, "VK_REDIRECT_URI", None)

    if not vk_app_id:
        raise HTTPException(status_code=503, detail="VK_APP_ID не настроен в конфигурации")
    if not vk_redirect_uri:
        raise HTTPException(status_code=503, detail="VK_REDIRECT_URI не настроен в конфигурации")

    # PKCE S256: обязателен для VK ID с 2024 года
    code_verifier = secrets.token_urlsafe(64)  # 86 chars — в диапазоне 43-128
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    # CSRF state
    state = _build_vk_state(store.id)

    # Сохраняем verifier и state в HttpOnly cookie (10 минут)
    oauth_state_payload = json.dumps({"verifier": code_verifier, "state": state})

    # Формируем URL авторизации
    params = {
        "response_type": "code",
        "client_id": vk_app_id,
        "redirect_uri": vk_redirect_uri,
        "scope": "vkid.personal_info email phone",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    authorize_url = f"{VK_AUTHORIZE_URL}?{query}"

    response = RedirectResponse(url=authorize_url, status_code=302)

    is_prod = getattr(settings, "ENVIRONMENT", "development") == "production"
    cookie_kwargs: dict = {
        "key": COOKIE_VK_STATE,
        "value": oauth_state_payload,
        "httponly": True,
        "samesite": "lax",
        "max_age": 600,  # 10 минут на завершение OAuth flow
        "path": "/api/sites",
    }
    if is_prod:
        cookie_kwargs["secure"] = True

    response.set_cookie(**cookie_kwargs)
    return response


@router.get("/{store_id}/auth/vk/callback")
async def vk_auth_callback(
    request: Request,
    code: str,
    state: str,
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """
    Шаг 2 VK ID OAuth: проверяем state (CSRF), обмениваем code на token,
    получаем user_info, делаем upsert SiteVisitor, выдаём site_session cookie.
    """
    vk_app_id = getattr(settings, "VK_APP_ID", None)
    vk_app_secret = getattr(settings, "VK_APP_SECRET", None)
    vk_redirect_uri = getattr(settings, "VK_REDIRECT_URI", None)
    site_return_url = getattr(settings, "SITE_RETURN_URL", "http://localhost:5174")

    if not vk_app_id or not vk_app_secret:
        raise HTTPException(status_code=503, detail="VK OAuth не настроен")

    # Проверяем state против cookie (защита от CSRF)
    raw_cookie = request.cookies.get(COOKIE_VK_STATE)
    if not raw_cookie:
        raise HTTPException(status_code=403, detail="OAuth state cookie отсутствует")

    try:
        cookie_data = json.loads(raw_cookie)
        cookie_state = cookie_data.get("state", "")
        code_verifier = cookie_data.get("verifier", "")
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(status_code=403, detail="Повреждённый OAuth state cookie")

    if not _verify_vk_state(state, cookie_state):
        raise HTTPException(status_code=403, detail="Несовпадение OAuth state (возможная CSRF-атака)")

    # Обмен code → access_token через POST https://id.vk.com/oauth2/auth
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_resp = await client.post(
            VK_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": vk_app_id,
                "client_secret": vk_app_secret,
                "redirect_uri": vk_redirect_uri,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if token_resp.status_code != 200:
        try:
            error_data = token_resp.json() if token_resp.headers.get("content-type", "").startswith("application/json") else {}
        except Exception:
            error_data = {}
        logger.error(
            "VK token exchange failed: status=%s error=%s",
            token_resp.status_code, error_data.get("error", "unknown"),
        )
        raise HTTPException(status_code=502, detail="Ошибка обмена кода VK на токен")

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="VK не вернул access_token")

    # Получаем user_info (надёжнее чем парсить id_token без JWKS)
    async with httpx.AsyncClient(timeout=10.0) as client:
        info_resp = await client.get(
            VK_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if info_resp.status_code != 200:
        try:
            info_error_data = info_resp.json() if info_resp.headers.get("content-type", "").startswith("application/json") else {}
        except Exception:
            info_error_data = {}
        logger.error(
            "VK user_info failed: status=%s error=%s",
            info_resp.status_code, info_error_data.get("error", "unknown"),
        )
        raise HTTPException(status_code=502, detail="Ошибка получения профиля VK")

    user_info = info_resp.json()
    vk_user_id = str(user_info.get("sub") or user_info.get("user_id", ""))
    if not vk_user_id:
        raise HTTPException(status_code=502, detail="VK не вернул user_id в профиле")

    given_name = user_info.get("given_name") or user_info.get("first_name", "")
    family_name = user_info.get("family_name") or user_info.get("last_name", "")
    display_name = f"{given_name} {family_name}".strip() or None
    avatar_url = user_info.get("picture") or user_info.get("avatar") or None

    # Upsert SiteVisitor по UNIQUE (store_id, 'vk', vk_user_id)
    visitor, is_new = await _upsert_visitor(
        db=db,
        store_id=store.id,
        provider="vk",
        provider_user_id=vk_user_id,
        display_name=display_name,
        avatar_url=avatar_url,
    )

    # Выдаём JWT cookie site_session
    token = _make_site_jwt(visitor, store.id, "vk", vk_user_id)
    response = RedirectResponse(url=site_return_url, status_code=302)
    _set_session_cookie(response, token)

    # Удаляем временный OAuth state cookie
    response.delete_cookie(key=COOKIE_VK_STATE, path="/api/sites")

    logger.info(
        "VK auth success: visitor=%s store=%s is_new=%s",
        visitor.id, store.id, is_new,
    )
    return response


@router.post("/{store_id}/auth/telegram/callback", response_model=AuthCallbackOut)
async def telegram_auth_callback(
    body: TelegramAuthIn,
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    Telegram Login Widget callback.
    Проверяет HMAC-SHA256 подпись, делает upsert SiteVisitor, выдаёт cookie.
    """
    bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
    if not bot_token:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN не настроен в конфигурации")

    # Собираем словарь для верификации (все поля кроме hash)
    data_dict: dict = {
        "id": str(body.id),
        "first_name": body.first_name,
        "auth_date": str(body.auth_date),
    }
    if body.last_name:
        data_dict["last_name"] = body.last_name
    if body.username:
        data_dict["username"] = body.username
    if body.photo_url:
        data_dict["photo_url"] = body.photo_url
    # hash добавляем для передачи в верификатор
    data_dict["hash"] = body.hash

    if not _verify_telegram_hash(data_dict, bot_token):
        raise HTTPException(status_code=403, detail="Неверная подпись Telegram (hash mismatch)")

    # Имя для отображения
    display_name = f"{body.first_name} {body.last_name or ''}".strip() or None

    # Upsert SiteVisitor по UNIQUE (store_id, 'telegram', telegram_id)
    visitor, is_new = await _upsert_visitor(
        db=db,
        store_id=store.id,
        provider="telegram",
        provider_user_id=str(body.id),
        display_name=display_name,
        avatar_url=body.photo_url,
    )

    # Выдаём JWT cookie site_session
    token = _make_site_jwt(visitor, store.id, "telegram", str(body.id))

    out = AuthCallbackOut(
        id=visitor.id,
        display_name=visitor.display_name,
        is_new=is_new,
    )
    response = JSONResponse(content=out.model_dump())
    _set_session_cookie(response, token)

    logger.info(
        "Telegram auth success: visitor=%s store=%s is_new=%s",
        visitor.id, store.id, is_new,
    )
    return response


@router.post("/{store_id}/auth/logout", status_code=204)
async def site_logout(
    store: Store = Depends(get_active_store),
) -> Response:
    """Удаляет cookie site_session — выход из сессии посетителя."""
    response = Response(status_code=204)
    _delete_session_cookie(response)
    return response


@router.get("/{store_id}/auth/me", response_model=VisitorMeOut)
async def site_me(
    visitor: SiteVisitor = Depends(require_site_visitor),
) -> VisitorMeOut:
    """Возвращает данные текущего авторизованного посетителя."""
    return VisitorMeOut(
        id=visitor.id,
        store_id=visitor.store_id,
        display_name=visitor.display_name,
        avatar_url=visitor.avatar_url,
        auth_provider=visitor.auth_provider,
        total_messages_count=visitor.total_messages_count,
        first_seen_at=visitor.first_seen_at,
        last_seen_at=visitor.last_seen_at,
    )


# ── Внутренние функции работы с БД ────────────────────────────────────────────


async def _upsert_visitor(
    db: AsyncSession,
    store_id: str,
    provider: str,
    provider_user_id: str,
    display_name: Optional[str],
    avatar_url: Optional[str],
    contact_email: Optional[str] = None,
    contact_phone: Optional[str] = None,
) -> tuple[SiteVisitor, bool]:
    """
    Атомарный upsert SiteVisitor через PostgreSQL ON CONFLICT DO UPDATE.

    Возвращает (visitor, is_new):
      is_new=True  — создан новый visitor (первый вход)
      is_new=False — существующий visitor, обновлён last_seen_at / display_name / avatar_url
    """
    now = _now()
    stmt = pg_insert(SiteVisitor).values(
        store_id=store_id,
        auth_provider=provider,
        auth_provider_user_id=provider_user_id,
        display_name=display_name,
        avatar_url=avatar_url,
        contact_email=contact_email,
        contact_phone=contact_phone,
        first_seen_at=now,
        last_seen_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["store_id", "auth_provider", "auth_provider_user_id"],
        set_={
            "display_name": stmt.excluded.display_name,
            "avatar_url": stmt.excluded.avatar_url,
            "last_seen_at": now,
        },
    ).returning(SiteVisitor)
    result = await db.execute(stmt)
    visitor = result.scalar_one()
    # is_new: если first_seen_at == last_seen_at — запись только что создана
    is_new = visitor.first_seen_at == visitor.last_seen_at
    await db.commit()
    return visitor, is_new
