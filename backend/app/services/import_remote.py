"""
Загрузка HTML-выгрузки 1С по URL (прямая ссылка или публичная ссылка Google Drive)
или из локального файла (IMPORT_1C_HTML_PATH).
"""
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.core.config import resolve_import_1c_html_path

MAX_IMPORT_BYTES = 50 * 1024 * 1024  # как в uploads


def load_import_html_from_path(path_str: str) -> tuple[bytes, str]:
    path = resolve_import_1c_html_path(path_str)
    if not path.is_file():
        raise ValueError(f"Файл выгрузки не найден: {path}")
    data = path.read_bytes()
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("Файл превышает 50 МБ")
    return data, path.name

_GDRIVE_FILE_RE = re.compile(
    r"https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
_YADISK_RE = re.compile(
    r"https?://(disk\.yandex\.(ru|com|com\.tr)|yadi\.sk)/",
    re.IGNORECASE,
)
_YADISK_DOWNLOAD_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"


def normalize_gdrive_url(url: str) -> str | None:
    """Возвращает прямую download-ссылку Google Drive или None если не GDrive."""
    u = url.strip()
    m = _GDRIVE_FILE_RE.match(u.split("?", 1)[0])
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    parsed = urlparse(u)
    if parsed.netloc in ("drive.google.com", "docs.google.com"):
        qs = parse_qs(parsed.query)
        if "id" in qs and qs["id"]:
            fid = qs["id"][0]
            return f"https://drive.google.com/uc?export=download&id={fid}"
    return None


def normalize_import_source_url(url: str) -> str:
    """Преобразует «просмотр» Google Drive в прямую загрузку."""
    gdrive = normalize_gdrive_url(url)
    return gdrive if gdrive is not None else url.strip()


async def resolve_yadisk_url(url: str, client: httpx.AsyncClient) -> str:
    """Разрешает публичную ссылку Яндекс.Диска в прямую download-ссылку через API."""
    resp = await client.get(
        _YADISK_DOWNLOAD_API,
        params={"public_key": url},
        timeout=httpx.Timeout(30.0),
    )
    if resp.status_code != 200:
        raise ValueError(f"Яндекс.Диск API вернул {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    href = data.get("href")
    if not href:
        raise ValueError("Яндекс.Диск API не вернул ссылку для скачивания")
    return href


def _filename_from_response(headers: httpx.Headers) -> str | None:
    cd = headers.get("content-disposition")
    if not cd:
        return None
    for part in cd.split(";"):
        part = part.strip()
        low = part.lower()
        if low.startswith("filename*="):
            # RFC 5987: filename*=UTF-8''%D0%B0.html
            _, _, rest = part.partition("=")
            rest = rest.strip()
            if "''" in rest:
                rest = rest.split("''", 1)[1]
            return unquote(rest.strip('"'))
        if low.startswith("filename="):
            _, _, rest = part.partition("=")
            return unquote(rest.strip().strip('"'))
    return None


async def fetch_import_html(url: str) -> tuple[bytes, str]:
    """
    Скачивает тело по URL. Поддерживает Google Drive, Яндекс.Диск, прямые ссылки.
    Возвращает (bytes, имя для лога).
    """
    u = url.strip()
    timeout = httpx.Timeout(120.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        # Яндекс.Диск: сначала получаем прямую ссылку через API
        if _YADISK_RE.match(u):
            target = await resolve_yadisk_url(u, client)
        else:
            target = normalize_import_source_url(u)

        async with client.stream("GET", target, headers={"User-Agent": "PhoneBase/1.0"}) as r:
            r.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in r.aiter_bytes():
                total += len(chunk)
                if total > MAX_IMPORT_BYTES:
                    raise ValueError("Файл по ссылке превышает 50 МБ")
                chunks.append(chunk)
            data = b"".join(chunks)

    name = _filename_from_response(r.headers) or "import-from-url.html"
    return data, name
