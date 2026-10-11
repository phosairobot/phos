"""Tests for PHOS's encrypted local, runtime-only secret store."""
from __future__ import annotations

import os

import pytest

from robot.secrets import SecretsError, SecretsService


def test_secret_lifecycle_is_encrypted_and_never_stores_plaintext(tmp_path):
    service = SecretsService(tmp_path / ".phos-secrets")
    value = "multiline secret\nwith unicode: caf\u00e9 \U0001f916"

    assert not service.has_secret("tts.elevenlabs.api_key")
    assert service.get_secret("tts.elevenlabs.api_key") is None
    service.set_secret("tts.elevenlabs.api_key", value)

    assert service.has_secret("tts.elevenlabs.api_key")
    assert service.get_secret("tts.elevenlabs.api_key") == value
    assert value.encode("utf-8") not in service.store_path.read_bytes()
    service.set_secret("tts.elevenlabs.api_key", "replacement")
    assert service.get_secret("tts.elevenlabs.api_key") == "replacement"
    assert service.remove_secret("tts.elevenlabs.api_key")
    assert not service.remove_secret("tts.elevenlabs.api_key")
    assert service.get_secret("tts.elevenlabs.api_key") is None


@pytest.mark.parametrize("name", ["", "api_key", ".tts.key", "tts.", "tts..key", "Tts.key"])
def test_secret_names_are_logical_dot_identifiers(tmp_path, name):
    service = SecretsService(tmp_path / ".phos-secrets")
    with pytest.raises(SecretsError, match="logical identifier"):
        service.set_secret(name, "value")


@pytest.mark.parametrize("value", ["", "  ", None])
def test_secret_values_must_not_be_empty(tmp_path, value):
    service = SecretsService(tmp_path / ".phos-secrets")
    with pytest.raises(SecretsError, match="must not be empty"):
        service.set_secret("tts.cartesia.api_key", value)


def test_existing_store_without_key_fails_closed(tmp_path):
    directory = tmp_path / ".phos-secrets"
    service = SecretsService(directory)
    service.set_secret("tts.google.credentials", "credential")
    service.key_path.unlink()

    with pytest.raises(SecretsError, match="key is missing"):
        SecretsService(directory)


def test_corrupt_store_fails_without_overwriting_it(tmp_path):
    directory = tmp_path / ".phos-secrets"
    service = SecretsService(directory)
    service.set_secret("tts.google.credentials", "credential")
    service.store_path.write_bytes(b"not-a-fernet-token")

    with pytest.raises(SecretsError, match="corrupt"):
        SecretsService(directory)
    assert service.store_path.read_bytes() == b"not-a-fernet-token"


def test_atomic_write_failure_preserves_existing_encrypted_store(tmp_path, monkeypatch):
    service = SecretsService(tmp_path / ".phos-secrets")
    service.set_secret("tts.elevenlabs.api_key", "original")
    original = service.store_path.read_bytes()

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("robot.config.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        service.set_secret("tts.elevenlabs.api_key", "replacement")

    assert service.store_path.read_bytes() == original
    assert not list(service.directory.glob(".secrets.enc.*"))


def test_store_permissions_are_restrictive_when_supported(tmp_path):
    service = SecretsService(tmp_path / ".phos-secrets")
    service.set_secret("tts.cartesia.api_key", "value")

    if os.name == "posix":
        assert service.directory.stat().st_mode & 0o777 == 0o700
        assert service.key_path.stat().st_mode & 0o777 == 0o600
        assert service.store_path.stat().st_mode & 0o777 == 0o600
