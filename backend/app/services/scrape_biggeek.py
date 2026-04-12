"""
Парсинг изображений товаров с biggeek.ru через Firecrawl.

Ищет товар по названию (brand + model + storage), скачивает фото
из галереи товара и сохраняет как CatalogPhoto.
"""
import io
import logging
import uuid
from pathlib import Path

import httpx
from firecrawl import FirecrawlApp
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_query(brand: str, model: str, storage: str) -> str:
    """Формирует поисковый запрос для biggeek.ru."""
    parts = [brand, model]
    if storage:
        parts.append(storage)
    return " ".join(p.strip() for p in parts if p and p.strip())


def scrape_product_images(brand: str, model: str, storage: str) -> list[str]:
    """
    Ищет товар на biggeek.ru и возвращает список URL изображений из галереи.

    Returns:
        Список URL изображений товара (только из галереи, без похожих/аксессуаров).
    """
    api_key = settings.FIRECRAWL_API_KEY
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY не настроен")

    query = _build_query(brand, model, storage)
    if not query:
        raise ValueError("Не указаны brand/model для поиска")

    app = FirecrawlApp(api_key=api_key)

    # Шаг 1: поиск через Firecrawl Search API (Google) — точные результаты
    logger.info("Firecrawl search: site:biggeek.ru %s", query)

    search_results = app.search(f"site:biggeek.ru {query}")
    product_url = _find_product_url(search_results, brand, model)
    if not product_url:
        logger.warning("Товар не найден на biggeek.ru: %s", query)
        return []

    # Шаг 2: парсим страницу товара — только галерею
    logger.info("Firecrawl: scraping product page %s", product_url)

    product_result = app.scrape_url(
        product_url,
        params={
            "formats": ["html"],
            "onlyMainContent": True,
        },
    )

    images = _extract_gallery_images(product_result)
    logger.info("Найдено %d изображений для %s", len(images), query)
    return images


def _find_product_url(search_results, brand: str, model: str) -> str | None:
    """Находит URL страницы товара из результатов Firecrawl Search."""
    items = []
    if isinstance(search_results, list):
        items = search_results
    elif isinstance(search_results, dict):
        items = search_results.get("data", search_results.get("results", []))

    model_lower = model.lower() if model else ""
    model_slug = model_lower.replace(" ", "-")

    # Приоритет: /products/ URL с моделью в названии
    for item in items:
        url = item.get("url", "") if isinstance(item, dict) else ""
        url_lower = url.lower()
        if "/products/" in url_lower and model_slug and model_slug in url_lower:
            return url

    # Fallback: /catalog/ URL с моделью
    for item in items:
        url = item.get("url", "") if isinstance(item, dict) else ""
        url_lower = url.lower()
        if "/catalog/" in url_lower and model_slug and model_slug in url_lower:
            return url

    # Последний fallback: первый /products/ URL с biggeek.ru
    for item in items:
        url = item.get("url", "") if isinstance(item, dict) else ""
        if "biggeek.ru/products/" in url.lower():
            return url

    return None


def _extract_gallery_images(result: dict) -> list[str]:
    """Извлекает URL изображений только из галереи товара (не из похожих/аксессуаров)."""
    from bs4 import BeautifulSoup

    html = result.get("html", "")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    seen = set()
    images = []

    # Галерея товара: превью-слайдер
    gallery = soup.find("div", class_="sl-prewiew-thumbs")
    if gallery:
        for img in gallery.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if src and "images.biggeek.ru" in src:
                # Берём версию в максимальном разрешении
                src_full = _to_full_res(src)
                if src_full not in seen:
                    seen.add(src_full)
                    images.append(src_full)

    # Основной слайдер (большие фото)
    main_slider = soup.find("div", class_="slider-main")
    if main_slider:
        for img in main_slider.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if src and "images.biggeek.ru" in src:
                src_full = _to_full_res(src)
                if src_full not in seen:
                    seen.add(src_full)
                    images.append(src_full)

    # Если галерея не найдена — fallback: ищем og:image (мета-тег)
    if not images:
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            src = og["content"]
            if "biggeek.ru" in src and src not in seen:
                images.append(src)

    return images


def _to_full_res(url: str) -> str:
    """Конвертирует URL превью в полноразмерный (biggeek.ru хранит /1/136/ и /1/870/)."""
    # /1/136/ — превью, /1/870/ — большое фото
    if "/1/136/" in url:
        return url.replace("/1/136/", "/1/870/")
    return url


async def download_and_save_image(
    url: str,
    store_id: str,
    product_key: str,
    media_root: str,
) -> tuple[str, int] | None:
    """
    Скачивает изображение по URL и сохраняет на диск.

    Returns:
        (relative_path, file_size) или None при ошибке.
    """
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Не удалось скачать %s: %s", url, exc)
        return None

    raw = resp.content
    if len(raw) < 1000:
        return None

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except OSError:
        logger.warning("Не удалось открыть изображение: %s", url)
        return None

    if min(img.width, img.height) < 100:
        return None

    media_path = Path(media_root).resolve()
    rel_dir = f"catalog/{store_id.replace('..', '').strip('/\\')}"
    out_dir = (media_path / rel_dir).resolve()
    if not str(out_dir).startswith(str(media_path)):
        return None
    out_dir.mkdir(parents=True, exist_ok=True)

    fname = uuid.uuid4().hex
    ext = ".jpg"
    out_file = out_dir / f"{fname}{ext}"

    rgb = img.convert("RGB") if img.mode != "RGB" else img
    rgb.save(out_file, format="JPEG", quality=88, optimize=True)

    rel_path = f"{rel_dir}/{fname}{ext}".replace("\\", "/")
    return rel_path, out_file.stat().st_size
