"""Encrypted credential handling, delegated to fastmcp's storage stack.

Toolplane writes no token-handling code and rolls no crypto: OAuth flows,
refresh, persistence, and Fernet encryption all belong to fastmcp and its
key-value layer. This module only assembles them — and manages the one
piece fastmcp does not: where the encryption key lives (the OS keyring,
with an env override for headless hosts).

Static secrets follow the same rule: config values may reference
``env://VAR`` or ``keyring://NAME`` so ``toolplane.toml`` never holds
secret material.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .errors import ToolplaneError

KEYRING_SERVICE = "toolplane"
OAUTH_KEY_NAME = "oauth-storage-key"
STORAGE_KEY_ENV = "TOOLPLANE_STORAGE_KEY"
DEFAULT_TOKEN_DIR = "~/.toolplane/oauth"
_SECRET_NAMES_FILE = "~/.toolplane/secret-names"


class CredentialStorageError(ToolplaneError, RuntimeError):
    """No safe place to keep the encryption key — never fall back to plaintext.

    Dual-inherits ToolplaneError so every CLI catch site that already
    handles the house taxonomy reports it as a diagnostic, not a
    traceback (Codex re-check finding on #95).
    """


def _load_or_create_storage_key() -> str:
    """Fernet key for token storage: env override, else OS keyring.

    A lost key is self-healing (encrypted values read as missing, so the
    next login just re-prompts), but a missing keyring must fail loudly
    BEFORE anything is written — silence here would mean plaintext.
    """
    env_key = os.environ.get(STORAGE_KEY_ENV)
    if env_key:
        return env_key
    try:
        import keyring

        key = keyring.get_password(KEYRING_SERVICE, OAUTH_KEY_NAME)
        if key is None:
            from cryptography.fernet import Fernet

            key = Fernet.generate_key().decode()
            keyring.set_password(KEYRING_SERVICE, OAUTH_KEY_NAME, key)
        return key
    except CredentialStorageError:
        raise
    except Exception as exc:
        raise CredentialStorageError(
            "no OS keyring is available to hold the token-encryption key; "
            f"set {STORAGE_KEY_ENV} to a Fernet key (python -c \"from "
            "cryptography.fernet import Fernet; print(Fernet.generate_key()"
            '.decode())") for headless hosts'
        ) from exc


def oauth_token_storage(directory: str | os.PathLike[str] | None = None) -> Any:
    """Encrypted at-rest token store: fastmcp's own wrapper over disk files."""
    from cryptography.fernet import Fernet
    from key_value.aio.stores.filetree import (
        FileTreeStore,
        FileTreeV1CollectionSanitizationStrategy,
        FileTreeV1KeySanitizationStrategy,
    )
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    token_dir = Path(directory or DEFAULT_TOKEN_DIR).expanduser()
    token_dir.mkdir(parents=True, exist_ok=True)
    store = FileTreeStore(
        data_directory=token_dir,
        # required: URL-shaped keys crash FileTreeStore without sanitization
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(token_dir),
        collection_sanitization_strategy=(
            FileTreeV1CollectionSanitizationStrategy(token_dir)
        ),
    )
    return FernetEncryptionWrapper(
        key_value=store, fernet=Fernet(_load_or_create_storage_key())
    )


def build_oauth(url: str) -> Any:
    """fastmcp OAuth helper wired to Toolplane's encrypted token store."""
    from fastmcp.client.auth import OAuth

    return OAuth(
        mcp_url=url,
        token_storage=oauth_token_storage(),
        client_name="toolplane",
    )


def _peek_storage_key() -> str | None:
    """Read-only key lookup for probe paths: never generates, never writes.

    A diagnostic command must not mint a persistent OS-level secret as a
    side effect (reviewer finding on #95) — get-or-create is reserved for
    paths that are about to store something.
    """
    env_key = os.environ.get(STORAGE_KEY_ENV)
    if env_key:
        return env_key
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, OAUTH_KEY_NAME)
    except Exception:
        return None


async def has_stored_oauth_tokens(url: str) -> bool:
    """Whether a prior login left tokens for this server.

    Read-only in every sense: no key is created, no directory is written.
    Read through fastmcp's own TokenStorageAdapter (same naming, same
    store) so this can never drift from where fastmcp actually keeps them.
    A missing key/dir just means "not primed"; an INVALID key raises so
    callers can surface the real problem instead of re-teaching login.
    """
    # fastmcp trims the server URL before keying its token storage
    # (oauth.py: mcp_url.rstrip("/")) — a trailing-slash config URL must
    # find the same record login stored (Codex re-check finding on #95)
    url = url.rstrip("/")
    token_dir = Path(DEFAULT_TOKEN_DIR).expanduser()
    key = _peek_storage_key()
    if key is None or not token_dir.is_dir():
        return False
    from cryptography.fernet import Fernet
    from fastmcp.client.auth.oauth import TokenStorageAdapter
    from key_value.aio.stores.filetree import (
        FileTreeStore,
        FileTreeV1CollectionSanitizationStrategy,
        FileTreeV1KeySanitizationStrategy,
    )
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    try:
        fernet = Fernet(key)
    except (ValueError, TypeError) as exc:
        raise CredentialStorageError(
            f"the configured token-encryption key is not a valid Fernet "
            f"key ({exc}); check {STORAGE_KEY_ENV} or the OS keyring entry"
        ) from exc
    storage = FernetEncryptionWrapper(
        key_value=FileTreeStore(
            data_directory=token_dir,
            key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(
                token_dir
            ),
            collection_sanitization_strategy=(
                FileTreeV1CollectionSanitizationStrategy(token_dir)
            ),
        ),
        fernet=fernet,
    )
    try:
        adapter = TokenStorageAdapter(async_key_value=storage, server_url=url)
        return await adapter.get_tokens() is not None
    except Exception:
        # unreadable/corrupt records mean "not primed"; the next login
        # overwrites them
        return False


def resolve_secret_reference(value: Any) -> Any:
    """Resolve ``env://VAR`` and ``keyring://NAME`` config values.

    Anything else passes through untouched. Missing secrets fail loudly
    with the command that fixes them — a silently-empty credential is a
    debugging session, not a fallback.
    """
    if not isinstance(value, str):
        return value
    if value.startswith("env://"):
        name = value[len("env://") :]
        resolved = os.environ.get(name)
        if resolved is None:
            raise CredentialStorageError(
                f"config references env://{name} but ${name} is not set"
            )
        return resolved
    if value.startswith("keyring://"):
        name = value[len("keyring://") :]
        import keyring

        try:
            resolved = keyring.get_password(KEYRING_SERVICE, name)
        except Exception as exc:
            # backend unavailable/locked must be an actionable Toolplane
            # error, not a dependency traceback (Codex finding on #95)
            raise CredentialStorageError(
                f"config references keyring://{name} but the OS keyring "
                f"backend failed ({exc.__class__.__name__}); configure a "
                "keyring backend or switch the reference to env://"
            ) from exc
        if resolved is None:
            raise CredentialStorageError(
                f"config references keyring://{name} but no such secret is "
                f"stored — run: toolplane secret set {name}"
            )
        return resolved
    return value


def resolve_config_secret_references(
    server_config: dict[str, Any],
) -> dict[str, Any]:
    """Resolve secret references in headers/env/auth (never ``"oauth"``).

    Split out of :func:`prepare_server_config` so browser-free lifecycle
    probes (mcp status) can resolve secrets identically to serve/execute
    without constructing OAuth (Codex finding on #95: probes previously
    sent literal ``env://`` strings upstream).

    A reference in ``auth`` resolves to a plain string, which fastmcp
    treats as a Bearer token — the shape ``mcp import --from codex``
    writes for ``bearer_token_env_var`` servers (#97). The literal
    ``"oauth"`` is not a reference and passes through for OAuth wiring.
    """
    prepared = dict(server_config)
    headers = prepared.get("headers")
    if isinstance(headers, dict):
        prepared["headers"] = {
            key: resolve_secret_reference(item) for key, item in headers.items()
        }
    env = prepared.get("env")
    if isinstance(env, dict):
        prepared["env"] = {
            key: resolve_secret_reference(item) for key, item in env.items()
        }
    auth = prepared.get("auth")
    if isinstance(auth, str) and auth.startswith(("env://", "keyring://")):
        prepared["auth"] = resolve_secret_reference(auth)
    return prepared


def prepare_server_config(server_config: dict[str, Any]) -> dict[str, Any]:
    """Make one mcpServers entry runnable: secrets resolved, OAuth wired.

    - ``url`` + ``auth = "oauth"`` gets a live fastmcp OAuth object backed
      by the encrypted token store (fastmcp's MCPConfig accepts httpx.Auth
      instances in the auth slot).
    - ``headers`` and stdio ``env`` values may be secret references.

    Idempotent: an already-prepared config passes through unchanged.
    """
    prepared = resolve_config_secret_references(server_config)
    if prepared.get("url") and prepared.get("auth") == "oauth":
        prepared["auth"] = build_oauth(str(prepared["url"]))
    return prepared


# --- static secret management (`toolplane secret ...`) ---


def _names_index_path() -> Path:
    return Path(_SECRET_NAMES_FILE).expanduser()


def _read_names() -> list[str]:
    path = _names_index_path()
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line.strip()]


def _write_names(names: list[str]) -> None:
    from .config_edit import write_text_atomic

    path = _names_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, "".join(f"{name}\n" for name in sorted(set(names))))


# same conservative charset as MCP server names: a name that survives the
# newline-delimited index, keyring backends, and keyring:// references
_SECRET_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _validate_secret_name(name: str) -> None:
    if not _SECRET_NAME.fullmatch(name):
        raise CredentialStorageError(
            "secret names must contain only letters, numbers, dots, "
            "underscores, and hyphens"
        )
    if name == OAUTH_KEY_NAME:
        raise CredentialStorageError(
            f"{OAUTH_KEY_NAME!r} is reserved — it is the OAuth token "
            "encryption key; changing it would orphan every stored token"
        )


def secret_peek(name: str) -> str | None:
    """Read a stored secret, or None. Never creates or prompts.

    Exists for collision checks (mcp import must not silently overwrite a
    secret the user stored themselves — reviewer finding on #97); it is
    not a general read surface, and values must never be printed.
    """
    import keyring

    _validate_secret_name(name)
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception as exc:
        raise CredentialStorageError(
            f"the OS keyring backend failed ({exc.__class__.__name__})"
        ) from exc


def secret_set(name: str, value: str) -> None:
    import keyring

    _validate_secret_name(name)
    try:
        keyring.set_password(KEYRING_SERVICE, name, value)
    except Exception as exc:
        raise CredentialStorageError(
            f"the OS keyring backend refused the write "
            f"({exc.__class__.__name__}); configure a keyring backend "
            "before storing secrets"
        ) from exc
    _write_names([*_read_names(), name])


def secret_delete(name: str) -> None:
    import keyring

    _validate_secret_name(name)
    try:
        keyring.delete_password(KEYRING_SERVICE, name)
    except keyring.errors.PasswordDeleteError as exc:
        raise CredentialStorageError(f"no such secret: {name}") from exc
    except Exception as exc:
        raise CredentialStorageError(
            f"the OS keyring backend failed ({exc.__class__.__name__})"
        ) from exc
    _write_names([entry for entry in _read_names() if entry != name])


def secret_list() -> list[str]:
    """Names only — values never leave the keyring through this surface."""
    return _read_names()
