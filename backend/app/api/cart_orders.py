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
from sqlalchemy.ext.asyncio import AsyncSession

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
