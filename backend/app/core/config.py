from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pathlib import Path

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

    DATABASE_URL: str = "postgresql+asyncpg://phonebase:phonebase@localhost:5432/phonebase"
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

    # Прямая HTTP(S)-ссылка на HTML-выгрузку 1С (Google Drive, Яндекс.Диск или любой URL)
    IMPORT_1C_HTML_URL: str | None = None

    # URL для HTML-выгрузки НОВЫХ товаров из 1С
    IMPORT_1C_NEW_HTML_URL: str | None = None

    # Интервал автоматического импорта из файлов (в минутах, 0 — отключено)
    IMPORT_INTERVAL_MINUTES: int = 30

    # Avito REST API — интервалы периодических задач (в минутах, 0 — отключено)
    AVITO_STATS_INTERVAL_MINUTES: int = 60
    AVITO_MESSENGER_INTERVAL_MINUTES: int = 5
    AVITO_FEED_CHECK_INTERVAL_MINUTES: int = 120

    # Сайты-витрины (multi-tenant)
    SITE_ORIGINS: str = Field(default="http://localhost:5174")  # comma-separated CORS origins
    SITE_RETURN_URL: str = Field(default="http://localhost:5174")  # куда редиректит OAuth после успеха
    SITE_SESSION_COOKIE_DOMAIN: str | None = None  # для кросс-субдомена (пустое = текущий хост)
    SITE_SESSION_DAYS: int = 30

    # OAuth — VK ID
    VK_APP_ID: str | None = None
    VK_APP_SECRET: str | None = None
    VK_REDIRECT_URI: str | None = None  # https://shop.basestock.ru/api/sites/{store_id}/auth/vk/callback

    # OAuth — Telegram Login Widget
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_BOT_USERNAME: str | None = None  # без @, нужен для Telegram Login Widget на сайте

    # Leads (Mobless Studio) — куда форс-форвардить заявки с публичной формы
    LEADS_CHAT_ID: str = "993678231"


settings = Settings()
