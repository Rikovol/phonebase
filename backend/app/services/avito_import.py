"""
Импорт объявлений с Авито: загрузка всех активных объявлений аккаунта
и привязка к существующим товарам по IMEI / названию модели.
"""
import logging
import re

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Product, Store
from app.services.avito_api import AvitoAPIError, build_avito_client

logger = logging.getLogger(__name__)

_RE_IMEI = re.compile(r"\b(\d{15})\b")
_RE_DIGITS = re.compile(r"\d+")


def _extract_imei(text: str) -> str | None:
    """Извлечь IMEI (15 цифр) из текста."""
    m = _RE_IMEI.search(text or "")
    return m.group(1) if m else None


def _normalize_title(title: str) -> str:
    """Нормализовать название для нечёткого сравнения."""
    t = title.lower().strip()
    # Убираем всё кроме букв, цифр, пробелов
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


_CONDITION_KEYWORDS = {
    "Отличное": ["отличн", "идеальн", "как новый", "идеал"],
    "Как новый": ["как новый", "идеальн"],
    "Хорошее": ["хорош", "нормальн"],
    "Среднее": ["средн"],
    "Плохое": ["плох"],
    "Удовлетворительное": ["удовлетвор"],
}


def _extract_condition(text: str) -> str | None:
    """Определить состояние из текста объявления."""
    t = text.lower()
    for condition, keywords in _CONDITION_KEYWORDS.items():
        for kw in keywords:
            if kw in t:
                return condition
    return None


def _conditions_compatible(product_condition: str | None, avito_condition: str | None) -> bool:
    """Проверить совместимость состояний товара и объявления."""
    if not product_condition or not avito_condition:
        return True  # если состояние неизвестно — не блокируем

    pc = product_condition.lower()
    ac = avito_condition.lower()

    # Точное совпадение
    if pc == ac:
        return True

    # Группы совместимости
    excellent = {"отличное", "как новый"}
    if pc in excellent and ac in excellent:
        return True

    return False


def _model_matches(product_model: str, product_storage: str | None,
                   product_condition: str | None, avito_title: str,
                   avito_description: str) -> bool:
    """Проверить совпадение модели + памяти + состояния."""
    title_norm = _normalize_title(avito_title)
    model_norm = _normalize_title(product_model)

    # Убираем бренд из модели если он в начале (Apple iPhone 14 → iPhone 14)
    parts = model_norm.split()
    if len(parts) > 1:
        model_no_brand = " ".join(parts[1:])
    else:
        model_no_brand = model_norm

    # Основное совпадение: модель содержится в заголовке
    matched = model_no_brand in title_norm or model_norm in title_norm
    if not matched:
        return False

    # Проверяем память если есть
    if product_storage:
        storage_nums = _RE_DIGITS.findall(product_storage)
        if storage_nums:
            storage_gb = storage_nums[-1]
            if storage_gb not in title_norm:
                return False

    # Проверяем состояние
    full_text = avito_title + " " + (avito_description or "")
    avito_cond = _extract_condition(full_text)
    if not _conditions_compatible(product_condition, avito_cond):
        return False

    return True


async def import_avito_items(db: AsyncSession, store: Store) -> dict:
    """
    Загрузить все активные объявления с Авито и привязать к товарам.

    Привязка (в порядке приоритета):
    1. По IMEI в описании/заголовке → product.sku_1c
    2. По совпадению модели + памяти в заголовке
    """
    client = build_avito_client(store)
    if not client:
        return {"error": "not_configured"}

    # Загрузить все активные объявления
    all_items = []
    async with client:
        page = 1
        while True:
            try:
                data = await client.get_items(page=page, per_page=50)
            except AvitoAPIError as e:
                logger.error("Ошибка загрузки объявлений Авито store=%s: %s", store.id, e)
                return {"error": str(e)}

            resources = data.get("resources", [])
            if not resources:
                break
            all_items.extend(resources)
            if len(resources) < 50:
                break
            page += 1

    logger.info("avito-import: store=%s, загружено %d объявлений", store.name, len(all_items))

    # Загрузить все товары магазина для маппинга
    products = (await db.execute(
        select(Product).where(
            and_(
                Product.store_id == store.id,
                Product.is_sold == False,  # noqa: E712
            )
        )
    )).scalars().all()

    # Индекс по IMEI (sku_1c)
    by_imei = {p.sku_1c: p for p in products}
    # Товары без avito_item_id — кандидаты для привязки
    unlinked = [p for p in products if not p.avito_item_id]

    linked = 0
    updated = 0
    skipped = 0
    imported_items = []

    for item in all_items:
        avito_id = str(item.get("id", ""))
        title = item.get("title", "")
        description = item.get("description", "")
        price = item.get("price", 0)
        url = item.get("url", "")
        status = item.get("status", "")

        if not avito_id:
            continue

        # Проверяем, уже ли привязано к какому-то товару
        already_linked = (await db.execute(
            select(Product).where(
                and_(
                    Product.store_id == store.id,
                    Product.avito_item_id == avito_id,
                )
            )
        )).scalar_one_or_none()

        if already_linked:
            # Обновляем URL и заголовок если изменились
            changed = False
            if url and already_linked.avito_url != url:
                already_linked.avito_url = url
                changed = True
            if title and already_linked.avito_title != title:
                already_linked.avito_title = title
                changed = True
            if changed:
                updated += 1
            skipped += 1
            imported_items.append({
                "avito_id": avito_id, "title": title, "price": price,
                "status": "already_linked", "product_id": already_linked.id,
            })
            continue

        # Привязка 1: по IMEI в заголовке или описании
        product = None
        imei_from_title = _extract_imei(title)
        imei_from_desc = _extract_imei(description)
        imei = imei_from_title or imei_from_desc

        if imei and imei in by_imei:
            product = by_imei[imei]

        # Привязка 2: по модели + памяти + состоянию
        if not product:
            for p in unlinked:
                if _model_matches(p.model, p.storage, p.condition, title, description):
                    product = p
                    break

        if product:
            product.avito_item_id = avito_id
            product.avito_url = url or product.avito_url
            if not product.avito_published:
                product.avito_published = True
            if title and not product.avito_title:
                product.avito_title = title
            linked += 1
            # Убираем из unlinked
            if product in unlinked:
                unlinked.remove(product)
            imported_items.append({
                "avito_id": avito_id, "title": title, "price": price,
                "status": "linked", "product_id": product.id,
                "match": "imei" if imei else "model",
            })
        else:
            imported_items.append({
                "avito_id": avito_id, "title": title, "price": price,
                "url": url, "status": "unmatched",
            })

    await db.commit()

    result = {
        "total_avito": len(all_items),
        "linked": linked,
        "updated": updated,
        "skipped": skipped,
        "unmatched": len([i for i in imported_items if i["status"] == "unmatched"]),
        "items": imported_items,
    }
    logger.info(
        "avito-import: store=%s — total=%d linked=%d updated=%d unmatched=%d",
        store.name, len(all_items), linked, updated,
        result["unmatched"],
    )
    return result
