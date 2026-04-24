"""
Smoke-тесты для /api/sites/* endpoints.

Тесты разделены на два уровня:
  - Валидация и routing (не требуют БД) — запускаются всегда.
  - Интеграционные (требуют PG в Docker) — помечены @pytest.mark.skip.

Запуск:
    cd backend
    pytest tests/test_sites.py -v
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

FAKE_STORE_ID = "00000000-0000-0000-0000-000000000000"


# ── Тесты валидации (не требуют БД) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_catalog_nonexistent_store_returns_404():
    """GET /api/sites/{store_id}/catalog?condition=new → 404 для несуществующего store."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/api/sites/{FAKE_STORE_ID}/catalog?condition=new")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_catalog_missing_condition_returns_422():
    """GET /api/sites/{store_id}/catalog без обязательного ?condition → 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/api/sites/{FAKE_STORE_ID}/catalog")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_messages_honeypot_rejected():
    """POST /api/sites/{store_id}/messages с заполненным `website` (honeypot) → 422."""
    payload = {
        "message_type": "contact",
        "contact_phone": "+79001234567",
        "body": "test",
        "website": "http://spam.com",   # honeypot заполнен — должен блокироваться
        "time_to_submit_ms": 5000,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(f"/api/sites/{FAKE_STORE_ID}/messages", json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_messages_fast_submit_rejected():
    """POST с time_to_submit_ms < 3000 → 422 (anti-spam)."""
    payload = {
        "message_type": "contact",
        "contact_phone": "+79001234567",
        "body": "test",
        "website": "",
        "time_to_submit_ms": 500,   # слишком быстро — бот
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(f"/api/sites/{FAKE_STORE_ID}/messages", json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_messages_tradein_without_fields_rejected():
    """POST message_type=tradein без поля tradein → 422."""
    payload = {
        "message_type": "tradein",
        "contact_phone": "+79001234567",
        "website": "",
        "time_to_submit_ms": 5000,
        # tradein field отсутствует — должна быть ошибка валидации
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(f"/api/sites/{FAKE_STORE_ID}/messages", json=payload)
    assert r.status_code == 422


# ── Тесты, требующие БД (интеграционные) ──────────────────────────────────────

@pytest.mark.skip(reason="requires DB: PostgreSQL via Docker Compose")
@pytest.mark.asyncio
async def test_messages_my_without_auth_returns_401():
    """GET /api/sites/{store_id}/messages/my без cookie → 401.

    Требует реального store_id из seed-данных (see backend/fixtures/).
    Запускать только при поднятом docker-compose.
    """
    seed_store_id = "REPLACE_WITH_SEED_STORE_ID"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/api/sites/{seed_store_id}/messages/my")
    assert r.status_code == 401


@pytest.mark.skip(reason="requires DB: PostgreSQL via Docker Compose")
@pytest.mark.asyncio
async def test_promotions_public_no_auth_required():
    """GET /api/sites/{store_id}/promotions не требует cookie — должен возвращать 200.

    Требует реального store_id из seed-данных.
    """
    seed_store_id = "REPLACE_WITH_SEED_STORE_ID"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/api/sites/{seed_store_id}/promotions")
    assert r.status_code == 200
