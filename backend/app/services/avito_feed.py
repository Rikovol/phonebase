"""
Генерация XML-фида для автозагрузки Авито (formatVersion=3).

Категория «Телефоны», GoodsType «Смартфон», Condition «Б/у».
Включаются только товары с avito_published=True, is_sold=False и хотя бы одним фото.
Фото берутся из ProductPhoto (не PurchaseDoc).
"""
import logging
import re

from lxml.etree import CDATA, Element, SubElement, tostring
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.business import Product, ProductPhoto, Store
from app.utils.imei_sn import imei_or_sn_display

log = logging.getLogger(__name__)

_DESCRIPTION_MAX_LEN = 7500

# Допустимые значения SimCount на Авито
_VALID_SIM_COUNTS = {1, 2, 3}

# Допустимые значения Completeness на Авито (смартфоны)
_VALID_COMPLETENESS = {
    "Телефон",
    "Телефон + зарядное устройство",
    "Телефон + аксессуары",
    "Полный комплект",
}

# Нормализация устаревших/альтернативных значений → канонические для Авито
_COMPLETENESS_MAP = {
    "полная": "Полный комплект",
    "полный комплект": "Полный комплект",
    "телефон": "Телефон",
    "телефон + зарядное устройство": "Телефон + зарядное устройство",
    "телефон + зарядка": "Телефон + зарядное устройство",
    "телефон + аксессуары": "Телефон + аксессуары",
}


def _norm_completeness(value: str | None) -> str:
    if not value:
        return "Телефон"
    normalized = _COMPLETENESS_MAP.get(value.lower().strip())
    if normalized:
        return normalized
    if value in _VALID_COMPLETENESS:
        return value
    return "Телефон"


def _norm_storage(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"[Gg][Bb]", "ГБ", s)


def _default_title(p: Product) -> str:
    model = p.model or ""
    brand = p.brand or ""
    name = model if brand and model.lower().startswith(brand.lower()) else f"{brand} {model}".strip()
    parts = [name, _norm_storage(p.storage), "б/у"]
    return " ".join(x for x in parts if x).strip()[:50]


_CONDITION_DETAILS = {
    "Как новый": "Корпус и экран без дефектов.",
    "Отличное": "Корпус и экран без дефектов.",
    "Хорошее": "На экране 1–2 мелкие царапины, на корпусе мелкие царапины.",
    "Среднее": "На экране 1–2 мелкие царапины, на корпусе глубокие царапины.",
    "Удовлетворительное": "На экране и корпусе заметные царапины и потёртости.",
    "Плохое": "На экране много мелких царапин, на корпусе глубокие царапины.",
}

# Маппинг нашего condition → теги Авито ScreenCondition / BodyCondition
_SCREEN_CONDITION = {
    "Как новый": "Без царапин",
    "Отличное": "Без царапин",
    "Хорошее": "1-2 мелких царапины",
    "Среднее": "1-2 мелких царапины",
    "Удовлетворительное": "Много мелких царапин",
    "Плохое": "Много мелких царапин",
}

_BODY_CONDITION = {
    "Как новый": "Без царапин",
    "Отличное": "Без царапин",
    "Хорошее": "Мелкие царапины",
    "Среднее": "Глубокие царапины",
    "Удовлетворительное": "Глубокие царапины",
    "Плохое": "Глубокие царапины",
}


def _default_description(p: Product, store: Store | None = None) -> str:
    model = p.model or ""
    brand = p.brand or ""
    base_name = model if brand and model.lower().startswith(brand.lower()) else f"{brand} {model}".strip()
    storage_str = _norm_storage(p.storage)
    name = " ".join(x for x in [base_name, storage_str] if x)

    # Определяем прилагательное состояния для заголовка
    condition_adj = {
        "Как новый": "идеальном",
        "Отличное": "отличном",
        "Хорошее": "хорошем",
        "Среднее": "среднем",
        "Удовлетворительное": "удовлетворительном",
        "Плохое": "б/у",
    }.get(p.condition or "", "б/у")

    lines = [f"НЕ УПУСТИТЕ СВОЙ ШАНС купить {name} в {condition_adj} состоянии!", ""]

    # Характеристики
    if model:
        lines.append(f"Модель: {name}.")
    if p.color:
        lines.append(f"Цвет: {p.color}.")
    if p.battery_pct:
        lines.append(f"Состояние аккумулятора: {p.battery_pct}.")
    if p.sim_count:
        sim_line = f"SIM-карт: {p.sim_count}"
        if p.sim_type:
            sim_line += f" ({p.sim_type})"
        lines.append(sim_line + ".")
    completeness = _norm_completeness(p.completeness)
    lines.append(f"Комплектация: {completeness}.")
    lines.append("")

    # Состояние
    if p.condition:
        detail = _CONDITION_DETAILS.get(p.condition, "")
        if detail:
            lines.append(f"✔️Состояние: {p.condition} — {detail}")
        else:
            lines.append(f"✔️Состояние: {p.condition}.")
    lines.append("✔️Без ремонтов, 1 месяц на проверку качества.")
    lines.append("✔️Поможем перенести данные и настроить устройство.")
    lines.append("")

    # Магазин
    store_name = (store.name if store else None) or "МобилАкс"
    lines.append(f"🟣{store_name} — ваш надёжный партнёр в мире цифровых технологий. Только проверенная техника.")
    lines.append("")
    lines.append("Сдайте своё старое устройство по программе Trade-in и получите дополнительную выгоду!")
    lines.append("")

    # Оплата
    lines.append("🏦Покупайте сейчас, платите потом!")
    lines.append("")
    lines.append("Официальные банки-партнёры.")
    lines.append("Оформление за 15 минут.")
    lines.append("90% одобрения заявок.")
    lines.append("")
    lines.append("💳Способы оплаты:")
    lines.append("")
    lines.append("Наличные / перевод.")
    lines.append("QR / терминал.")
    lines.append("Оплата по счёту для юридических лиц.")
    lines.append("Кредит от 9 банков-партнёров.")
    lines.append("Оплата частями через Яндекс Сплит.")
    lines.append("")

    # График и адрес
    lines.append("🕘График работы:")
    lines.append("")
    lines.append("Магазин: 9:00–19:00 (без выходных).")
    lines.append("Онлайн-консультации: 9:00–21:00.")

    if store and store.avito_address:
        lines.append("")
        lines.append("🎈Наш адрес:")
        lines.append("")
        lines.append(store.avito_address)

    lines.append("")
    lines.append(f"🥳Купите {name} уже сегодня по самым выгодным условиям в городе!")

    return "\n".join(lines)[:_DESCRIPTION_MAX_LEN]


def _photo_url(photo: ProductPhoto) -> str:
    base = settings.PUBLIC_URL.rstrip("/")
    path = photo.file_path.lstrip("/").replace("\\", "/")
    return f"{base}/media/{path}"


def _parse_battery_pct(battery_pct: str | None) -> str | None:
    """Извлекает число из строки вида '90%' и проверяет диапазон 0–100.
    Возвращает строку с числом или None если значение невалидно."""
    if battery_pct is None:
        return None
    digits = re.sub(r"[^\d]", "", str(battery_pct))
    if not digits or not digits.isdigit():
        return None
    val = int(digits)
    if not (70 <= val <= 100):
        return None
    return digits


# Маппинг брендов → каноничное написание для Авито
_AVITO_BRANDS = {
    "apple": "Apple", "samsung": "Samsung", "xiaomi": "Xiaomi",
    "huawei": "Huawei", "honor": "HONOR", "realme": "realme",
    "oppo": "OPPO", "vivo": "vivo", "oneplus": "OnePlus",
    "google": "Google", "sony": "Sony", "nokia": "Nokia",
    "asus": "ASUS", "lg": "LG", "motorola": "Motorola",
    "nothing": "Nothing", "tecno": "TECNO", "infinix": "Infinix",
    "zte": "ZTE", "meizu": "Meizu", "poco": "POCO",
}


def _avito_brand(brand: str | None) -> str | None:
    """Каноничное имя бренда для Авито или None если неизвестен."""
    if not brand:
        return None
    return _AVITO_BRANDS.get(brand.lower().strip(), brand)


async def generate_feed_xml(db: AsyncSession, store_id: str) -> bytes:
    store = await db.get(Store, store_id)
    if not store:
        return b""

    result = await db.execute(
        select(Product)
        .where(
            and_(
                Product.store_id == store_id,
                Product.avito_published == True,  # noqa: E712
                Product.is_sold == False,  # noqa: E712
                Product.is_new == False,  # noqa: E712  — только б/у
                Product.in_repair == False,  # noqa: E712
                Product.price_retail.isnot(None),
                Product.condition.isnot(None),
                Product.condition != "",
                Product.condition.in_(list(_SCREEN_CONDITION.keys())),
            )
        )
        .options(selectinload(Product.photos))
    )
    products = result.scalars().all()

    root = Element("Ads", formatVersion="3", target="Avito.ru")
    included = 0
    skipped = 0

    for p in products:
        product_photos = sorted(
            p.photos, key=lambda ph: (not ph.is_main, ph.created_at)
        )
        if not product_photos:
            log.debug("avito_feed: skip %s — нет фото", p.id)
            skipped += 1
            continue

        ad = SubElement(root, "Ad")
        SubElement(ad, "Id").text = p.id

        # ── Обязательные поля Авито ──────────────────────
        SubElement(ad, "Category").text = "Телефоны"
        SubElement(ad, "GoodsType").text = "Смартфон"
        SubElement(ad, "AdType").text = "Товар приобретен на продажу"
        SubElement(ad, "Condition").text = "Б/у"

        # Состояние корпуса и экрана (обязательные для б/у телефонов)
        SubElement(ad, "ScreenCondition").text = _SCREEN_CONDITION[p.condition]
        SubElement(ad, "BodyCondition").text = _BODY_CONDITION[p.condition]

        # Состояние батареи (для Apple iPhone — обязательное)
        if p.brand and p.brand.lower() == "apple":
            pct = _parse_battery_pct(p.battery_pct)
            if pct is None:
                log.debug("avito_feed: skip %s — Apple без валидного battery_pct (%s)", p.id, p.battery_pct)
                root.remove(ad)
                skipped += 1
                continue
            SubElement(ad, "BatteryCondition").text = pct

        # Заголовок — всегда обрезаем до 50 символов
        title = (p.avito_title or _default_title(p))[:50]
        SubElement(ad, "Title").text = title

        desc_text = p.avito_description or _default_description(p, store)
        desc_el = SubElement(ad, "Description")
        desc_el.text = CDATA(desc_text)

        SubElement(ad, "Price").text = str(round(p.price_retail))

        # Кол-во SIM-карт (обязательное для Авито; допустимо 1, 2, 3)
        sim = p.sim_count if p.sim_count in _VALID_SIM_COUNTS else 1
        SubElement(ad, "SimCount").text = str(sim)

        # Комплектация (обязательное для Авито)
        SubElement(ad, "Completeness").text = _norm_completeness(p.completeness)

        # ── Рекомендуемые поля (улучшают видимость) ──────
        avito_brand = _avito_brand(p.brand)
        if avito_brand:
            SubElement(ad, "Brand").text = avito_brand

        # Контактные данные магазина
        if store.avito_address:
            SubElement(ad, "Address").text = store.avito_address
        if store.avito_phone:
            SubElement(ad, "ContactPhone").text = store.avito_phone
        if store.avito_manager_name:
            SubElement(ad, "ManagerName").text = store.avito_manager_name

        # Предпочтительный способ связи
        SubElement(ad, "ContactMethod").text = "Сообщение и звонок"

        # Фотографии (до 10)
        images_el = SubElement(ad, "Images")
        for photo in product_photos[:10]:
            SubElement(images_el, "Image", url=_photo_url(photo))

        included += 1

    log.info("avito_feed: store=%s included=%d skipped=%d", store_id, included, skipped)

    xml_body = tostring(root, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + xml_body
