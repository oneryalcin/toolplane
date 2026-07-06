"""Shared test environment guards."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _isolated_credential_key(monkeypatch, tmp_path):
    """No test may touch the real OS keyring or the user's token store.

    The env key takes precedence over the keyring in
    credentials._load_or_create_storage_key, the token directory and
    secret-names index are redirected per-test, and the keyring functions
    themselves are replaced with an in-memory fake — so even a test that
    exercises the keyring paths directly (secret_set, keyring:// refs)
    can never read or write the developer's real keychain (reviewer
    finding on #95: the guarantee previously relied on each test opting
    into a fake).
    """
    monkeypatch.setenv("TOOLPLANE_STORAGE_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(
        "toolplane.credentials.DEFAULT_TOKEN_DIR", str(tmp_path / "oauth")
    )
    monkeypatch.setattr(
        "toolplane.credentials._SECRET_NAMES_FILE",
        str(tmp_path / "secret-names"),
    )

    import keyring
    import keyring.errors

    values: dict[tuple[str, str], str] = {}

    def fake_get(service: str, name: str) -> str | None:
        return values.get((service, name))

    def fake_set(service: str, name: str, value: str) -> None:
        values[(service, name)] = value

    def fake_delete(service: str, name: str) -> None:
        if (service, name) not in values:
            raise keyring.errors.PasswordDeleteError(name)
        del values[(service, name)]

    monkeypatch.setattr(keyring, "get_password", fake_get)
    monkeypatch.setattr(keyring, "set_password", fake_set)
    monkeypatch.setattr(keyring, "delete_password", fake_delete)
