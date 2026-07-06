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


def test_secret_names_reject_control_characters(fake_keyring) -> None:
    # "foo\nevil" passed the old guard and split into two phantom index
    # entries, leaving the real secret unmanageable (Opus finding on #95)
    for bad in ["foo\nevil", "foo bar", "foo/bar", "", "foo\tbar"]:
        with pytest.raises(CredentialStorageError, match="letters"):
            secret_set(bad, "v")
    assert secret_list() == []


def test_keyring_backend_failure_teaches_instead_of_traceback(monkeypatch) -> None:
    import keyring

    def locked(*_args):
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(keyring, "get_password", locked)
    with pytest.raises(CredentialStorageError, match="env://"):
        resolve_secret_reference("keyring://linear-api-key")


def test_probe_never_mints_a_keyring_key(monkeypatch, fake_keyring) -> None:
    # a read-only status probe must not provision a persistent OS secret
    # (Sonnet finding on #95)
    monkeypatch.delenv("TOOLPLANE_STORAGE_KEY")

    primed = asyncio.run(
        __import__("toolplane.credentials", fromlist=["has_stored_oauth_tokens"])
        .has_stored_oauth_tokens("https://x.example/mcp")
    )

    assert primed is False
    assert fake_keyring.values == {}


def test_invalid_storage_key_surfaces_instead_of_not_primed(monkeypatch) -> None:
    # a typo'd key must not masquerade as "run mcp login again"
    monkeypatch.setenv("TOOLPLANE_STORAGE_KEY", "not-a-fernet-key")
    token_dir = Path(credentials.DEFAULT_TOKEN_DIR).expanduser()
    token_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(CredentialStorageError, match="Fernet"):
        asyncio.run(
            credentials.has_stored_oauth_tokens("https://x.example/mcp")
        )


def test_register_mcp_server_prepares_dict_shapes(monkeypatch) -> None:
    # the public library path silently fell back to fastmcp's in-memory
    # OAuth, the exact failure this layer prevents (Sonnet finding on #95)
    from fastmcp.client.auth import OAuth

    from toolplane.adapters import mcp as mcp_adapter

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, server):
            captured["server"] = server

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def list_tools(self):
            return []

    monkeypatch.setattr(mcp_adapter, "_client", FakeClient)
    from toolplane import CapabilityRegistry

    asyncio.run(
        mcp_adapter.register_mcp_server(
            CapabilityRegistry(),
            "linear",
            {"url": "https://mcp.linear.app/mcp", "auth": "oauth"},
        )
    )

    assert isinstance(captured["server"]["auth"], OAuth)


def test_cli_secret_set_strips_crlf_from_piped_input(
    monkeypatch, fake_keyring, capsys
) -> None:
    # CRLF pipes stored 'token\r' — a silently corrupt credential headed
    # for an HTTP header (Opus finding on #95)
    import io

    from toolplane.cli import main

    fake_stdin = io.StringIO("token123\r\n")
    fake_stdin.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdin", fake_stdin)

    code = main(["secret", "set", "api-token"])

    assert code == 0
    assert fake_keyring.values[("toolplane", "api-token")] == "token123"


def test_trailing_slash_url_finds_the_stored_login(tmp_path, monkeypatch) -> None:
    # fastmcp keys token storage on the rstrip("/")-ed URL; a config URL
    # with a trailing slash must still read as primed (Codex re-check on
    # #95 — login-then-serve was permanently blocked for such configs)
    from fastmcp.client.auth.oauth import TokenStorageAdapter
    from mcp.shared.auth import OAuthToken

    from toolplane.credentials import has_stored_oauth_tokens, oauth_token_storage

    async def prime_and_check() -> tuple[bool, bool]:
        adapter = TokenStorageAdapter(
            async_key_value=oauth_token_storage(),
            server_url="https://x.example/mcp",
        )
        await adapter.set_tokens(
            OAuthToken(access_token="tok", token_type="Bearer")
        )
        return (
            await has_stored_oauth_tokens("https://x.example/mcp"),
            await has_stored_oauth_tokens("https://x.example/mcp/"),
        )

    exact, trailing = asyncio.run(prime_and_check())
    assert exact is True
    assert trailing is True


def test_cli_login_reports_credential_errors_as_diagnostics(
    tmp_path, monkeypatch, capsys
) -> None:
    # a headless host without key material must get "toolplane: ..." from
    # mcp login, not a RuntimeError traceback (Codex re-check on #95)
    import toolplane.mcp_lifecycle as lifecycle
    from toolplane.cli import main

    def no_key(_url: str):
        raise CredentialStorageError("no OS keyring is available")

    monkeypatch.setattr("toolplane.credentials.build_oauth", no_key)
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        '[mcp.servers.linear]\nurl = "https://mcp.linear.app/mcp"\n'
        'auth = "oauth"\n',
        encoding="utf-8",
    )

    code = main(["mcp", "login", "linear", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "toolplane: no OS keyring" in captured.err
    assert lifecycle is not None  # imported for monkeypatch scoping clarity
