"""
Парсинг изображений товаров с biggeek.ru (прямой HTTP, без Firecrawl).

Ищет товар по каталогу biggeek.ru, матчит по brand + model + storage + color,
скачивает ВСЕ фото из галереи товара и сохраняет как CatalogPhoto.
"""
import io
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from PIL import Image

logger = logging.getLogger(__name__)

# Маппинг модель-keywords → URL-категории biggeek.ru (приоритет над брендом)
MODEL_CATALOG_MAP: list[tuple[list[str], list[str]]] = [
    (["airpods"], ["/catalog/apple-airpods"]),
    (["ipad"], ["/catalog/apple-ipad"]),
    (["macbook"], ["/catalog/noutbuki-apple"]),
    (["apple watch", "watch ultra", "watch se"], ["/catalog/apple-watch"]),
    (["galaxy tab"], ["/catalog/planshety-samsung"]),
    (["galaxy buds"], ["/catalog/naushniki-samsung"]),
    (["galaxy watch"], ["/catalog/chasy-i-elektronnye-braslety"]),
    (["playstation", "ps5", "ps4", "dualsense"], ["/catalog/sony-playstation"]),
    (["nintendo", "switch"], ["/catalog/mediapleery-i-igrovye-konsoli"]),
    (["dyson"], ["/catalog/dlya-doma"]),
    (["garmin"], ["/catalog/chasy-i-elektronnye-braslety"]),
]

# Маппинг брендов → URL-категории biggeek.ru (fallback)
BRAND_CATALOG_MAP: dict[str, list[str]] = {
    "apple": ["/catalog/apple-iphone"],
    "samsung": ["/catalog/smartfony-samsung"],
    "xiaomi": ["/catalog/cmartfony-xiaomi", "/catalog/smartfony-redmi", "/catalog/smartfony-poco"],
    "poco": ["/catalog/smartfony-poco"],
    "redmi": ["/catalog/smartfony-redmi"],
    "honor": ["/catalog/huawei-honor"],
    "huawei": ["/catalog/kupit-huawei"],
    "google": ["/catalog/google-pixel"],
    "oneplus": ["/catalog/kupit-oneplus"],
    "nothing": ["/catalog/nothing-phone"],
    "realme": ["/catalog/smartfony"],
    "tecno": ["/catalog/smartfony"],
    "infinix": ["/catalog/smartfony"],
    "sony": ["/catalog/sony-playstation"],
    "dyson": ["/catalog/dlya-doma"],
    "garmin": ["/catalog/chasy-i-elektronnye-braslety"],
    "nothing": ["/catalog/nothing-phone"],
}

# Маппинг русских цветов → английские (biggeek использует оба в URL)
COLOR_MAP: dict[str, list[str]] = {
    "черный": ["black", "cernyj", "chernyj", "midnight"],
    "белый": ["white", "belyj", "starlight"],
    "синий": ["blue", "sinij"],
    "голубой": ["blue", "goluboj", "light-blue"],
    "красный": ["red", "krasnyj", "product-red"],
    "зеленый": ["green", "zelenyj"],
    "фиолетовый": ["purple", "fioletovyj"],
    "розовый": ["pink", "rozovyj", "rose"],
    "серый": ["gray", "grey", "seryj", "graphite", "space-gray"],
    "серебристый": ["silver", "serebristyj"],
    "золотой": ["gold", "zolotoj"],
    "бежевый": ["beige", "bezevyj"],
    "коралловый": ["coral", "korallovyj"],
    "титановый": ["titanium", "titanovyj", "titan"],
    "натуральный": ["natural", "naturalnyj"],
    "песочный": ["sand", "desert", "pesocnyj"],
    "ультрамарин": ["ultramarine"],
    "лавандовый": ["lavender", "lavandovyj"],
}

BASE_URL = "https://biggeek.ru"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def _normalize_color(color: str) -> list[str]:
    """Возвращает список возможных slug-вариантов цвета для матчинга в URL."""
    c = color.lower().strip()
    variants = [c]

    for ru, en_list in COLOR_MAP.items():
        if ru in c or c in ru:
            variants.extend(en_list)

    # Английские цвета напрямую
    english_colors = [
        "black", "white", "blue", "red", "green", "purple", "pink",
        "gray", "grey", "silver", "gold", "graphite", "titanium",
        "midnight", "starlight", "natural", "desert", "ultramarine",
    ]
    for ec in english_colors:
        if ec in c:
            variants.append(ec)

    return list(set(variants))


# Расшифровка аббревиатур для нечёткого матчинга
MODEL_SYNONYMS: dict[str, list[str]] = {
    "anc": ["active", "noise", "cancellation"],
}


def _expand_model_words(model: str) -> set[str]:
    """Разбивает модель на слова, расшифровывает аббревиатуры."""
    words = set(re.findall(r'[a-zа-яё0-9]+', model.lower()))
    expanded = set(words)
    for abbr, full_words in MODEL_SYNONYMS.items():
        if abbr in words:
            expanded.update(full_words)
    return expanded


def _model_to_slug(model: str) -> str:
    """Превращает модель в slug для поиска в URL."""
    return re.sub(r'\s+', '-', model.lower().strip())


def _match_url(url: str, brand: str, model: str, storage: str, color: str) -> int:
    """Возвращает score матча URL с параметрами товара. 0 = нет матча."""
    url_lower = url.lower()
    if "/products/" not in url_lower:
        return 0

    score = 0
    slug = _model_to_slug(model)

    # Точный матч модели: после slug должна идти цифра, однобуквенный предлог, или конец
    # "airpods-pro-3-s-zaradnym" → match (s = предлог)
    # "iphone-16-pro-max" → no match (max = другая модель)
    pattern = re.escape(slug) + r'(?=-\d|-[a-z](?=-)|$)'
    if re.search(pattern, url_lower):
        score += 10
    elif slug in url_lower:
        score += 3
    else:
        # Нечёткий матч: считаем совпавшие ключевые слова модели в URL
        # Для случаев типа "AirPods 4 ANC" → "airpods-with-active-noise-cancellation-4-go-pokolenia"
        model_words = _expand_model_words(model)
        url_words = set(re.findall(r'[a-zа-яё0-9]+', url_lower))
        # Исключаем общие слова (бренд, предлоги)
        stopwords = {"apple", "samsung", "xiaomi", "gb", "с", "и", "для"}
        model_words -= stopwords
        if not model_words:
            return 0
        matched = model_words & url_words
        ratio = len(matched) / len(model_words)
        if ratio >= 0.5 and len(matched) >= 2:
            score += round(ratio * 8)  # max 8 за нечёткий матч
        else:
            return 0

    if storage:
        storage_num = re.sub(r'[^\d]', '', storage)
        if storage_num and f"{storage_num}-gb" in url_lower.replace(" ", "-"):
            score += 5

    if color:
        color_variants = _normalize_color(color)
        if any(cv in url_lower for cv in color_variants):
            score += 20  # Цвет — высший приоритет

    # Запчасти/аксессуары (левый наушник, правый наушник, зарядный футляр) — пропускаем
    if re.search(r'levyj|pravyj|zaradn|futlar|oem', url_lower):
        return 0

    # "Disk" в модели = с диском → исключаем Digital Edition
    if re.search(r'\bdisk\b', model.lower()) and "digital" in url_lower:
        return 0

    return score


async def scrape_product_images(brand: str, model: str, storage: str, color: str = "") -> list[str]:
    """
    Ищет товар на biggeek.ru и возвращает список URL всех изображений из галереи.

    Стратегия:
    1. Определяем каталог по бренду
    2. ��канируем страницы каталога, ищем URL товара по model+storage+color
    3. Парсим страницу товара — извле��аем ВСЕ фото из галереи
    """
    query = " ".join(p.strip() for p in [brand, model] if p and p.strip())
    if not query:
        raise ValueError("Не указаны brand/model для поиска")

    product_url = await _find_product_url(brand, model, storage, color)
    if not product_url:
        logger.warning("Товар не найден на biggeek.ru: %s %s %s %s", brand, model, storage, color)
        return []

    images = await _scrape_gallery(product_url)
    logger.info("Найдено %d фото для %s %s %s %s", len(images), brand, model, storage, color)
    return images


def _resolve_catalogs(brand: str, model: str) -> list[str]:
    """Определяет каталоги для поиска: сначала по модели, потом по бренду."""
    model_lower = model.lower().strip()
    for keywords, catalog_paths in MODEL_CATALOG_MAP:
        if any(kw in model_lower for kw in keywords):
            return catalog_paths
    return BRAND_CATALOG_MAP.get(brand.lower().strip(), ["/catalog/smartfony"])


async def _find_product_url(brand: str, model: str, storage: str, color: str) -> str | None:
    """Находит URL товара, сканируя каталог biggeek.ru.

    При слабом матче (score < 10) дополнительно проверяет заголовок <h1>
    страницы товара на совпадение ключевых слов модели.
    """
    catalogs = _resolve_catalogs(brand, model)

    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=HEADERS) as client:
        best_url = None
        best_score = 0
        # Кандидаты со слабым матчем для title-проверки
        candidates: list[tuple[str, int]] = []

        for catalog_path in catalogs:
            for page in range(1, 20):  # До 20 страниц каталога
                url = f"{BASE_URL}{catalog_path}" + (f"?page={page}" if page > 1 else "")
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        break
                except httpx.HTTPError:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                links = soup.find_all("a", href=re.compile(r"/products/"))

                for link in links:
                    href = link.get("href", "")
                    if not href.startswith("http"):
                        href = BASE_URL + href

                    score = _match_url(href, brand, model, storage, color)
                    if score > best_score:
                        best_score = score
                        best_url = href
                    if 0 < score < 10 and href not in {c[0] for c in candidates}:
                        candidates.append((href, score))

                # Если нашли точный матч с цветом — не сканируем дальше
                if best_score >= 30:
                    return best_url

                # Если на странице нет ссылок на товары — закончились
                if not links:
                    break

        # Если матч слабый — проверяем заголовок h1 лучших кандидатов
        if best_score < 10 and candidates:
            model_words = set(re.findall(r'[a-zа-яё0-9]+', model.lower()))
            model_words -= {"gb", "гб", "с", "и", "для"}
            # Сортируем по score desc, проверяем топ-3
            candidates.sort(key=lambda x: -x[1])
            for cand_url, cand_score in candidates[:3]:
                try:
                    resp = await client.get(cand_url)
                    if resp.status_code != 200:
                        continue
                except httpx.HTTPError:
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                h1 = soup.find("h1")
                if not h1:
                    continue
                title_words = set(re.findall(r'[a-zа-яё0-9]+', h1.get_text().lower()))
                matched = model_words & title_words
                if len(matched) >= len(model_words) * 0.7 and len(matched) >= 2:
                    logger.info("Title-match: %s → %s (matched %s)", model, cand_url, matched)
                    return cand_url

        return best_url


async def _scrape_gallery(product_url: str) -> list[str]:
    """Парсит страницу товара и извлекает ВСЕ URL из галереи."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=HEADERS) as client:
        try:
            resp = await client.get(product_url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Не удалось загрузить %s: %s", product_url, exc)
            return []

    soup = BeautifulSoup(resp.text, "html.parser")
    seen = set()
    images = []

    # Галерея товара: превью-слайдер
    gallery = soup.find("div", class_="sl-prewiew-thumbs")
    if gallery:
        for img in gallery.find_all("img"):
            src = _normalize_url(img.get("src") or img.get("data-src") or "")
            if src and "images.biggeek.ru" in src:
                src_full = _to_full_res(src)
                if src_full not in seen:
                    seen.add(src_full)
                    images.append(src_full)

    # Основной слайдер (большие фото)
    main_slider = soup.find("div", class_="slider-main")
    if main_slider:
        for img in main_slider.find_all("img"):
            src = _normalize_url(img.get("src") or img.get("data-src") or "")
            if src and "images.biggeek.ru" in src:
                src_full = _to_full_res(src)
                if src_full not in seen:
                    seen.add(src_full)
                    images.append(src_full)

    # Fallback: og:image
    if not images:
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            src = _normalize_url(og["content"])
            if src and "images.biggeek.ru" in src:
                src_full = _to_full_res(src)
                if src_full not in seen:
                    images.append(src_full)

    return images


def _normalize_url(src: str) -> str:
    """Приводит относительные URL к абсолютным."""
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return BASE_URL + src
    return src


def _to_full_res(url: str) -> str:
    """Конвертирует URL превью в полноразмерный (biggeek.ru ��ранит /1/136/ и /1/870/)."""
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
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=HEADERS) as client:
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
