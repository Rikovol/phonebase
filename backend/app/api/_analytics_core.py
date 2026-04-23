"""
Общие хелперы и константы для analytics.py и feeds.py.

До этого модуля в обоих файлах дублировались: нормализация storage/model,
маппинги condition'ов, формула market_avg. Любой рассинхрон приводил к тому,
что в Аналитике админки цена отличалась от той, что видит клиент в
trade-in калькуляторе на мобилакс.рф.

Правило: все общие для Аналитики и фида helpers — тут. Специфика
(gap-fill, filters из query, формат ответа) — в соответствующих файлах.
"""
import re


# ── Нормализация storage: "8/256Gb" → "256", "1Tb" → "1024" ────────────────

_RE_STORAGE_SLASH = re.compile(r"(\d+)/(\d+)\s*[gGгГtTтТ]")
_RE_STORAGE_NUM = re.compile(r"(\d+)")


def normalize_storage(s: str | None) -> str:
    """'8/256Gb' → '256', '256 ГБ' → '256', '1Tb' → '1024' (в Gb)."""
    if not s:
        return ""
    s = s.strip()
    m = _RE_STORAGE_SLASH.search(s)
    if m:
        val = m.group(2)
    else:
        nums = _RE_STORAGE_NUM.findall(s)
        val = nums[-1] if nums else ""
    if val and re.search(r"[tTтТ]", s):
        try:
            val = str(int(val) * 1024)
        except ValueError:
            pass
    return val


# ── Нормализация model: очистка до «ядра» для сравнения ────────────────────

_RE_ARTCODE = re.compile(r"\b[a-z]\d{3}[a-z]?(/[a-z]{2,3})?\b", re.I)
_RE_RAM = re.compile(r"\bram\s*\d+\s*(gb|гб)?\b", re.I)
_RE_PAREN = re.compile(r"\([^)]*\)")
_RE_EXTRA = re.compile(r"\b(5g|4g|lte|nfc|ds)\b", re.I)
_RE_MULTI_SPACE = re.compile(r"\s{2,}")
_RE_COLOR_TAIL = re.compile(
    r"[,;]\s*(black|white|grey|gray|blue|green|red|gold|silver|violet|yellow|pink|"
    r"marble|cobalt|titanium|starlight|midnight|jet|хорош\S*|отличн\S*|средн\S*|плох\S*).*$",
    re.I,
)
_RE_COLORS = re.compile(
    r"\b(black|white|grey|gray|blue|green|red|gold|silver|violet|yellow|pink|"
    r"marble|cobalt|titanium|starlight|midnight|jet|onyx|cream|lavender|phantom|"
    r"graphite|amber|coral|bronze|ivory|lime|orange|purple|sapphire|teal)\b",
    re.I,
)


def normalize_model(model: str | None, brand: str | None) -> str:
    """Приводит модель к ядру для сравнения.
    'Samsung Galaxy S24 Ultra, Black' → 'galaxy s24 ultra'
    'Galaxy S24 S921B/DS Ram 8Gb' → 'galaxy s24'
    """
    if not model:
        return ""
    m = model.strip().lower()
    if brand:
        b = brand.strip().lower()
        if m.startswith(b + " "):
            m = m[len(b) + 1:]
    m = _RE_PAREN.sub(" ", m)
    m = _RE_ARTCODE.sub(" ", m)
    m = _RE_RAM.sub(" ", m)
    m = _RE_COLOR_TAIL.sub("", m)
    m = _RE_COLORS.sub(" ", m)
    m = _RE_EXTRA.sub(" ", m)
    m = _RE_MULTI_SPACE.sub(" ", m).strip(" ,.")
    return m


def clean_model(brand: str, model: str) -> str:
    """Удаляет ТОЛЬКО префикс бренда, без агрессивной чистки артикулов/цветов.
    Нужно в trade-in фиде — там модель отдаётся клиенту с артикулом/памятью
    как идентификатор позиции, а нормализованное ядро используется только
    для matching с конкурентами (через normalize_model)."""
    m = re.sub(rf"^{re.escape(brand)}\s+", "", model, flags=re.I).strip()
    return re.sub(r"\s+", " ", m)


# ── Маппинг condition из Product → стандартные уровни excellent/good/poor/repair ──

COND_MAP: dict[str, str] = {
    "Как новый": "excellent",
    "Отличное": "excellent",
    "Новое": "excellent",
    "Хорошее": "good",
    "Удовлетворительное": "poor",
    "Плохое": "poor",
    "На запчасти": "repair",
    "Ремонт": "repair",
    "Требуется ремонт": "repair",
}


# Порядок состояний для UI-сортировки (верх → низ списка).
CONDITION_ORDER: dict[str, int] = {
    "Как новый": 0,
    "Отличное": 1,
    "Хорошее": 2,
    "Удовлетворительное": 3,
    "На запчасти": 4,
}


# Обратный маппинг: поле competitor_price → наш condition-name (нужно для gap-fill
# в analytics.py — когда у нас нет позиции, но есть цена у конкурента для данного
# уровня состояния).
GAP_CONDITION_MAP: list[tuple[str, str]] = [
    ("price_excellent", "Отличное"),
    ("price_good", "Хорошее"),
    ("price_poor", "Удовлетворительное"),
    ("price_repair", "На запчасти"),
]


# ── Формула market price для trade-in ──────────────────────────────────────

def market_avg(our_cost: float | None, competitor_price: float | None) -> float | None:
    """
    Формула расчёта рыночной цены выкупа (для trade-in калькулятора мобилакс.рф):

        market = (our_cost + competitor_price) / 2   — если есть оба
               | competitor_price                     — если только конкурент
               | our_cost                             — если только наша
               | None                                 — если нет ничего

    Применяется отдельно для каждого condition (excellent/good/poor/repair).
    """
    if our_cost is not None and competitor_price is not None:
        return (our_cost + competitor_price) / 2
    if competitor_price is not None:
        return float(competitor_price)
    if our_cost is not None:
        return float(our_cost)
    return None
