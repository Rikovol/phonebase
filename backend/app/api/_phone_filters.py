"""
Общие фильтры не-смартфонных товаров.

Используется в analytics.py (Аналитика цен) и feeds.py (trade-in фид).
Mac-семейство (MacBook / iMac / Mac mini / Mac Pro / Mac Studio) —
ОСТАЁТСЯ ВЕЗДЕ: магазин торгует Apple-техникой, trade-in тоже принимает.

Источник правды один. Не дублировать в потребителях.
"""


# Бренды, которые производят только не-смартфоны. Смело отсекать целиком.
# ВАЖНО: ASUS, Honor, Nothing, Huawei, TECNO, Samsung, Xiaomi, Google и т.п.
# сюда НЕ добавлять — они делают и смартфоны. Их не-смартфоны ловим по model.
NON_PHONE_BRANDS: tuple[str, ...] = (
    "Ноутбук", "Ноутбуки", "Наушники", "Планшет", "Планшеты",
    "Часы", "Аксессуары", "Гарнитура",
    "Acer", "Lenovo", "MSI", "HP", "Dell",
    "Packard Bell", "Toshiba", "Fujitsu",
    "Aquarius", "ARDOR", "ARDOR GAMING",
)


# Паттерны не-смартфонов в model — для брендов, которые делают и то и другое
# (Asus VivoBook, Honor MagicBook, Samsung Galaxy Watch, Xiaomi Pad и т.п.).
# MacBook/iMac/Mac НЕ включены — для Аналитики они остаются.
NON_PHONE_PATTERNS: tuple[str, ...] = (
    # Общие категории не-смартфонов:
    "watch", "buds", "airpods", "headphone", "наушник", "гарнитура",
    "ноутбук", "лэптоп", "laptop", "notebook",
    "планшет", "tablet", "ipad", "tab ",
    # Apple non-phone (кроме Mac-семейства):
    "apple watch", "vision pro",
    # Ноутбучные линейки разных брендов:
    "vivobook", "zenbook", "tuf gaming", "rog strix", "rog zephyrus",
    "thinkpad", "ideapad", "yoga ", "legion",
    "pavilion", "elitebook", "probook", "omen ", "envy ",
    "aspire", "predator", "nitro ", "swift ",
    "katana", "stealth", "raider ", "sword ", "prestige", "bravo ", "pulse",
    "megabook", "magicbook", "matebook",
    "galaxy book", "galaxy tab", "galaxy watch", "galaxy buds",
    "mi pad", "mi band", "xiaomi pad", "xiaomi book",
    "redmi pad", "redmi book", "redmi watch", "redmi buds",
    "pixel tablet", "pixel watch", "pixel buds",
)


def is_non_phone_model(model: str | None) -> bool:
    """Проверка, что model — не-смартфон (не подходит ни для Аналитики, ни для trade-in).
    Mac-семейство (MacBook/iMac/Mac mini/Mac Pro/Mac Studio) здесь НЕ ловится —
    остаётся и в Аналитике, и в trade-in-фиде.

    Дополнительный catch-all: любое "book" в названии (например, новый
    `Surface Book`, `Realme Book`, `Infinix Book`, не перечисленные в patterns)
    считается ноутбуком. Исключение — MacBook.
    """
    if not model:
        return False
    m = model.lower()
    if any(p in m for p in NON_PHONE_PATTERNS):
        return True
    if "book" in m and "macbook" not in m:
        return True
    return False
