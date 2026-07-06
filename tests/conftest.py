"""Shared test environment guards."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _isolated_credential_key(monkeypatch, tmp_path):
    """No test may touch the real OS keyring or the user's token store.

    The env key takes precedence over the keyring in
    credentials._load_or_create_storage_key, and the token directory is
    redirected per-test so OAuth wiring never reads or writes
    ~/.toolplane.
    """
    monkeypatch.setenv("TOOLPLANE_STORAGE_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(
        "toolplane.credentials.DEFAULT_TOKEN_DIR", str(tmp_path / "oauth")
    )
    monkeypatch.setattr(
        "toolplane.credentials._SECRET_NAMES_FILE",
        str(tmp_path / "secret-names"),
    )
