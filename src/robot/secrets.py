"""Encrypted local secrets, separate from normal PHOS configuration."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from threading import RLock

from cryptography.fernet import Fernet, InvalidToken

from robot.config import atomic_write_text

logger = logging.getLogger(__name__)
_NAME = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


class SecretsError(ValueError):
    """A secret store cannot be safely read or updated."""


class SecretsService:
    """Runtime-only access to an authenticated encrypted local secret mapping."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.key_path = self.directory / "secret.key"
        self.store_path = self.directory / "secrets.enc"
        self.lock = RLock()
        self._initialize()

    def _initialize(self) -> None:
        if self.store_path.exists() and not self.key_path.exists():
            raise SecretsError("Encrypted secret store exists but its encryption key is missing.")
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as error:
            raise SecretsError("Secret store directory cannot be created.") from error
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise SecretsError("Secret store directory is unsafe.")
        if self.key_path.is_symlink() or self.store_path.is_symlink():
            raise SecretsError("Secret store files must not be symbolic links.")
        self._restrict(self.directory, 0o700)
        if not self.key_path.exists():
            atomic_write_text(self.key_path, Fernet.generate_key().decode("ascii"))
        self._restrict(self.key_path, 0o600)
        try:
            self._fernet = Fernet(self.key_path.read_bytes())
        except (OSError, ValueError) as error:
            raise SecretsError("Invalid encryption key for secret store.") from error
        if self.store_path.exists():
            self._restrict(self.store_path, 0o600)
            self._read()

    @staticmethod
    def _restrict(path: Path, mode: int) -> None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise SecretsError("Secret name must be a dot-separated logical identifier.")

    @staticmethod
    def _validate_value(value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise SecretsError("Secret value must not be empty.")

    def _read(self) -> dict[str, str]:
        if not self.store_path.exists():
            return {}
        try:
            plaintext = self._fernet.decrypt(self.store_path.read_bytes())
            values = json.loads(plaintext.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, InvalidToken) as error:
            raise SecretsError("Encrypted secret store is corrupt or cannot be decrypted.") from error
        if not isinstance(values, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                              for key, value in values.items()):
            raise SecretsError("Encrypted secret store has an invalid format.")
        for key in values:
            self._validate_name(key)
        return values

    def _write(self, values: dict[str, str]) -> None:
        plaintext = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        token = self._fernet.encrypt(plaintext).decode("ascii")
        atomic_write_text(self.store_path, token)
        self._restrict(self.store_path, 0o600)

    def has_secret(self, name: str) -> bool:
        self._validate_name(name)
        with self.lock:
            return name in self._read()

    def get_secret(self, name: str) -> str | None:
        """Return a value only for trusted runtime code, never Web DTOs."""
        self._validate_name(name)
        with self.lock:
            return self._read().get(name)

    def set_secret(self, name: str, value: str) -> None:
        self._validate_name(name)
        self._validate_value(value)
        with self.lock:
            values = self._read()
            values[name] = value
            self._write(values)
        logger.info("Secret %s configured", name)

    def remove_secret(self, name: str) -> bool:
        self._validate_name(name)
        with self.lock:
            values = self._read()
            if name not in values:
                return False
            del values[name]
            self._write(values)
        logger.info("Secret %s removed", name)
        return True
