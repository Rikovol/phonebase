"""
Генерация XML-фида для автозагрузки Авито (formatVersion=3).

Категория «Телефоны», GoodsType «Смартфон», Condition «Б/у».
Включаются только товары с avito_published=True, is_sold=False и хотя бы одним фото.
Фото берутся из ProductPhoto (не PurchaseDoc).
"""
import re

from lxml.etree import CDATA, Element, SubElement, tostring
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.business import Product, ProductPhoto, Store
from app.utils.imei_sn import imei_or_sn_display


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
    name = " ".join(x for x in [base_name, _norm_storage(p.storage)] if x)
    headline = f"{name} б/у"
    if p.battery_pct:
        headline += f" — аккумулятор {p.battery_pct}"

    lines = [headline, ""]
    lines.append("Состояние:")
    lines.append("")
    if p.condition:
        detail = _CONDITION_DETAILS.get(p.condition, "")
        if detail:
            lines.append(f"{p.condition}: {detail}")
        else:
            lines.append(f"Общее состояние: {p.condition}.")
    if p.battery_pct:
        lines.append(f"Аккумулятор: {p.battery_pct} ёмкости.")
    if p.color:
        lines.append(f"Цвет: {p.color}.")
    lines.append("Все функции работают исправно, неисправностей нет.")
    lines.append("Гарантия: 30 дней на проверку работоспособности.")

    lines.append("")
    lines.append("Преимущества покупки:")
    lines.append("")
    lines.append("Проверка перед покупкой.")
    lines.append("Помощь в настройке и переносе данных.")
    lines.append("Возможность Trade-in.")
    lines.append("Рассрочка платежа.")

    if store and store.avito_address:
        store_name = store.name or "МобилАкс"
        lines.append("")
        lines.append(
            f"Где найти: Магазин «{store_name}», {store.avito_address}."
            " Часы работы: 9:00–19:00 (консультации до 21:00)."
        )

    lines.append("")
    lines.append("Доставка: по всей России, быстрая обработка заказа.")
    lines.append("")
    lines.append("Примечание: устройство б/у, возможны незначительные следы эксплуатации, не влияющие на работу.")

    return "\n".join(lines)


def _photo_url(photo: ProductPhoto) -> str:
    base = settings.PUBLIC_URL.rstrip("/")
    path = photo.file_path.lstrip("/").replace("\\", "/")
    return f"{base}/media/{path}"


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
                Product.condition.isnot(None),
                Product.condition != "",
            )
        )
        .options(selectinload(Product.photos))
    )
    products = result.scalars().all()

    root = Element("Ads", formatVersion="3", target="Avito.ru")

    for p in products:
        product_photos = sorted(
            p.photos, key=lambda ph: (not ph.is_main, ph.created_at)
        )
        if not product_photos:
            continue

        ad = SubElement(root, "Ad")
        SubElement(ad, "Id").text = p.id

        # ── Обязательные поля Авито ──────────────────────
        SubElement(ad, "Category").text = "Телефоны"
        SubElement(ad, "GoodsType").text = "Смартфон"
        SubElement(ad, "AdType").text = "Товар приобретен на продажу"
        SubElement(ad, "Condition").text = "Б/у"

        # Состояние корпуса и экрана (обязательные для б/у телефонов)
        screen = _SCREEN_CONDITION.get(p.condition)
        body = _BODY_CONDITION.get(p.condition)
        if screen:
            SubElement(ad, "ScreenCondition").text = screen
        if body:
            SubElement(ad, "BodyCondition").text = body

        # Состояние батареи (для Apple iPhone — обязательное)
        if p.battery_pct and p.brand and p.brand.lower() == "apple":
            pct = re.sub(r"[^\d]", "", p.battery_pct)
            if pct and pct.isdigit():
                SubElement(ad, "BatteryCondition").text = pct

        SubElement(ad, "Title").text = p.avito_title or _default_title(p)

        desc_text = p.avito_description or _default_description(p, store)
        desc_el = SubElement(ad, "Description")
        desc_el.text = CDATA(desc_text)

        if p.price_retail:
            SubElement(ad, "Price").text = str(int(p.price_retail))

        # Кол-во SIM-карт (обязательное для Авито)
        sim = p.sim_count or 1
        SubElement(ad, "SimCount").text = str(sim)

        # Комплектация (обязательное для Авито)
        SubElement(ad, "Completeness").text = p.completeness or "Телефон"

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

    xml_body = tostring(root, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + xml_body
