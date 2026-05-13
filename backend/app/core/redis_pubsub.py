"""Redis pubsub channel для real-time уведомлений админов о новых заказах.

Используется в:
- POST /orders (cart_orders.py) — publish_new_order при создании
- GET /api/orders/sse (cart_orders.py, Task 10) — subscribe + SSE stream админу
"""
import json
import logging

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger(__name__)

ORDERS_CHANNEL = "orders:new"

_pool: redis.ConnectionPool | None = None


def get_redis() -> redis.Redis:
    """Lazy singleton — переиспользуем connection pool через все вызовы."""
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)
    return redis.Redis(connection_pool=_pool)


async def publish_new_order(order_id: str, store_id: str, total_price: str) -> None:
    """Публикует событие о новом заказе в Redis канал orders:new.

    Best-effort: если Redis недоступен — логируем и продолжаем (заказ создан).
    """
    try:
        r = get_redis()
        payload = json.dumps({
            "order_id": order_id,
            "store_id": store_id,
            "total": total_price,
        })
        await r.publish(ORDERS_CHANNEL, payload)
    except Exception:
        logger.exception("publish_new_order failed (не критично — заказ создан)")
