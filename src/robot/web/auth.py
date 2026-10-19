"""Filesystem password storage, independent of HTTP and runtime configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from threading import RLock

from werkzeug.security import check_password_hash, generate_password_hash


class PasswordStore:
    """One administrator, with an atomic, owner-only credential file.

    A missing directory bootstraps. Missing/corrupt data in an existing directory
    fails closed; it never silently restores the publicly known password.
    """

    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "password.json"
        self.lock = RLock()
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            self._write("phos", must_change=True)
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Unsafe administrator directory")
        if directory.stat().st_mode & 0o077 or self.path.stat().st_mode & 0o077:
            raise ValueError("Administrator data must be accessible only to its owner")
        if self.path.is_symlink():
            raise ValueError("Unsafe administrator file")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if (set(data) != {"password_hash", "must_change"}
                or not isinstance(data["password_hash"], str)
                or not data["password_hash"].startswith("pbkdf2:sha256:1000000$")
                or type(data["must_change"]) is not bool):
            raise ValueError("Invalid administrator data; local recovery required")
        self.data = data

    @property
    def must_change(self):
        return self.data["must_change"]

    def verify(self, password: str) -> bool:
        with self.lock:
            return len(password) <= 256 and check_password_hash(self.data["password_hash"], password)

    def change(self, current: str, new: str, confirmation: str):
        with self.lock:
            if not self.verify(current):
                raise ValueError("Current password is incorrect.")
            if new != confirmation:
                raise ValueError("New passwords do not match.")
            if not 12 <= len(new) <= 256 or not new.strip() or new == current:
                raise ValueError("Use a different password with 12–256 characters.")
            self._write(new, must_change=False)

    def reset(self, new: str, confirmation: str):
        """Set a password after an independently verified physical recovery code."""
        with self.lock:
            if new != confirmation:
                raise ValueError("New passwords do not match.")
            if not 12 <= len(new) <= 256 or not new.strip():
                raise ValueError("Use a password with 12–256 characters.")
            self._write(new, must_change=False)

    def _write(self, password: str, *, must_change: bool):
        data = {"password_hash": generate_password_hash(password, method="pbkdf2:sha256:1000000"),
                "must_change": must_change}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             delete=False) as output:
                temporary = Path(output.name)
                json.dump(data, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            self.data = data
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
