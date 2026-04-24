"""
Rate-limiter singleton для сайтов-витрин.

Вынесен в отдельный модуль чтобы избежать circular import:
    main.py → sites.py → main.py (circular)

Использование в sites.py:
    from app.core.limiter import limiter
    ...
    @router.post("/{store_id}/messages", status_code=201)
    @limiter.limit("10/hour")
    async def create_message(request: Request, ...):
        ...

main.py читает этот же объект и регистрирует его через app.state.limiter.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    default_limits=[],  # лимиты задаются декораторами на конкретных endpoints
)
