"""Админ-роутер: заявки с сайта (site_messages).

Используется React-админкой CRM (раздел «Магазин»).
Публичный роутер для сайтов-витрин — в sites.py, не трогать.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.access import can_modify_site_message, can_view_site_message
from app.api.auth import require_active
from app.core.database import get_db
from app.models.business import AvitoMessage, SiteMessage, SiteVisitor, User

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Схемы ────────────────────────────────────────────────────────────────────


class SiteMessageOut(BaseModel):
    id: str
    store_id: str
    visitor_id: Optional[str]
    message_type: str
    status: str
    is_verified: bool
    auth_provider: Optional[str]
    contact_name: Optional[str]
    contact_phone: Optional[str]
    contact_email: Optional[str]
    preferred_channel: Optional[str]
    tradein_brand: Optional[str]
    tradein_model: Optional[str]
    tradein_storage: Optional[str]
    tradein_color: Optional[str]
    tradein_condition: Optional[str]
    tradein_battery_pct: Optional[int]
    tradein_completeness: Optional[str]
    tradein_estimated_price: Optional[float]
    subject: Optional[str]
    body: Optional[str]
    assigned_to: Optional[str]
    notes: Optional[str]
    answered_at: Optional[str]
    answered_by: Optional[str]
    last_reply_text: Optional[str]
    created_at: str
    updated_at: str
    closed_at: Optional[str]


class SiteMessageDetailOut(SiteMessageOut):
    visitor_total_messages_count: Optional[int]
    visitor_first_seen_at: Optional[str]
    visitor_display_name: Optional[str]
    visitor_is_blocked: Optional[bool]


class SiteMessageUpdate(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    notes: Optional[str] = None


class SiteMessageReply(BaseModel):
    reply_text: str
    channel_override: Optional[str] = None  # telegram | max | email | phone


# ── Helpers ───────────────────────────────────────────────────────────────────


def _message_out(msg: SiteMessage) -> SiteMessageOut:
    return SiteMessageOut(
        id=msg.id,
        store_id=msg.store_id,
        visitor_id=msg.visitor_id,
        message_type=msg.message_type,
        status=msg.status,
        is_verified=msg.is_verified,
        auth_provider=msg.auth_provider,
        contact_name=msg.contact_name,
        contact_phone=msg.contact_phone,
        contact_email=msg.contact_email,
        preferred_channel=msg.preferred_channel,
        tradein_brand=msg.tradein_brand,
        tradein_model=msg.tradein_model,
        tradein_storage=msg.tradein_storage,
        tradein_color=msg.tradein_color,
        tradein_condition=msg.tradein_condition,
        tradein_battery_pct=msg.tradein_battery_pct,
        tradein_completeness=msg.tradein_completeness,
        tradein_estimated_price=float(msg.tradein_estimated_price) if msg.tradein_estimated_price else None,
        subject=msg.subject,
        body=msg.body,
        assigned_to=msg.assigned_to,
        notes=msg.notes,
        answered_at=msg.answered_at.isoformat() if msg.answered_at else None,
        answered_by=msg.answered_by,
        last_reply_text=msg.last_reply_text,
        created_at=msg.created_at.isoformat(),
        updated_at=msg.updated_at.isoformat(),
        closed_at=msg.closed_at.isoformat() if msg.closed_at else None,
    )


_VALID_STATUSES = {"new", "in_progress", "answered", "closed", "spam"}


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/stats")
async def get_stats(
    store_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Счётчики непрочитанных, всего сегодня, avg время ответа."""
    # Определяем store_id для фильтра
    effective_store_id = _effective_store_id(current_user, store_id)

    base = select(SiteMessage)
    if effective_store_id:
        base = base.where(SiteMessage.store_id == effective_store_id)

    # Непрочитанные по типу (answered_at IS NULL, статус не closed/spam)
    unread_q = base.where(
        SiteMessage.answered_at.is_(None),
        SiteMessage.status.not_in(["closed", "spam"]),
    )
    unread_rows = (await db.execute(
        select(SiteMessage.message_type, func.count(SiteMessage.id).label("cnt"))
        .where(
            SiteMessage.answered_at.is_(None),
            SiteMessage.status.not_in(["closed", "spam"]),
            *([SiteMessage.store_id == effective_store_id] if effective_store_id else []),
        )
        .group_by(SiteMessage.message_type)
    )).all()
    unread_count_per_type = {row.message_type: row.cnt for row in unread_rows}

    # Всего сегодня
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total_today_q = select(func.count(SiteMessage.id)).where(
        SiteMessage.created_at >= today_start,
        *([SiteMessage.store_id == effective_store_id] if effective_store_id else []),
    )
    total_today = (await db.execute(total_today_q)).scalar() or 0

    # Среднее время ответа (часы) — только для отвеченных
    avg_q = select(
        func.avg(
            func.extract("epoch", SiteMessage.answered_at - SiteMessage.created_at) / 3600
        )
    ).where(
        SiteMessage.answered_at.isnot(None),
        *([SiteMessage.store_id == effective_store_id] if effective_store_id else []),
    )
    avg_response_time_hours = (await db.execute(avg_q)).scalar()

    return {
        "unread_count_per_type": unread_count_per_type,
        "total_today": total_today,
        "avg_response_time_hours": round(float(avg_response_time_hours), 2) if avg_response_time_hours else None,
    }


@router.get("/")
async def list_messages(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    message_type: Optional[List[str]] = Query(
        None,
        description="Один тип или несколько (повторить ?message_type=order&message_type=tradein)",
    ),
    unread_only: bool = Query(False),
    from_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    effective_store_id = _effective_store_id(current_user, store_id)

    query = select(SiteMessage)
    if effective_store_id:
        query = query.where(SiteMessage.store_id == effective_store_id)
    if status:
        query = query.where(SiteMessage.status == status)
    if message_type:
        query = query.where(SiteMessage.message_type.in_(message_type))
    if unread_only:
        query = query.where(
            SiteMessage.answered_at.is_(None),
            SiteMessage.status.not_in(["closed", "spam"]),
        )
    if from_date:
        dt = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        query = query.where(SiteMessage.created_at >= dt)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(SiteMessage.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    rows = (await db.execute(query)).scalars().all()

    return {
        "items": [_message_out(m) for m in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/{message_id}", response_model=SiteMessageDetailOut)
async def get_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    msg = await db.get(SiteMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if not can_view_site_message(current_user, msg):
        raise HTTPException(status_code=403, detail="Нет доступа к заявке")

    # Данные о visitor'е (inner join по необходимости)
    visitor_total = None
    visitor_first_seen = None
    visitor_display_name = None
    visitor_is_blocked = None

    if msg.visitor_id:
        visitor = await db.get(SiteVisitor, msg.visitor_id)
        if visitor:
            visitor_total = visitor.total_messages_count
            visitor_first_seen = visitor.first_seen_at.isoformat()
            visitor_display_name = visitor.display_name
            visitor_is_blocked = visitor.is_blocked

    base = _message_out(msg)
    return SiteMessageDetailOut(
        **base.model_dump(),
        visitor_total_messages_count=visitor_total,
        visitor_first_seen_at=visitor_first_seen,
        visitor_display_name=visitor_display_name,
        visitor_is_blocked=visitor_is_blocked,
    )


@router.patch("/{message_id}")
async def update_message(
    message_id: str,
    body: SiteMessageUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    msg = await db.get(SiteMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if not can_modify_site_message(current_user, msg):
        raise HTTPException(status_code=403, detail="Нет доступа к редактированию заявки")

    if body.status is not None:
        if body.status not in _VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Недопустимый статус. Допустимые: {_VALID_STATUSES}")
        msg.status = body.status
        if body.status == "closed" and not msg.closed_at:
            msg.closed_at = datetime.now(timezone.utc)
    if body.assigned_to is not None:
        msg.assigned_to = body.assigned_to or None
    if body.notes is not None:
        msg.notes = body.notes or None

    msg.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "updated", "id": msg.id}


@router.post("/{message_id}/reply")
async def reply_to_message(
    message_id: str,
    body: SiteMessageReply,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    msg = await db.get(SiteMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if not can_modify_site_message(current_user, msg):
        raise HTTPException(status_code=403, detail="Нет доступа к ответу на заявку")

    if not body.reply_text or not body.reply_text.strip():
        raise HTTPException(status_code=400, detail="Текст ответа не может быть пустым")

    now = datetime.now(timezone.utc)
    msg.last_reply_text = body.reply_text.strip()
    msg.answered_at = now
    msg.answered_by = str(current_user.id)
    if msg.status in ("new", "in_progress"):
        msg.status = "answered"
    msg.updated_at = now

    # Отправка по каналу
    channel = body.channel_override or msg.preferred_channel
    if channel in ("telegram", "max", "email"):
        # TODO: реализовать отправку через внешние сервисы (Telegram Bot API / MAX / SMTP).
        # Пока сохраняем ответ локально и логируем намерение отправки.
        logger.info(
            "TODO send reply via channel=%s to message_id=%s visitor_id=%s",
            channel, message_id, msg.visitor_id,
        )
    await db.commit()
    return {"status": "replied", "id": msg.id, "channel": channel}


# ── Внутренние хелперы ────────────────────────────────────────────────────────


def _effective_store_id(user: User, requested_store_id: Optional[str]) -> Optional[str]:
    """Staff видит только свой store_id, admin без фильтра видит все."""
    if user.role == "staff":
        return user.store_id  # игнорируем requested_store_id — безопасность
    if user.role == "admin":
        return requested_store_id  # admin сам выбирает или None=всё
    # info — видит всё, но без записи
    return requested_store_id


# ─── Unified inbox: site_messages + avito_messages в одной ленте ──────────────


def _site_message_kind(message_type: str) -> str:
    """В таб «Заказы» уходит только `order` с сайта.
    Trade-in — это заявка на оценку, не заказ; идёт в «Сообщения» отдельным цветом.
    """
    return "order" if message_type == "order" else "message"


def _site_message_visuals(message_type: str) -> tuple[str, str]:
    """(color, icon) для site_messages по типу.
    - order  → blue  + order-site  (Заказы)
    - tradein→ blue  + message-site (Сообщения, выделенный синий — это заявка)
    - contact/feedback → cyan + message-site (Сообщения)
    """
    if message_type == "order":
        return ("blue", "order-site")
    if message_type == "tradein":
        return ("blue", "message-site")
    return ("cyan", "message-site")


def _vk_profile_url(provider: str | None, provider_user_id: str | None) -> str | None:
    if not provider or not provider_user_id:
        return None
    if provider == "vk":
        return f"https://vk.com/id{provider_user_id}"
    if provider == "telegram":
        return f"https://t.me/{provider_user_id}"
    return None


def _build_client_from_visitor(visitor: SiteVisitor | None, msg: SiteMessage) -> dict:
    """Достаём клиента из SiteVisitor (если есть) с fallback на денорм-поля SiteMessage."""
    auth_provider = (visitor.auth_provider if visitor else None) or msg.auth_provider
    return {
        "name": (visitor.display_name if visitor else None) or msg.contact_name,
        "avatar_url": visitor.avatar_url if visitor else None,
        "profile_url": _vk_profile_url(
            auth_provider,
            visitor.auth_provider_user_id if visitor else None,
        ),
        "phone": (visitor.contact_phone if visitor else None) or msg.contact_phone,
        "email": (visitor.contact_email if visitor else None) or msg.contact_email,
        "auth_provider": auth_provider,
    }


def _site_preview(msg: SiteMessage) -> str:
    if msg.message_type == "tradein":
        parts = [msg.tradein_brand or "", msg.tradein_model or "", msg.tradein_storage or ""]
        return f"Trade-in: {' '.join(p for p in parts if p)}".strip()
    if msg.message_type == "order":
        return "Заказ с сайта"
    return "Обращение с сайта"


@router.get("/unified")
async def list_unified(
    store_id: Optional[str] = Query(None),
    source: Optional[str] = Query(None, description="site|avito|None"),
    kind: Optional[str] = Query(None, description="order|message|None"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_active),
):
    """Единая лента: заявки с сайта + диалоги Avito.

    Возвращает items с полями source/kind/color/icon — фронтенд использует их
    для раскраски карточек. Аватарка/имя/ссылка на профиль приходят в client.
    """
    effective_store_id = _effective_store_id(current_user, store_id)
    items: list[dict] = []

    # Site messages
    if source != "avito":
        site_q = (
            select(SiteMessage, SiteVisitor)
            .outerjoin(SiteVisitor, SiteMessage.visitor_id == SiteVisitor.id)
            .order_by(SiteMessage.created_at.desc())
        )
        if effective_store_id:
            site_q = site_q.where(SiteMessage.store_id == effective_store_id)
        if kind == "order":
            site_q = site_q.where(SiteMessage.message_type == "order")
        elif kind == "message":
            site_q = site_q.where(
                SiteMessage.message_type.in_(["contact", "feedback", "tradein"])
            )
        site_q = site_q.limit(per_page * 4)  # запас для merge-сортировки
        for msg, visitor in (await db.execute(site_q)).all():
            color, icon = _site_message_visuals(msg.message_type)
            items.append({
                "id": msg.id,
                "source": "site",
                "kind": _site_message_kind(msg.message_type),
                "color": color,
                "icon": icon,
                "created_at": msg.created_at.isoformat(),
                "status": msg.status,
                "preview": _site_preview(msg),
                "client": _build_client_from_visitor(visitor, msg),
                "internal": {
                    "message_type": msg.message_type,
                    "site_message_id": msg.id,
                },
            })

    # Avito messages: incoming-сообщения с заполненными полями автора/контекста.
    # Заказы Avito (kind=order, зелёный) определяются детектором is_order.
    if source != "site":
        av_q = (
            select(AvitoMessage)
            .where(AvitoMessage.direction == "incoming")
            .order_by(AvitoMessage.created_at.desc())
        )
        if effective_store_id:
            av_q = av_q.where(AvitoMessage.store_id == effective_store_id)
        if kind == "order":
            av_q = av_q.where(AvitoMessage.is_order.is_(True))
        elif kind == "message":
            av_q = av_q.where(AvitoMessage.is_order.is_(False))
        av_q = av_q.limit(per_page * 4)
        for am in (await db.execute(av_q)).scalars().all():
            is_order = bool(am.is_order)
            short_id = (am.author_id or "")[:8]
            display_name = am.author_name or f"Авито · {short_id}"
            chat_url = (
                f"https://www.avito.ru/profile/messenger/channel/{am.chat_id}"
                if am.chat_id else None
            )
            items.append({
                "id": am.id,
                "source": "avito",
                "kind": "order" if is_order else "message",
                "color": "green" if is_order else "purple",
                "icon": "order-avito" if is_order else "message-avito",
                "created_at": am.created_at.isoformat(),
                "status": am.status or "new",
                "preview": (am.content or "")[:200],
                "client": {
                    "name": display_name,
                    "avatar_url": am.author_avatar_url,
                    "profile_url": am.author_profile_url or chat_url,
                    "phone": None,
                    "email": None,
                    "auth_provider": "avito",
                },
                "context": {
                    "item_id": am.item_id,
                    "item_title": am.item_title,
                    "item_url": am.item_url,
                    "chat_url": chat_url,
                } if am.item_id else None,
                "internal": {
                    "chat_id": am.chat_id,
                    "avito_message_id": am.avito_message_id,
                },
            })

    # Сортировка по дате + пагинация
    items.sort(key=lambda x: x["created_at"], reverse=True)
    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "items": items[start:end],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
