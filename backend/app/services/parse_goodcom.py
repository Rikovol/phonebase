"""
Парсер цен конкурентов: t.goodcom.ru (Хорошая Связь).
Вытягивает цены скупки по всем брендам/моделям/памяти через публичный AJAX API.
"""
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import CompetitorPrice

logger = logging.getLogger(__name__)

BASE_URL = "https://t.goodcom.ru"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
SOURCE = "goodcom"


async def _get_session() -> tuple[httpx.AsyncClient, str]:
    """Получить свежую сессию и CSRF-токен."""
    client = httpx.AsyncClient(timeout=30, follow_redirects=True)
    resp = await client.get(BASE_URL, headers={"User-Agent": UA})
    resp.raise_for_status()
    csrf = client.cookies.get("csrf_cookie_name", "")
    return client, csrf


async def _post(client: httpx.AsyncClient, path: str, csrf: str, data: dict) -> dict:
    data["csrf_test_name"] = csrf
    resp = await client.post(
        f"{BASE_URL}{path}",
        data=data,
        headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"},
    )
    resp.raise_for_status()
    return resp.json()


async def _fetch_all_pages(client: httpx.AsyncClient, csrf: str, path: str, extra: dict) -> list[dict]:
    """Вычитать все страницы пагинированного AJAX-ответа."""
    items = []
    page = 1
    while True:
        data = await _post(client, path, csrf, {"term": "", "page": str(page), **extra})
        batch = data.get("items", [])
        if not batch:
            break
        items.extend(batch)
        total = data.get("total", 0)
        if page * 10 >= total:
            break
        page += 1
    return items


async def fetch_goodcom_prices() -> list[dict]:
    """Скачать все цены с GoodCom. Возвращает список словарей."""
    client, csrf = await _get_session()
    devices = []

    try:
        brands_raw = await _fetch_all_pages(client, csrf, "/ajax/searchBrand", {})
        brands = sorted({item["brand_name"] for item in brands_raw if item.get("brand_name")})
        logger.info("goodcom: найдено %d брендов: %s", len(brands), ", ".join(brands))

        for brand in brands:
            # Обновляем CSRF каждый бренд (на случай протухания)
            csrf = client.cookies.get("csrf_cookie_name", csrf)
            models = await _fetch_all_pages(client, csrf, "/ajax/searchModel", {"brandName": brand})

            for m in models:
                combined = m.get("combined_name", "")
                has_memory = m.get("memoryVariants") is not None

                if not has_memory:
                    devices.append({
                        "brand": brand,
                        "model": combined.strip(),
                        "memory": None,
                        "full_name": m.get("name", ""),
                        "price_excellent": _int(m.get("price_b")),
                        "price_good": _int(m.get("price_c")),
                        "price_poor": _int(m.get("price_d")),
                        "price_repair": _int(m.get("price_g")),
                    })
                else:
                    csrf = client.cookies.get("csrf_cookie_name", csrf)
                    memories = await _fetch_all_pages(
                        client, csrf, "/ajax/searchDeviceMemory", {"combinedName": combined},
                    )
                    for mem in memories:
                        devices.append({
                            "brand": brand,
                            "model": combined.strip(),
                            "memory": str(mem.get("memory_size", "")) + " ГБ" if mem.get("memory_size") else None,
                            "full_name": mem.get("name", ""),
                            "price_excellent": _int(mem.get("price_b")),
                            "price_good": _int(mem.get("price_c")),
                            "price_poor": _int(mem.get("price_d")),
                            "price_repair": _int(mem.get("price_g")),
                        })

            logger.info("goodcom: %s — %d моделей, всего %d", brand, len(models), len(devices))
    finally:
        await client.aclose()

    return devices


def _int(val) -> int | None:
    try:
        return int(val) if val else None
    except (ValueError, TypeError):
        return None


async def save_goodcom_prices(db: AsyncSession, devices: list[dict]) -> int:
    """Сохранить спарсенные цены в БД (полная перезапись источника goodcom)."""
    now = datetime.now(timezone.utc)

    await db.execute(delete(CompetitorPrice).where(CompetitorPrice.source == SOURCE))

    rows = []
    for d in devices:
        rows.append(CompetitorPrice(
            source=SOURCE,
            brand=d["brand"],
            model=d["model"],
            memory=d.get("memory"),
            full_name=d.get("full_name"),
            price_excellent=d.get("price_excellent"),
            price_good=d.get("price_good"),
            price_poor=d.get("price_poor"),
            price_repair=d.get("price_repair"),
            parsed_at=now,
        ))
    db.add_all(rows)
    await db.commit()
    logger.info("goodcom: сохранено %d записей", len(rows))
    return len(rows)


async def run_goodcom_parse(db: AsyncSession) -> int:
    """Полный цикл: скачать + сохранить."""
    devices = await fetch_goodcom_prices()
    return await save_goodcom_prices(db, devices)
