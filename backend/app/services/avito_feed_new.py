"""
Генерация XML-фида для автозагрузки Авито — НОВЫЕ товары (formatVersion=3).

Категория «Телефоны», GoodsType «Смартфон», Condition «Новое».
Товары группируются по (brand, model, storage) внутри магазина.
Фото берутся из CatalogPhoto (привязаны к наименованию, а не к IMEI).
"""
import logging
import re
from collections import defaultdict

from lxml.etree import CDATA, Element, SubElement, tostring
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.catalog_photos import make_product_key, make_product_key_no_color
from app.core.config import settings
from app.models.business import CatalogPhoto, Product, Store

log = logging.getLogger(__name__)

_DESCRIPTION_MAX_LEN = 7500

_VALID_SIM_COUNTS = {1, 2, 3}

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
    if not brand:
        return None
    return _AVITO_BRANDS.get(brand.lower().strip(), brand)


def _norm_storage(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"[Gg][Bb]", "ГБ", s)


def _default_title_new(brand: str, model: str, storage: str) -> str:
    name = model if brand and model.lower().startswith(brand.lower()) else f"{brand} {model}".strip()
    parts = [name, _norm_storage(storage)]
    return " ".join(x for x in parts if x).strip()[:50]


def _default_description_new(
    brand: str, model: str, storage: str, colors: list[str],
    sim_count: int | None, sim_type: str | None,
    store: Store | None = None,
) -> str:
    name = model if brand and model.lower().startswith(brand.lower()) else f"{brand} {model}".strip()
    storage_str = _norm_storage(storage)
    full_name = " ".join(x for x in [name, storage_str] if x)

    lines = [f"НОВЫЙ {full_name} — в наличии!", ""]

    lines.append(f"Модель: {full_name}.")
    if colors:
        lines.append(f"Доступные цвета: {', '.join(colors)}.")
    if sim_count:
        sim_line = f"SIM-карт: {sim_count}"
        if sim_type:
            sim_line += f" ({sim_type})"
        lines.append(sim_line + ".")
    lines.append("")

    lines.append("✔️Состояние: Новый, в заводской упаковке.")
    lines.append("✔️Официальная гарантия.")
    lines.append("✔️Поможем перенести данные и настроить устройство.")
    lines.append("")

    store_name = (store.name if store else None) or "МобилАкс"
    lines.append(f"🟣{store_name} — ваш надёжный партнёр в мире цифровых технологий. Только проверенная техника.")
    lines.append("")
    lines.append("Сдайте своё старое устройство по программе Trade-in и получите дополнительную выгоду!")
    lines.append("")

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
    lines.append(f"🥳Купите {full_name} уже сегодня по самым выгодным условиям в городе!")

    return "\n".join(lines)[:_DESCRIPTION_MAX_LEN]


def _photo_url(photo: CatalogPhoto) -> str:
    base = settings.PUBLIC_URL.rstrip("/")
    path = photo.file_path.lstrip("/").replace("\\", "/")
    return f"{base}/media/{path}"


async def generate_feed_xml_new(db: AsyncSession, store_id: str) -> bytes:
    """Генерация XML-фида новых товаров для Авито."""
    store = await db.get(Store, store_id)
    if not store:
        return b""

    # Загружаем все новые товары магазина, не проданные, с ценой
    result = await db.execute(
        select(Product).where(
            and_(
                Product.store_id == store_id,
                Product.avito_published == True,  # noqa: E712
                Product.is_sold == False,  # noqa: E712
                Product.is_new == True,  # noqa: E712
                Product.price_retail.isnot(None),
            )
        )
    )
    products = result.scalars().all()

    if not products:
        root = Element("Ads", formatVersion="3", target="Avito.ru")
        xml_body = tostring(root, encoding="utf-8", xml_declaration=False)
        return b'<?xml version="1.0" encoding="utf-8"?>\n' + xml_body

    # Группируем по (brand, model, storage) — одно объявление на группу
    groups: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        key = make_product_key_no_color(p.brand or "", p.model, p.storage or "")
        groups[key].append(p)

    # Загружаем каталожные фото: ключи с цветом + без цвета
    all_keys_with_color = set()
    all_keys_no_color = set()
    for key, items in groups.items():
        all_keys_no_color.add(key)
        for p in items:
            if p.color:
                all_keys_with_color.add(make_product_key(p.brand or "", p.model, p.storage or "", p.color))

    all_search_keys = list(all_keys_with_color | all_keys_no_color)
    photos_result = await db.execute(
        select(CatalogPhoto).where(
            and_(
                CatalogPhoto.store_id == store_id,
                CatalogPhoto.product_key.in_(all_search_keys),
            )
        )
    )
    all_photos = photos_result.scalars().all()
    photos_by_key: dict[str, list[CatalogPhoto]] = defaultdict(list)
    for ph in all_photos:
        photos_by_key[ph.product_key].append(ph)

    root = Element("Ads", formatVersion="3", target="Avito.ru")
    included = 0
    skipped = 0

    for key, items in groups.items():
        # Ищем фото: пробуем каждый цвет группы, потом fallback без цвета
        catalog_photos = []
        for p in items:
            if p.color:
                ck = make_product_key(p.brand or "", p.model, p.storage or "", p.color)
                catalog_photos = sorted(
                    photos_by_key.get(ck, []),
                    key=lambda ph: (not ph.is_main, ph.created_at),
                )
                if catalog_photos:
                    break
        if not catalog_photos:
            catalog_photos = sorted(
                photos_by_key.get(key, []),
                key=lambda ph: (not ph.is_main, ph.created_at),
            )
        if not catalog_photos:
            log.debug("avito_feed_new: skip %s — нет каталожных фото", key)
            skipped += 1
            continue

        # Берём данные из первого товара группы (стабильная сортировка по id)
        items.sort(key=lambda p: p.id)
        first = items[0]
        brand = first.brand or ""
        model = first.model or ""
        storage = first.storage or ""

        # Собираем доступные цвета и суммарное количество
        colors = sorted({p.color for p in items if p.color})
        total_qty = sum(p.quantity or 1 for p in items)

        # Минимальная цена группы
        prices = [p.price_retail for p in items if p.price_retail]
        if not prices:
            skipped += 1
            continue
        min_price = min(prices)

        # SIM info из первого товара
        sim_count = first.sim_count
        sim_type = first.sim_type

        # Используем id первого товара как Id объявления (для стабильности)
        ad_id = first.id

        ad = SubElement(root, "Ad")
        SubElement(ad, "Id").text = ad_id

        SubElement(ad, "Category").text = "Телефоны"
        SubElement(ad, "GoodsType").text = "Смартфон"
        SubElement(ad, "AdType").text = "Товар от производителя"
        SubElement(ad, "Condition").text = "Новое"

        title = (first.avito_title or _default_title_new(brand, model, storage))[:50]
        SubElement(ad, "Title").text = title

        desc_text = first.avito_description or _default_description_new(
            brand, model, storage, colors, sim_count, sim_type, store,
        )
        desc_el = SubElement(ad, "Description")
        desc_el.text = CDATA(desc_text)

        SubElement(ad, "Price").text = str(round(min_price))

        sim = sim_count if sim_count in _VALID_SIM_COUNTS else 1
        SubElement(ad, "SimCount").text = str(sim)

        avito_brand = _avito_brand(brand)
        if avito_brand:
            SubElement(ad, "Brand").text = avito_brand

        if store.avito_address:
            SubElement(ad, "Address").text = store.avito_address
        if store.avito_phone:
            SubElement(ad, "ContactPhone").text = store.avito_phone
        if store.avito_manager_name:
            SubElement(ad, "ManagerName").text = store.avito_manager_name

        SubElement(ad, "ContactMethod").text = "Сообщение и звонок"

        # Фотографии (до 10)
        images_el = SubElement(ad, "Images")
        for photo in catalog_photos[:10]:
            SubElement(images_el, "Image", url=_photo_url(photo))

        included += 1

    log.info("avito_feed_new: store=%s included=%d skipped=%d", store_id, included, skipped)

    xml_body = tostring(root, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + xml_body
