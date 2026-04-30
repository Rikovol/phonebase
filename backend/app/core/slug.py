"""Простой URL-safe slugify без внешних зависимостей.

Транслитерирует кириллицу в латиницу, заменяет всё кроме [a-z0-9] на дефис,
схлопывает повторы. Для каталога моделей и категорий phonebase.
"""
from __future__ import annotations

import re

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(value: str, *, max_length: int = 100) -> str:
    """«iPhone 17 Pro Max» → 'iphone-17-pro-max'; «Смартфоны» → 'smartfony'."""
    if not value:
        return ""
    s = value.strip().lower()
    s = "".join(_TRANSLIT.get(ch, ch) for ch in s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if len(s) > max_length:
        s = s[:max_length].rstrip("-")
    return s
