"""Best-effort Redis JSON cache для read-эндпоинтов.

Паттерн: cache-aside (lazy):
  1. GET key
  2. miss / Redis-down → factory() → отдать
  3. SETEX key ttl json(value)  — best-effort

Использование:
    from app.core.redis_cache import get_or_set_json

    @router.get("/heavy-endpoint")
    async def heavy():
        async def _compute() -> dict:
            # тяжёлый SQL → MyModel.model_dump(mode='json')
            return result.model_dump(mode='json')

        cached = await get_or_set_json("heavy:k1", 60, _compute)
        return MyModel.model_validate(cached)

При недоступности Redis (down / timeout / любые ошибки) — fallback на прямой
вызов factory без cache. Эндпоинт **никогда не падает** из-за cache.
"""
import json
import logging
from typing import Any, Awaitable, Callable

from app.core.redis_pubsub import get_redis

logger = logging.getLogger(__name__)


async def get_or_set_json(
    key: str,
    ttl: int,
    factory: Callable[[], Awaitable[Any]],
) -> Any:
    """Cache-aside JSON-кэш с TTL.

    Args:
        key: Redis-ключ, неймспейсный (e.g. "menu:{store_id}:{condition}").
        ttl: TTL в секундах (>0).
        factory: async callable возвращающий JSON-serializable value (dict/list).
                 Используем `model_dump(mode='json')` для Pydantic с UUID/datetime.

    Returns:
        Cached value либо результат factory(). Redis-ошибки не пробрасываются.
    """
    # 1) Попытка прочитать cache
    try:
        r = get_redis()
        cached = await r.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.exception("redis cache GET failed for %s — fallback to factory", key)

    # 2) Cache miss / Redis-down → factory
    value = await factory()

    # 3) Best-effort заливаем обратно в cache
    try:
        r = get_redis()
        await r.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        logger.exception("redis cache SET failed for %s (значение отдано клиенту)", key)

    return value


async def invalidate(key: str) -> None:
    """Явное удаление ключа (для админ-mutate-эндпоинтов).

    Best-effort: при Redis-down логируем и продолжаем.
    """
    try:
        r = get_redis()
        await r.delete(key)
    except Exception:
        logger.exception("redis cache DEL failed for %s", key)
