"""
Парсинг изображений товаров с biggeek.ru через Firecrawl.

Ищет товар по названию (brand + model + storage), скачивает фото товара
и сохраняет как CatalogPhoto (привязка к product_key).
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

BIGGEEK_SEARCH_URL = "https://biggeek.ru/search"


def _build_query(brand: str, model: str, storage: str) -> str:
    """Формирует поисковый запрос для biggeek.ru."""
    parts = [brand, model]
    if storage:
        parts.append(storage)
    return " ".join(p.strip() for p in parts if p and p.strip())


def scrape_product_images(brand: str, model: str, storage: str) -> list[str]:
    """
    Ищет товар на biggeek.ru и возвращает список URL изображений.

    Returns:
        Список URL изображений товара (без дубликатов, без мелких иконок).
    """
    api_key = settings.FIRECRAWL_API_KEY
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY не настроен")

    query = _build_query(brand, model, storage)
    if not query:
        raise ValueError("Не указаны brand/model для поиска")

    app = FirecrawlApp(api_key=api_key)

    # Шаг 1: поиск товара на biggeek.ru
    search_url = f"{BIGGEEK_SEARCH_URL}?q={query}"
    logger.info("Firecrawl: scraping search page %s", search_url)

    search_result = app.scrape_url(
        search_url,
        params={
            "formats": ["links"],
            "onlyMainContent": True,
        },
    )

    # Ищем ссылку на страницу товара
    product_url = _find_product_url(search_result, brand, model)
    if not product_url:
        logger.warning("Товар не найден на biggeek.ru: %s", query)
        return []

    # Шаг 2: парсим страницу товара для получения изображений
    logger.info("Firecrawl: scraping product page %s", product_url)

    product_result = app.scrape_url(
        product_url,
        params={
            "formats": ["html"],
            "onlyMainContent": True,
        },
    )

    images = _extract_images(product_result, product_url)
    logger.info("Найдено %d изображений для %s", len(images), query)
    return images


def _find_product_url(result: dict, brand: str, model: str) -> str | None:
    """Находит URL страницы товара из результатов поиска."""
    links = result.get("links", [])
    brand_lower = brand.lower() if brand else ""
    model_lower = model.lower() if model else ""
    model_slug = model_lower.replace(" ", "-") if model_lower else ""

    for link in links:
        url = link if isinstance(link, str) else link.get("url", "")
        url_lower = url.lower()
        if "biggeek.ru/" not in url_lower:
            continue
        # Пропускаем страницу поиска и главную
        if "/search" in url_lower or url_lower.rstrip("/") == "https://biggeek.ru":
            continue
        # Ищем URL содержащий бренд или модель
        if brand_lower and brand_lower in url_lower:
            return url
        if model_slug and model_slug in url_lower:
            return url

    # Fallback: первая ссылка с biggeek.ru, которая выглядит как страница товара
    for link in links:
        url = link if isinstance(link, str) else link.get("url", "")
        url_lower = url.lower()
        if "biggeek.ru/" in url_lower and "/search" not in url_lower:
            path = url_lower.split("biggeek.ru/")[-1]
            if path and "/" not in path.strip("/"):
                continue
            if path:
                return url

    return None


def _extract_images(result: dict, product_url: str) -> list[str]:
    """Извлекает URL изображений товара из HTML."""
    from bs4 import BeautifulSoup

    html = result.get("html", "")
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    seen = set()
    images = []

    # Ищем изображения в галерее товара и основном контенте
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue

        # Абсолютный URL
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = "https://biggeek.ru" + src

        # Фильтруем: только изображения товаров biggeek.ru
        if "biggeek.ru" not in src and not src.startswith("https://cdn"):
            continue

        # Пропускаем мелкие иконки, логотипы, баннеры
        if any(skip in src.lower() for skip in [
            "logo", "icon", "favicon", "banner", "svg",
            "thumb", "50x", "30x", "20x", "placeholder",
        ]):
            continue

        if src not in seen:
            seen.add(src)
            images.append(src)

    return images


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
    if len(raw) < 1000:  # слишком мелкий — скорее всего не фото
        return None

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except OSError:
        logger.warning("Не удалось открыть изображение: %s", url)
        return None

    # Пропускаем слишком мелкие изображения (иконки)
    if img.width < 200 or img.height < 200:
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
