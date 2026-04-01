"""
Сервис шифрования персональных данных (152-ФЗ).

Используем Fernet (AES-128-CBC + HMAC-SHA256) из библиотеки cryptography.
Ключ шифрования хранится ОТДЕЛЬНО от данных — в переменной окружения.
В продакшне рекомендуется использовать HashiCorp Vault или Яндекс Lockbox.
"""
import hashlib
import os
from pathlib import Path
from cryptography.fernet import Fernet
from app.core.config import settings


class PDEncryptionService:
    """Шифрование/дешифрование персональных данных."""

    def __init__(self):
        key = settings.PD_ENCRYPTION_KEY.encode()
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> bytes:
        """Зашифровать строку с ПД."""
        if not value:
            return b""
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        """Расшифровать ПД."""
        if not token:
            return ""
        return self._fernet.decrypt(token).decode("utf-8")

    def file_hash(self, file_bytes: bytes) -> str:
        """SHA-256 хеш файла для проверки целостности."""
        return hashlib.sha256(file_bytes).hexdigest()

    def encrypt_file(self, file_bytes: bytes) -> bytes:
        """Зашифровать файл с ПД (фото паспорта, договор)."""
        return self._fernet.encrypt(file_bytes)

    def decrypt_file(self, encrypted_bytes: bytes) -> bytes:
        """Расшифровать файл с ПД."""
        return self._fernet.decrypt(encrypted_bytes)

    def save_pd_file(self, encrypted_bytes: bytes, filename: str) -> str:
        """
        Сохранить зашифрованный файл ПД на диск.
        Возвращает относительный путь для хранения в БД.
        """
        pd_root = Path(settings.PD_DOCS_ROOT)
        pd_root.mkdir(parents=True, exist_ok=True)

        # Права только для владельца процесса — никаких group/other
        pd_root.chmod(0o700)

        safe_filename = f"{os.urandom(16).hex()}_{filename}"
        file_path = pd_root / safe_filename

        with open(file_path, "wb") as f:
            f.write(encrypted_bytes)

        # Только владелец может читать файл
        file_path.chmod(0o600)

        return str(safe_filename)

    def load_pd_file(self, relative_path: str) -> bytes:
        """Загрузить и расшифровать файл ПД."""
        file_path = Path(settings.PD_DOCS_ROOT) / relative_path
        with open(file_path, "rb") as f:
            encrypted_bytes = f.read()
        return self.decrypt_file(encrypted_bytes)

    def delete_pd_file(self, relative_path: str) -> None:
        """
        Безвозвратное уничтожение файла ПД (перезапись нулями перед удалением).
        Требуется по 152-ФЗ при уничтожении ПД.
        """
        file_path = Path(settings.PD_DOCS_ROOT) / relative_path
        if file_path.exists():
            # Перезаписываем случайными байтами перед удалением
            size = file_path.stat().st_size
            with open(file_path, "wb") as f:
                f.write(os.urandom(size))
            file_path.unlink()


# Синглтон
pd_crypto = PDEncryptionService()
