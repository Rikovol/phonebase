"""E-commerce endpoints — корзина и заказы для shop.basestock.ru.

Cart endpoints — public, привязаны к SiteVisitor cookie (existing dep).
Order endpoints — public POST/GET (свой), admin GET/PATCH/SSE (require_active).

Спека: docs/superpowers/specs/2026-05-06-mobileax-apple-redesign-design.md §3.3
"""
import html
import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import require_active
from app.api.sites import get_active_store, get_site_visitor
from app.core.config import settings
from app.core.database import get_db
from app.core.limiter import limiter
from app.models.business import (
    Cart,
    CartItem,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    SiteVisitor,
    Store,
    User,
)

logger = logging.getLogger(__name__)
router = APIRouter()

DELIVERY_TYPES = {"pickup", "courier_orel", "sdek"}


# ─── Pydantic схемы ──────────────────────────────────────────────────────────

class CartItemIn(BaseModel):
    """Тело POST /cart/items — добавить товар в корзину."""
    product_id: str = Field(min_length=1, max_length=36)
    quantity: int = Field(default=1, ge=1, le=99)


class CartItemPatch(BaseModel):
    """Тело PATCH /cart/items/{id} — изменить quantity."""
    quantity: int = Field(ge=1, le=99)


class CartItemOut(BaseModel):
    """Позиция корзины в ответе GET /cart — joined с Product."""
    id: str
    product_id: str
    quantity: int
    name: str
    brand: str | None
    model: str | None
    storage: str | None
    color: str | None
    condition: str | None
    unit_price: Decimal
    image_url: str | None


class CartOut(BaseModel):
    """Ответ GET /cart — список позиций + total."""
    id: str
    items: list[CartItemOut]
    total: Decimal


class ContactIn(BaseModel):
    """Контакт клиента в checkout-форме."""
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=10, max_length=30)
    email: str | None = Field(default=None, max_length=200)
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("name", "phone", "email", "comment", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class DeliveryIn(BaseModel):
    """Способ доставки в checkout-форме.

    type: pickup | courier_orel | sdek (см. DELIVERY_TYPES константу)
    address — обязателен для courier_orel и sdek, опционален для pickup.
    """
    type: str = Field(min_length=1, max_length=30)
    address: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=100)

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v):
        if v not in DELIVERY_TYPES:
            raise ValueError(f"delivery.type must be one of {sorted(DELIVERY_TYPES)}")
        return v


class OrderCreateIn(BaseModel):
    """Тело POST /orders — создать заказ из текущей корзины."""
    contact: ContactIn
    delivery: DeliveryIn


class OrderItemOut(BaseModel):
    """Позиция заказа в ответе."""
    id: str
    product_id: str | None
    product_snapshot: dict
    quantity: int
    unit_price: Decimal


class OrderOut(BaseModel):
    """Полный ответ — заказ с позициями."""
    id: str
    store_id: str
    status: str
    contact: dict
    delivery: dict
    total_price: Decimal
    comment: str | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemOut]


class OrderStatusPatch(BaseModel):
    """Тело PATCH /api/orders/{id} (admin) — смена статуса + опц. комментарий."""
    status: str = Field(min_length=1, max_length=20)
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v):
        valid = {s.value for s in OrderStatus}
        if v not in valid:
            raise ValueError(f"status must be one of {sorted(valid)}")
        return v


# ─── Cart endpoints (public, требуют SiteVisitor cookie) ─────────────────────


async def _get_or_create_cart(
    db: AsyncSession, visitor: SiteVisitor, store: Store
) -> Cart:
    """Возвращает корзину visitor'а или создаёт пустую.

    Race-safe: SAVEPOINT + IntegrityError → повторный SELECT (Cart.visitor_id
    UNIQUE — два параллельных GET /cart без savepoint бросали бы 500).
    Паттерн совпадает с catalog_refs._find_or_create_brand.

    Помещает store_id из URL — у visitor может быть только одна корзина (UNIQUE),
    но store_id в записи фиксируется при создании.
    Caller отвечает за commit (мы не коммитим внутри helper).
    """
    cart = (await db.execute(
        select(Cart).where(Cart.visitor_id == visitor.id)
    )).scalar_one_or_none()
    if cart is not None:
        return cart

    new_cart = Cart(visitor_id=visitor.id, store_id=store.id)
    try:
        async with db.begin_nested():
            db.add(new_cart)
            await db.flush()
    except IntegrityError:
        # Параллельный запрос уже создал корзину — читаем существующую запись.
        existing = (await db.execute(
            select(Cart).where(Cart.visitor_id == visitor.id)
        )).scalar_one_or_none()
        if existing is not None:
            return existing
        raise
    return new_cart


async def _build_cart_out(db: AsyncSession, cart: Cart) -> CartOut:
    """Загружает items + JOIN Product (с eager-load photos) для имени/цены/фото и считает total."""
    from sqlalchemy.orm import selectinload  # local import to avoid module-level overhead

    rows = (await db.execute(
        select(CartItem, Product)
        .join(Product, Product.id == CartItem.product_id)
        .options(selectinload(Product.photos))
        .where(CartItem.cart_id == cart.id)
        .order_by(CartItem.created_at)
    )).all()

    items: list[CartItemOut] = []
    total = Decimal("0")
    for ci, p in rows:
        unit_price = p.price_retail or Decimal("0")
        total += unit_price * ci.quantity
        # Берём главное фото из relationship; если is_main нет — fallback на первое фото
        main_photo = next((ph.file_path for ph in p.photos if ph.is_main), None)
        if main_photo is None and p.photos:
            main_photo = p.photos[0].file_path
        items.append(CartItemOut(
            id=ci.id,
            product_id=ci.product_id,
            quantity=ci.quantity,
            name=p.model or "",
            brand=p.brand,
            model=p.model,
            storage=p.storage,
            color=p.color,
            condition=p.condition,
            unit_price=unit_price,
            image_url=main_photo,  # relative path; frontend префиксит /media/
        ))
    return CartOut(id=cart.id, items=items, total=total)


@router.get("/sites/{store_id}/cart", response_model=CartOut)
@limiter.limit("10/minute")
async def get_cart(
    request: Request,
    store: Store = Depends(get_active_store),
    visitor: SiteVisitor | None = Depends(get_site_visitor),
    db: AsyncSession = Depends(get_db),
) -> CartOut:
    """Текущая корзина visitor'а. Если cookie отсутствует — 401."""
    if visitor is None:
        raise HTTPException(status_code=401, detail="Требуется cookie site_session")
    cart = await _get_or_create_cart(db, visitor, store)
    await db.commit()  # commit на случай создания пустой
    return await _build_cart_out(db, cart)


@router.post("/sites/{store_id}/cart/items", response_model=CartOut, status_code=201)
@limiter.limit("10/minute")
async def add_cart_item(
    request: Request,
    payload: CartItemIn = Body(...),
    store: Store = Depends(get_active_store),
    visitor: SiteVisitor | None = Depends(get_site_visitor),
    db: AsyncSession = Depends(get_db),
) -> CartOut:
    """Добавить товар в корзину. Если product_id уже есть — увеличиваем quantity (cap 99)."""
    if visitor is None:
        raise HTTPException(status_code=401, detail="Требуется cookie site_session")

    # Verify product exists in this store (security + sanity)
    product = (await db.execute(
        select(Product).where(Product.id == payload.product_id, Product.store_id == store.id)
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Товар не найден в этом магазине")

    cart = await _get_or_create_cart(db, visitor, store)

    # Upsert по UNIQUE (cart_id, product_id). Race-safe: SAVEPOINT + IntegrityError
    # → повторный SELECT + increment (double-click на «В корзину» от frontend
    # бросал бы 500 на UNIQUE violation; паттерн совпадает с _get_or_create_cart).
    existing = (await db.execute(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.product_id == payload.product_id
        )
    )).scalar_one_or_none()
    if existing is not None:
        existing.quantity = min(existing.quantity + payload.quantity, 99)
    else:
        try:
            async with db.begin_nested():
                db.add(CartItem(
                    cart_id=cart.id,
                    product_id=payload.product_id,
                    quantity=payload.quantity,
                ))
                await db.flush()
        except IntegrityError:
            # Параллельный POST уже вставил тот же товар — инкрементим вместо INSERT.
            existing = (await db.execute(
                select(CartItem).where(
                    CartItem.cart_id == cart.id,
                    CartItem.product_id == payload.product_id,
                )
            )).scalar_one()
            existing.quantity = min(existing.quantity + payload.quantity, 99)

    await db.commit()
    return await _build_cart_out(db, cart)


@router.patch("/sites/{store_id}/cart/items/{item_id}", response_model=CartOut)
@limiter.limit("20/minute")
async def update_cart_item(
    request: Request,
    item_id: str,
    payload: CartItemPatch = Body(...),
    store: Store = Depends(get_active_store),
    visitor: SiteVisitor | None = Depends(get_site_visitor),
    db: AsyncSession = Depends(get_db),
) -> CartOut:
    """Изменить quantity конкретной позиции. Только в своей корзине (по visitor_id)."""
    if visitor is None:
        raise HTTPException(status_code=401, detail="Требуется cookie site_session")

    item = (await db.execute(
        select(CartItem).join(Cart, Cart.id == CartItem.cart_id).where(
            CartItem.id == item_id, Cart.visitor_id == visitor.id
        )
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Позиция не найдена в вашей корзине")

    item.quantity = payload.quantity
    await db.commit()

    cart = await _get_or_create_cart(db, visitor, store)
    return await _build_cart_out(db, cart)


@router.delete(
    "/sites/{store_id}/cart/items/{item_id}",
    response_model=CartOut,
    response_model_exclude_none=False,
)
@limiter.limit("20/minute")
async def remove_cart_item(
    request: Request,
    item_id: str,
    store: Store = Depends(get_active_store),
    visitor: SiteVisitor | None = Depends(get_site_visitor),
    db: AsyncSession = Depends(get_db),
) -> CartOut:
    """Удалить позицию из своей корзины. Возвращает обновлённую корзину."""
    if visitor is None:
        raise HTTPException(status_code=401, detail="Требуется cookie site_session")

    item = (await db.execute(
        select(CartItem).join(Cart, Cart.id == CartItem.cart_id).where(
            CartItem.id == item_id, Cart.visitor_id == visitor.id
        )
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Позиция не найдена в вашей корзине")

    await db.delete(item)
    await db.commit()

    cart = await _get_or_create_cart(db, visitor, store)
    return await _build_cart_out(db, cart)


@router.delete("/sites/{store_id}/cart", status_code=204, response_model=None)
@limiter.limit("5/minute")
async def clear_cart(
    request: Request,
    store: Store = Depends(get_active_store),
    visitor: SiteVisitor | None = Depends(get_site_visitor),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Очистить корзину visitor'а целиком. 204 No Content."""
    if visitor is None:
        raise HTTPException(status_code=401, detail="Требуется cookie site_session")

    cart = (await db.execute(
        select(Cart).where(Cart.visitor_id == visitor.id)
    )).scalar_one_or_none()
    if cart is None:
        return  # nothing to clear — 204

    await db.execute(
        text("DELETE FROM cart_items WHERE cart_id = :cid"), {"cid": cart.id}
    )
    await db.commit()


# ─── TG уведомление продавцу при новом заказе (паттерн leads.py) ─────────────

async def _notify_tg_about_order(order: Order, items: list[OrderItem]) -> None:
    """Шлёт TG-уведомление с деталями заказа в settings.LEADS_CHAT_ID.

    Best-effort — не блокирует 201 на ошибке TG. HTML parse_mode + html.escape
    для пользовательского ввода (паттерн leads.py v1.5.4).
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return

    e = html.escape
    contact = order.contact
    delivery = order.delivery
    items_text = "\n".join([
        f"• {e(it.product_snapshot.get('name') or it.product_snapshot.get('model') or '?')} "
        f"× {it.quantity} = {it.unit_price * it.quantity} ₽"
        for it in items
    ])

    lines = [
        f"🆕 <b>Новый заказ #{order.id[:8]}</b>",
        "",
        f"👤 <b>{e(contact.get('name', ''))}</b>",
        f"📞 <code>{e(contact.get('phone', ''))}</code>",
    ]
    if contact.get("email"):
        lines.append(f"✉️ {e(contact['email'])}")

    lines.extend([
        "",
        f"🚚 <b>Доставка:</b> {e(delivery.get('type', ''))}",
    ])
    if delivery.get("address"):
        lines.append(f"📍 {e(delivery['address'])}")
    if delivery.get("city"):
        lines.append(f"🏙 {e(delivery['city'])}")

    lines.extend([
        "",
        "🛒 <b>Товары:</b>",
        items_text,
        "",
        f"💰 <b>Итого: {order.total_price} ₽</b>",
    ])
    if contact.get("comment"):
        lines.append("")
        lines.append(f"💬 {e(contact['comment'])}")

    text_msg = "\n".join(lines)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json={
                "chat_id": settings.LEADS_CHAT_ID,
                "text": text_msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            r.raise_for_status()
    except httpx.HTTPError:
        logger.exception("TG order notification failed (не блокирующий)")


# ─── Order endpoints (public POST — admin GET/PATCH/SSE в Task 10) ───────────

@router.post("/sites/{store_id}/orders", response_model=OrderOut, status_code=201)
@limiter.limit("5/minute")
async def create_order(
    request: Request,
    payload: OrderCreateIn = Body(...),
    store: Store = Depends(get_active_store),
    visitor: SiteVisitor | None = Depends(get_site_visitor),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """Создаёт заказ из текущей корзины visitor'а:
    1. SELECT Cart + CartItems JOIN Product с eager photos.
    2. Snapshot каждого товара (name/brand/storage/color/condition/image_url) → JSONB.
    3. INSERT Order + OrderItem'ы со snapshot'ами.
    4. DELETE FROM cart_items (очищаем корзину).
    5. publish_new_order Redis + _notify_tg_about_order (best-effort, ПОСЛЕ commit).
    6. Returns OrderOut.
    """
    from sqlalchemy.orm import selectinload  # local import per Task 7 паттерн

    if visitor is None:
        raise HTTPException(status_code=401, detail="Требуется cookie site_session")

    # Row-lock корзины — блокирует параллельные POST /orders для того же
    # visitor'а, предотвращая дубль-заказы при двойном клике / network glitch
    # (Task 9 code-review Important #2).
    cart = (await db.execute(
        select(Cart)
        .where(Cart.visitor_id == visitor.id, Cart.store_id == store.id)
        .with_for_update()
    )).scalar_one_or_none()
    if cart is None:
        raise HTTPException(status_code=400, detail="Корзина пуста")

    rows = (await db.execute(
        select(CartItem, Product)
        .join(Product, Product.id == CartItem.product_id)
        .options(selectinload(Product.photos))
        .where(CartItem.cart_id == cart.id)
    )).all()
    if not rows:
        raise HTTPException(status_code=400, detail="Корзина пуста")

    # 1. Snapshot + total. Защита от silent zero-price (Task 9 code-review
    # Critical #1): товар без price_retail НЕ должен бесплатно уезжать в Order.
    total = Decimal("0")
    snapshots: list[tuple[Product, int, Decimal]] = []
    for ci, p in rows:
        if p.price_retail is None or p.price_retail <= Decimal("0"):
            raise HTTPException(
                status_code=400,
                detail=f"Товар «{p.model or p.id}» сейчас недоступен к заказу (нет цены)",
            )
        unit_price = p.price_retail
        total += unit_price * ci.quantity
        snapshots.append((p, ci.quantity, unit_price))

    # 2. Create Order
    order = Order(
        store_id=store.id,
        visitor_id=visitor.id,
        status=OrderStatus.NEW.value,
        contact=payload.contact.model_dump(exclude_none=True),
        delivery=payload.delivery.model_dump(exclude_none=True),
        total_price=total,
    )
    db.add(order)
    await db.flush()  # получаем order.id

    # 3. Create OrderItem'ы со snapshot'ами
    order_items: list[OrderItem] = []
    for p, qty, unit_price in snapshots:
        main_photo = next((ph.file_path for ph in p.photos if ph.is_main), None)
        if main_photo is None and p.photos:
            main_photo = p.photos[0].file_path
        snap = {
            "name": p.model or "",
            "brand": p.brand,
            "model": p.model,
            "storage": p.storage,
            "color": p.color,
            "condition": p.condition,
            "image_url": main_photo,
        }
        oi = OrderItem(
            order_id=order.id,
            product_id=p.id,
            product_snapshot=snap,
            quantity=qty,
            unit_price=unit_price,
        )
        db.add(oi)
        order_items.append(oi)

    # 4. Clear cart (используем уже импортированный text())
    await db.execute(
        text("DELETE FROM cart_items WHERE cart_id = :cid"), {"cid": cart.id}
    )

    await db.commit()
    await db.refresh(order)

    # 5. Notifications fire-and-forget (Task 9 code-review Important #3):
    # await блокировал клиента на ≤10s при медленном TG api → теперь background
    # task. Заказ уже committed; если worker крашится между commit и task — TG
    # сообщение теряется, но Order в БД сохранён (правильный приоритет).
    import asyncio
    from app.core.redis_pubsub import publish_new_order
    asyncio.create_task(publish_new_order(order.id, store.id, str(total)))
    asyncio.create_task(_notify_tg_about_order(order, order_items))

    return OrderOut(
        id=order.id,
        store_id=order.store_id,
        status=order.status,
        contact=order.contact,
        delivery=order.delivery,
        total_price=order.total_price,
        comment=order.comment,
        created_at=order.created_at,
        updated_at=order.updated_at,
        items=[OrderItemOut(
            id=oi.id,
            product_id=oi.product_id,
            product_snapshot=oi.product_snapshot,
            quantity=oi.quantity,
            unit_price=oi.unit_price,
        ) for oi in order_items],
    )


# ─── Order endpoints: helper + visitor's own GET ─────────────────────────────


def _order_to_out(order: Order, items: list[OrderItem]) -> OrderOut:
    """Helper — DRY конвертер Order + items → OrderOut. Используется во всех
    admin endpoints + visitor get_my_order."""
    return OrderOut(
        id=order.id,
        store_id=order.store_id,
        status=order.status,
        contact=order.contact,
        delivery=order.delivery,
        total_price=order.total_price,
        comment=order.comment,
        created_at=order.created_at,
        updated_at=order.updated_at,
        items=[OrderItemOut(
            id=oi.id,
            product_id=oi.product_id,
            product_snapshot=oi.product_snapshot,
            quantity=oi.quantity,
            unit_price=oi.unit_price,
        ) for oi in items],
    )


@router.get("/sites/{store_id}/orders/{order_id}", response_model=OrderOut)
@limiter.limit("10/minute")
async def get_my_order(
    request: Request,
    order_id: str,
    store: Store = Depends(get_active_store),
    visitor: SiteVisitor | None = Depends(get_site_visitor),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """Visitor видит только свой заказ (по visitor_id) в нужном магазине.

    Если cookie отсутствует — 401.
    Если order_id не принадлежит этому visitor'у или другому магазину — 404
    (не 403, чтобы не выдавать existence информацию).
    """
    if visitor is None:
        raise HTTPException(status_code=401, detail="Требуется cookie site_session")

    order = (await db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(
            Order.id == order_id,
            Order.visitor_id == visitor.id,
            Order.store_id == store.id,
        )
    )).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    return _order_to_out(order, list(order.items))


# ─── Admin order endpoints (require_active JWT) ──────────────────────────────


@router.get("/orders", response_model=list[OrderOut])
async def admin_list_orders(
    store_id: str | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_active),
) -> list[OrderOut]:
    """Список заказов с фильтрами для admin SPA «Заказы».

    status_filter (не status — чтобы не конфликтовать с fastapi.status):
      one of new/in_progress/confirmed/shipped/completed/cancelled.
    Сортировка по created_at DESC (новые сверху). selectinload предотвращает
    N+1 при загрузке items для всей страницы.
    """
    q = (
        select(Order)
        .options(selectinload(Order.items))
        .order_by(desc(Order.created_at))
    )
    if store_id:
        q = q.where(Order.store_id == store_id)
    if status_filter:
        q = q.where(Order.status == status_filter)
    q = q.limit(min(limit, 200)).offset(offset)
    orders = (await db.execute(q)).scalars().all()
    return [_order_to_out(o, list(o.items)) for o in orders]


@router.get("/orders/{order_id}", response_model=OrderOut)
async def admin_get_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_active),
) -> OrderOut:
    """Деталь заказа для admin UI."""
    order = (await db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.id == order_id)
    )).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return _order_to_out(order, list(order.items))


@router.patch("/orders/{order_id}", response_model=OrderOut)
async def admin_patch_order(
    order_id: str,
    payload: OrderStatusPatch = Body(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_active),
) -> OrderOut:
    """Смена статуса заказа + опц. комментарий (admin)."""
    order = (await db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.id == order_id)
    )).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    order.status = payload.status
    if payload.comment is not None:
        order.comment = payload.comment
    await db.commit()
    await db.refresh(order)
    return _order_to_out(order, list(order.items))


# ─── SSE stream для real-time уведомлений admin SPA о новых заказах ──────────


@router.get("/orders/sse")
async def admin_orders_sse(
    _: User = Depends(require_active),
) -> StreamingResponse:
    """Server-Sent Events — admin подписывается, получает push при создании
    нового заказа (Redis pubsub `orders:new`).

    Headers:
      Cache-Control: no-cache (браузер не должен кешировать)
      X-Accel-Buffering: no (nginx не буферизует — иначе message приходит чанком)
    """
    from app.core.redis_pubsub import get_redis, ORDERS_CHANNEL

    async def event_generator():
        r = get_redis()
        pubsub = r.pubsub()
        try:
            await pubsub.subscribe(ORDERS_CHANNEL)
            async for msg in pubsub.listen():
                if msg.get("type") == "message":
                    # msg["data"] — JSON string из publish_new_order
                    yield f"data: {msg['data']}\n\n"
        finally:
            await pubsub.unsubscribe(ORDERS_CHANNEL)
            await pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx no-buffer
        },
    )
