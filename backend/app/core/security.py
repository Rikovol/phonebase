"""Переэкспорт зависимостей авторизации для API, где нельзя импортировать из auth напрямую."""

from app.api.auth import get_current_user, require_admin

__all__ = ["get_current_user", "require_admin"]
