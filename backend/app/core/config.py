from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py — _BASE = каталог core/
_BASE = Path(__file__).resolve().parent
_BACKEND_DIR = _BASE.parents[1]  # backend/ (в Docker: /app)
_REPO_ROOT = _BASE.parents[2]  # корень репозитория PhoneBase
# .env: сначала корень репозитория, затем backend/
_PROJECT_ROOT = _REPO_ROOT
_BACKEND_ROOT = _BACKEND_DIR  # тот же каталог backend/


def _default_fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def resolve_import_1c_html_path(path_str: str) -> Path:
    """Относительный путь — от каталога backend/, затем от корня репозитория."""
    p = Path(path_str.strip())
    if p.is_absolute():
        return p
    for base in (_BACKEND_DIR, _REPO_ROOT):
        cand = (base / p).resolve()
        if cand.is_file():
            return cand
    return (_BACKEND_DIR / p).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            str(_PROJECT_ROOT / ".env"),
            str(_BACKEND_ROOT / ".env"),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    ENVIRONMENT: str = "development"
    SECRET_KEY: str = Field(
        default="dev-secret-key-for-local-phonebase-jwt-signing-min-32-chars"
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    DATABASE_URL: str = "sqlite+aiosqlite:///./phonebase.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    PD_ENCRYPTION_KEY: str = Field(default_factory=_default_fernet_key)

    MEDIA_ROOT: str = "./media"
    PD_DOCS_ROOT: str = "./pd_docs"
    # Документы закупки (по папкам IMEI/S/N), только локально — не в фидах Авито
    PURCHASE_DOCS_ROOT: str = "./purchase_docs"
    MAX_PHOTO_SIZE_MB: int = 10
    MAX_DOC_SIZE_MB: int = 20

    # CSV в .env: localhost,127.0.0.1 (не JSON-массив)
    ALLOWED_HOSTS: str = Field(default="localhost,127.0.0.1")
    CORS_ORIGINS: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173"
    )

    PUBLIC_URL: str = Field(default="http://localhost:8000")

    # Прямая HTTP(S)-ссылка на HTML-выгрузку 1С (Google Drive «поделиться» или любой URL)
    IMPORT_1C_HTML_URL: str | None = None
    # Локальный путь к тому же HTML (от каталога backend/ или абсолютный). Имеет приоритет над URL.
    IMPORT_1C_HTML_PATH: str | None = None

    # Путь / URL для HTML-выгрузки НОВЫХ товаров из 1С
    IMPORT_1C_NEW_HTML_URL: str | None = None
    IMPORT_1C_NEW_HTML_PATH: str | None = None

    # Интервал автоматического импорта из файлов (в минутах, 0 — отключено)
    IMPORT_INTERVAL_MINUTES: int = 30

    # Avito REST API — интервалы периодических задач (в минутах, 0 — отключено)
    AVITO_STATS_INTERVAL_MINUTES: int = 60
    AVITO_MESSENGER_INTERVAL_MINUTES: int = 5
    AVITO_FEED_CHECK_INTERVAL_MINUTES: int = 120


settings = Settings()
