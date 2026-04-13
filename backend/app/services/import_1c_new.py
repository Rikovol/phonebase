"""
Парсер HTML-выгрузки 1С «Оценка склада НОВЫЕ».

Структура файла:
─────────────────────────────────────────────────────────────────
TR.R10 + TD.R11C0                → заголовок бренда: "iPad", "Samsung (mob)", ...
TR.R10 + TD.R12C0                → товарная строка:
  ячейка 0  склад:       "REM-GSM (Склад)"
  ячейка 1  IMEI+хар-ка: "357658250952789, A175F"
  ячейка 2  наименование: "Samsung Galaxy A17 8/256Gb Black (NEW)"
  ячейка 3  остаток:      "1,000"
  ячейка 4  розница:      "18 490"
  ячейка 5  учётная:      "13 550,00"
─────────────────────────────────────────────────────────────────
"""
import re
from dataclasses import dataclass
from typing import Optional
from bs4 import BeautifulSoup


@dataclass
class ParsedNewProduct:
    store_name:   str
    category:     Optional[str]
    imei:         str
    model:        str
    storage:      str
    color:        str
    quantity:     int
    price_retail: Optional[float]
    price_cost:   Optional[float]
    sim_type:     Optional[str] = None

    @property
    def brand(self) -> str:
        ALIASES = {
            "apple": "Apple", "iphone": "Apple", "ipad": "Apple",
            "airpods": "Apple", "macbook": "Apple",
            "samsung": "Samsung", "galaxy": "Samsung",
            "xiaomi": "Xiaomi", "redmi": "Xiaomi", "poco": "Xiaomi",
            "huawei": "Huawei", "honor": "Honor",
            "realme": "Realme", "oppo": "OPPO", "vivo": "Vivo",
            "infinix": "Infinix", "tecno": "TECNO", "nothing": "Nothing",
            "sony": "Sony", "playstation": "Sony",
            "google": "Google", "pixel": "Google",
            "oneplus": "OnePlus",
            "dyson": "Dyson", "garmin": "Garmin",
            "nintendo": "Nintendo",
        }
        # Ищем бренд по любому слову модели, не только первому
        words = self.model.lower().split() if self.model else []
        for word in words:
            if word in ALIASES:
                return ALIASES[word]
        return words[0].capitalize() if words else ""


# ─── helpers ──────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r'\s+', ' ', text.replace('\xa0', ' ')).strip()

def _parse_money(raw: str) -> Optional[float]:
    c = re.sub(r'[^\d,.]', '', raw).replace(',', '.')
    parts = c.split('.')
    if len(parts) > 2:
        c = ''.join(parts[:-1]) + '.' + parts[-1]
    try:
        return float(c) if c else None
    except ValueError:
        return None

def _parse_qty(raw: str) -> int:
    m = re.match(r'(\d+)', raw.replace('\xa0', ''))
    return int(m.group(1)) if m else 0

def _parse_imei_block(s: str) -> tuple[str, str]:
    s = _clean(s)
    if "," in s:
        parts = [p.strip() for p in s.split(",")]
        imei = parts[0]
        extra = ", ".join(parts[1:])
        return imei, extra
    m = re.match(r"^(\d{14,17})(?:\s+|$)", s)
    if m:
        rest = s[m.end():].strip()
        return m.group(1), rest
    parts = s.split(None, 1)
    imei = parts[0]
    extra = parts[1].strip() if len(parts) > 1 else ""
    return imei, extra

def _parse_new_name(name: str) -> tuple[str, str, str]:
    """Parse name like 'Samsung Galaxy A17 8/256Gb Black (NEW)' -> (model, storage, color)."""
    name = re.sub(r'\(NEW\)', '', name, flags=re.IGNORECASE).strip()
    name = re.sub(r'\(Б/У\)', '', name, flags=re.IGNORECASE).strip()

    # Extract storage
    m = re.search(r'\b(\d+(?:[/+]\d+)?[GT]b)\b', name, re.IGNORECASE)
    storage = m.group(1) if m else ''
    model_and_color = re.sub(r'\b\d+(?:[/+]\d+)?[GT]b\b', '', name, flags=re.IGNORECASE)
    model_and_color = re.sub(r'\s+', ' ', model_and_color).strip()

    # Extract color: common color words at the end
    COLOR_WORDS = {
        'black', 'white', 'blue', 'green', 'red', 'gold', 'silver', 'gray', 'grey',
        'purple', 'pink', 'orange', 'yellow', 'cream', 'titanium', 'graphite',
        'midnight', 'starlight', 'space', 'sierra', 'alpine', 'coral', 'lavender',
        'mint', 'violet', 'bronze', 'natural', 'desert', 'ultramarine', 'teal',
        'brown', 'dark', 'light', 'navy', 'peach', 'lime', 'olive', 'cyan',
        'rose', 'sand', 'phantom', 'burgundy', 'ruby',
    }
    words = model_and_color.split()
    color_parts = []
    while words and words[-1].lower() in COLOR_WORDS:
        color_parts.insert(0, words.pop())
    color = ' '.join(color_parts)
    model = ' '.join(words).strip()

    # Remove trailing parenthetical model codes like (MU8F2)
    model = re.sub(r'\s*\([A-Z0-9]+\)\s*$', '', model).strip()

    return model, storage, color

_ALL_COLOR_WORDS = {
    'black', 'white', 'blue', 'green', 'red', 'gold', 'silver', 'gray', 'grey',
    'purple', 'pink', 'orange', 'yellow', 'cream', 'titanium', 'graphite',
    'midnight', 'starlight', 'space', 'sierra', 'alpine', 'coral', 'lavender',
    'mint', 'violet', 'bronze', 'natural', 'desert', 'ultramarine', 'teal',
    'brown', 'dark', 'light', 'navy', 'peach', 'lime', 'olive', 'cyan',
    'rose', 'sand', 'phantom', 'burgundy', 'ruby',
}

def _norm_store(s: str) -> str:
    return re.sub(r'\s*\(Склад\)\s*$', '', s, flags=re.IGNORECASE).strip()


# ─── parser ───────────────────────────────────────────────────────────────────

class OneCNewHTMLParser:
    def parse(self, html: bytes | str) -> list[ParsedNewProduct]:
        if isinstance(html, bytes):
            html = self._decode(html)
        soup = BeautifulSoup(html, 'html.parser')
        products: list[ParsedNewProduct] = []
        current_cat: Optional[str] = None

        for row in soup.find_all('tr'):
            cells = row.find_all('td')
            if not cells:
                continue
            c0 = (cells[0].get('class') or [''])[0]

            # Brand/category header row
            if c0 == 'R11C0':
                t = _clean(cells[0].get_text(' '))
                if t:
                    current_cat = t
                continue

            # Product item row
            if c0 == 'R12C0':
                try:
                    p = self._row(cells, current_cat)
                    if p:
                        products.append(p)
                except Exception:
                    continue

        return products

    def _row(self, cells, category) -> Optional[ParsedNewProduct]:
        if len(cells) < 4:
            return None
        get = lambda i: _clean(cells[i].get_text(' ')) if len(cells) > i else ''
        store_raw, imei_raw, name_raw = get(0), get(1), get(2)
        qty_raw, retail_raw, cost_raw = get(3), get(4), get(5)

        if not store_raw or not name_raw:
            return None

        imei, _extra = _parse_imei_block(imei_raw)
        model, storage, color = _parse_new_name(name_raw)

        # Parse SIM type from extra (e.g. "SIM+eSIM", "eSIM+eSIM")
        sim_type = None
        if _extra:
            sim_candidate = _extra.strip()
            if re.search(r'(?i)sim|esim', sim_candidate):
                sim_type = sim_candidate
            elif not color:
                candidate = sim_candidate.split(',')[0].strip()
                if candidate.lower() in _ALL_COLOR_WORDS:
                    color = candidate

        return ParsedNewProduct(
            store_name=_norm_store(store_raw), category=category,
            imei=imei, model=model, storage=storage, color=color,
            quantity=_parse_qty(qty_raw),
            price_retail=_parse_money(retail_raw),
            price_cost=_parse_money(cost_raw),
            sim_type=sim_type,
        )

    @staticmethod
    def _decode(data: bytes) -> str:
        for enc in ('utf-8-sig', 'utf-8', 'windows-1251'):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode('utf-8', errors='replace')
