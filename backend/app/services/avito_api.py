"""
HTTP-клиент для Avito REST API.

OAuth2 авторизация (client_credentials), кеширование токенов, базовые методы.
Credentials хранятся per-store (каждый магазин — отдельный аккаунт продавца на Авито).
"""
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.avito.ru"
TOKEN_URL = f"{BASE_URL}/token"


class AvitoAPIError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"Avito API {status}: {detail}")


class AvitoAPIClient:
    """Async HTTP-клиент для одного аккаунта продавца на Авито."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._token_expires_at: float = 0
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)

    async def close(self):
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ── OAuth2 ──────────────────────────────────────────────

    async def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token

        resp = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        if resp.status_code != 200:
            raise AvitoAPIError(resp.status_code, f"Token request failed: {resp.text}")

        data = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 3600) - 60
        return self._token

    # ── Базовый request ─────────────────────────────────────

    async def _request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> Any:
        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}

        resp = await self._http.request(method, path, headers=headers, json=json, params=params)

        # Retry once on 401 (token expired between check and request)
        if resp.status_code == 401:
            self._token = None
            token = await self._ensure_token()
            headers["Authorization"] = f"Bearer {token}"
            resp = await self._http.request(method, path, headers=headers, json=json, params=params)

        if resp.status_code >= 400:
            raise AvitoAPIError(resp.status_code, resp.text[:500])

        if resp.status_code == 204:
            return None
        return resp.json()

    # ── Profile API ──────────────────────────────────────────

    async def get_profile(self) -> dict:
        """Профиль продавца: имя, телефон, адрес и т.д."""
        return await self._request("GET", "/core/v1/accounts/self")

    # ── Items API ───────────────────────────────────────────

    async def get_items(self, page: int = 1, per_page: int = 50) -> dict:
        """Список объявлений продавца."""
        return await self._request(
            "GET", "/core/v1/items",
            params={"page": page, "per_page": per_page, "status": "active"},
        )

    async def get_item(self, item_id: str) -> dict:
        return await self._request("GET", f"/core/v1/items/{item_id}")

    async def update_item_price(self, item_id: str, price: int) -> dict | None:
        """Обновить цену объявления."""
        return await self._request(
            "POST", f"/core/v1/items/{item_id}/update_price",
            json={"price": price},
        )

    async def close_item(self, item_id: str) -> dict | None:
        """Снять объявление с публикации (пометить проданным)."""
        return await self._request(
            "POST", f"/core/v1/items/{item_id}/close",
        )

    # ── Statistics API ──────────────────────────────────────

    async def get_items_stats(
        self, item_ids: list[str], date_from: str, date_to: str
    ) -> dict:
        """
        Статистика просмотров/контактов/избранного.
        item_ids — до 200 ID, date_from/date_to — 'YYYY-MM-DD'.
        """
        return await self._request(
            "POST", "/core/v1/items/stats",
            json={
                "itemIds": item_ids,
                "dateFrom": date_from,
                "dateTo": date_to,
                "fields": ["uniqViews", "uniqContacts", "uniqFavorites"],
            },
        )

    # ── Messenger API ───────────────────────────────────────

    async def get_user_id(self) -> str:
        """Получить ID аккаунта продавца (нужен для мессенджера)."""
        data = await self._request("GET", "/core/v1/accounts/self")
        return str(data.get("id", ""))

    async def get_chats(self, user_id: str, limit: int = 50, offset: int = 0) -> dict:
        return await self._request(
            "GET", f"/messenger/v2/accounts/{user_id}/chats",
            params={"limit": limit, "offset": offset},
        )

    async def get_chat_messages(
        self, user_id: str, chat_id: str, limit: int = 50, offset: int = 0
    ) -> dict:
        return await self._request(
            "GET", f"/messenger/v3/accounts/{user_id}/chats/{chat_id}/messages",
            params={"limit": limit, "offset": offset},
        )

    async def subscribe_webhook(self, user_id: str, url: str) -> dict:
        return await self._request(
            "POST", f"/messenger/v3/accounts/{user_id}/webhooks",
            json={"url": url},
        )

    async def get_webhooks(self, user_id: str) -> dict:
        return await self._request("GET", f"/messenger/v3/accounts/{user_id}/webhooks")

    async def delete_webhook(self, user_id: str, webhook_id: str) -> None:
        await self._request("DELETE", f"/messenger/v3/accounts/{user_id}/webhooks/{webhook_id}")

    # ── Autoload Reports API ────────────────────────────────

    async def get_autoload_reports(self, page: int = 1, per_page: int = 10) -> dict:
        """Отчёты автозагрузки — ошибки, статусы загруженных объявлений."""
        return await self._request(
            "GET", "/autoload/v2/reports",
            params={"page": page, "per_page": per_page},
        )

    async def get_report_items(self, report_id: str, page: int = 1, per_page: int = 100) -> dict:
        """Элементы конкретного отчёта (маппинг Id → avito_item_id)."""
        return await self._request(
            "GET", f"/autoload/v2/reports/{report_id}/items",
            params={"page": page, "per_page": per_page},
        )


# ── Helper: построить клиент из Store ────────────────────────

def build_avito_client(store) -> AvitoAPIClient | None:
    """
    Создать AvitoAPIClient из объекта Store.
    client_secret расшифровывается из Fernet.
    Возвращает None если credentials не настроены.
    """
    if not store.avito_client_id or not store.avito_client_secret:
        return None

    from app.services.pd_encryption import pd_crypto

    try:
        secret = pd_crypto.decrypt(store.avito_client_secret.encode())
    except Exception:
        logger.error("Не удалось расшифровать avito_client_secret для магазина %s", store.id)
        return None

    return AvitoAPIClient(client_id=store.avito_client_id, client_secret=secret)
