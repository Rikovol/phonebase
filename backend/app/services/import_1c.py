"""
Парсер HTML-выгрузки 1С «Оценка склада (в ценах по виду цены)».

Структура файла (разобрана по реальной выгрузке):
─────────────────────────────────────────────────────────────────
TR.R9  + TD.R9C0 colspan=2         → заголовок группы: "iPhone", "AirPods", ...
TR.R9 / TR.R185  + TD.R10C0/R185C0 → товарная строка:
  ячейка 0  склад:          "REM-GSM (Склад)"
  ячейка 1  IMEI+хар-ка:    "351008267826350, Orange"
  ячейка 2  наименование:   "Apple iPhone 11 128Gb (Б/У)"
  ячейка 3  цвет:           "White"
  ячейка 4  дата покупки:   "19.02.2026 0:00:00"
  ячейка 5  состояние:      "Хорошее"
  ячейка 6  АКБ:            "75%"
  ячейка 7  остаток:        "1,000"
  ячейка 8  розница:        "13 500"
  ячейка 9  учётная:        "9 000,00"
─────────────────────────────────────────────────────────────────
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup


@dataclass
class ParsedProduct:
    store_name:   str
    category:     Optional[str]
    imei:         str
    model:        str
    storage:      str
    color:        str
    condition:    str
    battery_pct:  str
    in_repair:    bool
    quantity:     int
    price_retail: Optional[float]
    price_cost:   Optional[float]
    purchased_at: Optional[datetime] = None

    @property
    def brand(self) -> str:
        first = self.model.split()[0].lower() if self.model else ""
        ALIASES = {
            "apple": "Apple", "iphone": "Apple",
            "samsung": "Samsung",
            "xiaomi": "Xiaomi", "redmi": "Xiaomi", "poco": "Xiaomi",
            "huawei": "Huawei", "honor": "Honor",
            "realme": "Realme", "oppo": "OPPO", "vivo": "Vivo",
            "infinix": "Infinix", "tecno": "TECNO", "nothing": "Nothing",
        }
        return ALIASES.get(first, self.model.split()[0] if self.model else "")


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

def _parse_imei_block(s: str) -> tuple[str, str, bool]:
    s = _clean(s)
    in_repair = "в ремонте" in s.lower()
    if "," in s:
        parts = [p.strip() for p in s.split(",")]
        imei = parts[0]
        extra = ", ".join(parts[1:])
        return imei, extra, in_repair
    # Без запятой: «3510… Orange» — в sku только цифры IMEI, цвет уходит в extra
    m = re.match(r"^(\d{14,17})(?:\s+|$)", s)
    if m:
        rest = s[m.end() :].strip()
        return m.group(1), rest, in_repair
    parts = s.split(None, 1)
    imei = parts[0]
    extra = parts[1].strip() if len(parts) > 1 else ""
    return imei, extra, in_repair

def _parse_name_full(name: str, imei_extra: str) -> tuple[str, str, str, str, str]:
    name = name.replace('(Б/У)', '').strip()
    parts = [p.strip() for p in name.split(',')]
    model_raw = parts[0]
    color     = parts[1] if len(parts) > 1 else ''
    condition = parts[2] if len(parts) > 2 else ''
    battery   = parts[3] if len(parts) > 3 else ''

    m = re.search(r'\b(\d+(?:[/+]\d+)?[GT]b)\b', model_raw, re.IGNORECASE)
    storage = m.group(1) if m else ''
    model = re.sub(r'\b\d+(?:[/+]\d+)?[GT]b\b', '', model_raw, flags=re.IGNORECASE)
    model = re.sub(r'\s+', ' ', model).strip()

    if not color and imei_extra:
        color = imei_extra.split(',')[0].strip()

    if battery and not re.match(r'^\d+%$|^(Отличн|Хорош|Удовл)', battery, re.IGNORECASE):
        battery = ''

    return model, storage, color, condition, battery

def _parse_date(raw: str) -> Optional[datetime]:
    s = raw.strip()
    if not s:
        return None
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None

def _norm_store(s: str) -> str:
    return re.sub(r'\s*\(Склад\)\s*$', '', s, flags=re.IGNORECASE).strip()


# ─── parser ───────────────────────────────────────────────────────────────────

class OneCHTMLParser:
    ITEM_ROW_CLS  = {'R9', 'R185'}
    ITEM_CELL_CLS = {'R10C0', 'R185C0'}

    def parse(self, html: bytes | str) -> list[ParsedProduct]:
        if isinstance(html, bytes):
            html = self._decode(html)
        soup = BeautifulSoup(html, 'html.parser')
        products: list[ParsedProduct] = []
        current_cat: Optional[str] = None

        for row in soup.find_all('tr'):
            row_cls = (row.get('class') or [''])[0]
            cells   = row.find_all('td')
            if not cells:
                continue
            c0 = (cells[0].get('class') or [''])[0]

            # Заголовок категории
            if row_cls == 'R9' and c0 == 'R9C0':
                t = _clean(cells[0].get_text(' '))
                if t:
                    current_cat = t
                continue

            # Товарная строка
            if row_cls in self.ITEM_ROW_CLS and c0 in self.ITEM_CELL_CLS:
                try:
                    p = self._row(cells, current_cat)
                    if p:
                        products.append(p)
                except Exception:
                    continue

        return products

    def _row(self, cells, category) -> Optional[ParsedProduct]:
        if len(cells) < 4:
            return None
        get = lambda i: _clean(cells[i].get_text(' ')) if len(cells) > i else ''
        store_raw, imei_raw, name_raw = get(0), get(1), get(2)

        if not store_raw or not name_raw:
            return None

        imei, extra, in_repair = _parse_imei_block(imei_raw)

        # Новый формат (≥8 ячеек): цвет, дата, состояние, АКБ — отдельные колонки
        if len(cells) >= 8:
            color = get(3)
            purchased_at = _parse_date(get(4))
            condition = get(5)
            battery = get(6)
            qty_raw, retail_raw, cost_raw = get(7), get(8), get(9)

            model_clean = name_raw.replace('(Б/У)', '').strip()
            m = re.search(r'\b(\d+(?:[/+]\d+)?[GT]b)\b', model_clean, re.IGNORECASE)
            storage = m.group(1) if m else ''
            model = re.sub(r'\b\d+(?:[/+]\d+)?[GT]b\b', '', model_clean, flags=re.IGNORECASE)
            model = re.sub(r'\s+', ' ', model).strip()
        else:
            # Старый формат (6 ячеек): всё в наименовании
            qty_raw, retail_raw, cost_raw = get(3), get(4), get(5)
            model, storage, color, condition, battery = _parse_name_full(name_raw, extra)
            purchased_at = None

        return ParsedProduct(
            store_name=_norm_store(store_raw), category=category,
            imei=imei, model=model, storage=storage, color=color,
            condition=condition, battery_pct=battery, in_repair=in_repair,
            quantity=_parse_qty(qty_raw),
            price_retail=_parse_money(retail_raw),
            price_cost=_parse_money(cost_raw),
            purchased_at=purchased_at,
        )

    @staticmethod
    def _decode(data: bytes) -> str:
        for enc in ('utf-8-sig', 'utf-8', 'windows-1251'):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode('utf-8', errors='replace')
