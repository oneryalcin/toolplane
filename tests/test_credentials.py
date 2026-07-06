"""Contracts for encrypted credential handling (#94).

The conftest autouse fixture pins TOOLPLANE_STORAGE_KEY and redirects the
token directory, so nothing here can touch the real OS keyring or
~/.toolplane.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from toolplane import credentials
from toolplane.credentials import (
    CredentialStorageError,
    _load_or_create_storage_key,
    oauth_token_storage,
    prepare_server_config,
    resolve_secret_reference,
    secret_delete,
    secret_list,
    secret_set,
)


class _FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> str | None:
        return self.values.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.values[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        import keyring.errors

        if (service, name) not in self.values:
            raise keyring.errors.PasswordDeleteError(name)
        del self.values[(service, name)]


@pytest.fixture()
def fake_keyring(monkeypatch):
    fake = _FakeKeyring()
    import keyring

    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    monkeypatch.setattr(keyring, "delete_password", fake.delete_password)
    return fake


def test_env_key_wins_without_touching_the_keyring(monkeypatch) -> None:
    # a broken keyring must be irrelevant when the env override is set
    import keyring

    def boom(*_args):
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr(keyring, "get_password", boom)
    key = _load_or_create_storage_key()
    assert key  # from conftest's TOOLPLANE_STORAGE_KEY


def test_no_key_source_fails_loudly_before_writing(monkeypatch) -> None:
    import keyring

    monkeypatch.delenv("TOOLPLANE_STORAGE_KEY")

    def boom(*_args):
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr(keyring, "get_password", boom)
    with pytest.raises(CredentialStorageError, match="TOOLPLANE_STORAGE_KEY"):
        _load_or_create_storage_key()


def test_keyring_key_is_created_once_and_reused(monkeypatch, fake_keyring) -> None:
    monkeypatch.delenv("TOOLPLANE_STORAGE_KEY")

    first = _load_or_create_storage_key()
    second = _load_or_create_storage_key()

    assert first == second
    assert len(fake_keyring.values) == 1


def test_token_storage_writes_ciphertext_only(tmp_path) -> None:
    storage = oauth_token_storage(tmp_path / "tokens")

    async def roundtrip() -> str | None:
        await storage.put(
            collection="probe",
            key="tokens",
            value={"access_token": "hunter2-super-secret"},
        )
        loaded = await storage.get(collection="probe", key="tokens")
        return loaded["access_token"] if loaded else None

    assert asyncio.run(roundtrip()) == "hunter2-super-secret"
    raw = "".join(
        path.read_text()
        for path in (tmp_path / "tokens").rglob("*")
        if path.is_file()
    )
    assert "hunter2-super-secret" not in raw
    assert "__encrypted_data__" in raw


def test_resolve_passes_plain_values_through() -> None:
    assert resolve_secret_reference("plain-value") == "plain-value"
    assert resolve_secret_reference(42) == 42


def test_missing_env_reference_teaches(monkeypatch) -> None:
    monkeypatch.delenv("NOPE_XYZ", raising=False)
    with pytest.raises(CredentialStorageError, match="NOPE_XYZ"):
        resolve_secret_reference("env://NOPE_XYZ")


def test_missing_keyring_reference_teaches_the_set_command(fake_keyring) -> None:
    with pytest.raises(CredentialStorageError, match="toolplane secret set"):
        resolve_secret_reference("keyring://linear-api-key")


def test_secret_round_trip_and_names_only_listing(fake_keyring) -> None:
    secret_set("linear-api-key", "lin_secret_123")

    assert secret_list() == ["linear-api-key"]
    assert resolve_secret_reference("keyring://linear-api-key") == "lin_secret_123"
    # the names index file must never hold values
    index = Path(credentials._SECRET_NAMES_FILE).read_text()
    assert "lin_secret_123" not in index

    secret_delete("linear-api-key")
    assert secret_list() == []


def test_deleting_a_missing_secret_teaches(fake_keyring) -> None:
    with pytest.raises(CredentialStorageError, match="no such secret"):
        secret_delete("never-stored")


def test_oauth_storage_key_name_is_reserved(fake_keyring) -> None:
    with pytest.raises(CredentialStorageError, match="reserved"):
        secret_set("oauth-storage-key", "x")
    with pytest.raises(CredentialStorageError, match="reserved"):
        secret_delete("oauth-storage-key")


def test_prepare_leaves_non_oauth_servers_untouched() -> None:
    config = {"command": "uvx", "args": ["some-server"]}
    assert prepare_server_config(config) == config

    bearer = {"url": "https://x.example/mcp", "headers": {"X-Plain": "v"}}
    assert prepare_server_config(bearer) == bearer
