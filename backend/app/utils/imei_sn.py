"""Отображение IMEI / серийного номера без хвостов вроде цвета из ячейки 1С."""
import re

_IMEI_HEAD = re.compile(r"^(\d{14,17})(?:\D|$)")
# Хвосты « (Black) », « (Графит) » и т.п. после IMEI/S/N
_PAREN_TAIL = re.compile(r"\s*[\(（][^)）]*[\)）]\s*$")


def _strip_paren_tails(s: str) -> str:
    t = s.strip()
    while True:
        nxt = _PAREN_TAIL.sub("", t).strip()
        if nxt == t:
            return t
        t = nxt


def imei_or_sn_display(raw: str | None) -> str:
    if not raw:
        return ""
    s = raw.strip()
    if not s:
        return ""
    head = s.split(",")[0].strip()
    head = _strip_paren_tails(head)
    m = _IMEI_HEAD.match(head)
    if m:
        return m.group(1)
    parts = head.split()
    return parts[0] if parts else head
