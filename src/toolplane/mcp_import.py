"""Import MCP server configs from other clients into a Toolplane TOML.

Sources are read-only: import never mutates the client configs it reads.
Secret-bearing values are never copied into the TOML as literals — they
become ``env://`` or ``keyring://`` references (see #97), and discovered
secrets are moved into the OS keyring via the same machinery as
``toolplane secret set``.

Trust boundary: ``.mcp.json`` (and anything else under a cloned repo) is
attacker-influenced input. Values that already look like toolplane secret
references are refused outright — importing them would let a hostile
config choose which local secret gets attached to which remote server.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

from .config_edit import (
    ConfigEditError,
    ensure_table,
    parse_config_document,
    write_text_atomic,
)
from .credentials import (
    OAUTH_KEY_NAME,
    CredentialStorageError,
    secret_peek,
    secret_set,
)
from .mcp_lifecycle import _SERVER_NAME


class McpImportError(ConfigEditError):
    """Raised when an import source cannot be read at all."""


_SECRET_KEY_HINT = re.compile(
    r"(key|token|secret|password|credential|authorization)", re.IGNORECASE
)
# A value that looks like a bare credential: long, no whitespace, mixed
# letters and digits, and not a path or URL. Paths ("/...") and URLs keep
# their slashes, which this deliberately excludes; false positives only
# cost a keyring indirection, false negatives copy a secret in plaintext.
_SECRET_VALUE_HINT = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_\-.=+]{20,}$")
_REMOTE_BRIDGE_BASENAMES = frozenset({"mcp-remote", "fastmcp-remote"})
_SECRET_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")
_SERVER_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
_REF_PREFIXES = ("env://", "keyring://")
_TRANSPORT_TYPES = frozenset({"http", "sse", "streamable-http"})


@dataclass
class PlannedServer:
    """One server ready to be written, plus everything the user must know."""

    name: str
    source: str
    config: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    secrets_to_store: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class SkippedServer:
    name: str
    source: str
    reason: str


@dataclass
class ImportReport:
    imported: list[PlannedServer] = field(default_factory=list)
    skipped: list[SkippedServer] = field(default_factory=list)
    dry_run: bool = False
    config_path: Path | None = None


@dataclass(frozen=True)
class _RawServer:
    name: str
    source: str
    mapping: Mapping[str, Any]
    disabled_reason: str | None = None


def import_mcp_servers(
    config_path: str | Path,
    source: str,
    *,
    home: Path | None = None,
    project_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
    dry_run: bool = False,
    force: bool = False,
    plaintext: bool = False,
    verbatim: bool = False,
) -> ImportReport:
    """Import MCP servers from ``claude`` or ``codex`` into the config file."""
    home = home or Path.home()
    project_dir = project_dir or Path.cwd()
    environ = os.environ if environ is None else environ

    if source == "claude":
        raw_servers = _discover_claude_servers(home, project_dir)
    elif source == "codex":
        raw_servers = _discover_codex_servers(home)
    else:
        raise McpImportError(f"Unknown import source: {source!r}")

    report = ImportReport(dry_run=dry_run, config_path=Path(config_path).expanduser())
    document = parse_config_document(report.config_path)
    servers_table = ensure_table(ensure_table(document, "mcp"), "servers")

    seen: dict[str, str] = {}
    secret_names_used: set[str] = set()
    changed = False
    for raw in raw_servers:
        name = _sanitize_server_name(raw.name)
        if name is None:
            report.skipped.append(
                SkippedServer(raw.name, raw.source, "name has no usable characters")
            )
            continue
        if name in seen:
            report.skipped.append(
                SkippedServer(
                    name, raw.source, f"shadowed by the same name from {seen[name]}"
                )
            )
            continue
        seen[name] = raw.source
        if raw.disabled_reason is not None:
            report.skipped.append(
                SkippedServer(name, raw.source, raw.disabled_reason)
            )
            continue
        if name in servers_table and not force:
            report.skipped.append(
                SkippedServer(
                    name, raw.source, "already configured (--force to replace)"
                )
            )
            continue

        try:
            planned = _convert_server(
                name,
                raw,
                environ=environ,
                plaintext=plaintext,
                verbatim=verbatim,
                secret_names_used=secret_names_used,
            )
        except McpImportError as exc:
            report.skipped.append(SkippedServer(name, raw.source, str(exc)))
            continue
        if name != raw.name:
            planned.notes.append(
                f"renamed from {_printable(raw.name)!r} (invalid characters)"
            )

        report.imported.append(planned)
        servers_table[name] = _build_server_table(planned)
        changed = True

    if changed and not dry_run:
        # config first, secrets second: a failed secret store leaves refs
        # that fail loudly at use with the fixing command, while the
        # reverse order left orphaned keyring entries nothing references
        # (reviewer finding on #97)
        write_text_atomic(report.config_path, tomlkit.dumps(document))
        _store_planned_secrets(report.imported)
    return report


def _store_planned_secrets(planned_servers: Sequence[PlannedServer]) -> None:
    pending = [
        (secret_name, secret_value)
        for planned in planned_servers
        for secret_name, secret_value in planned.secrets_to_store
    ]
    for index, (secret_name, secret_value) in enumerate(pending):
        try:
            secret_set(secret_name, secret_value)
        except CredentialStorageError as exc:
            remaining = ", ".join(name for name, _ in pending[index:])
            raise CredentialStorageError(
                f"{exc} — the config was written but these secrets were "
                f"not stored: {remaining}; store each with: "
                "toolplane secret set <name>"
            ) from exc


# --- discovery ---------------------------------------------------------------


def _discover_claude_servers(home: Path, project_dir: Path) -> list[_RawServer]:
    """Claude Code servers, most specific scope first (first name wins)."""
    claude_json = home / ".claude.json"
    project_mcp_json = project_dir / ".mcp.json"
    if not claude_json.exists() and not project_mcp_json.exists():
        raise McpImportError(
            "no Claude Code config found "
            f"(looked for {claude_json} and {project_mcp_json})"
        )

    servers: list[_RawServer] = []
    data: Mapping[str, Any] = {}
    disabled_in_project: frozenset[str] = frozenset()
    if claude_json.exists():
        data = _read_json_mapping(claude_json)
        project_entry = _project_entry(data.get("projects"), project_dir)
        if project_entry is not None:
            servers.extend(
                _raw_servers_from(
                    project_entry.get("mcpServers"),
                    f"claude project scope ({claude_json})",
                )
            )
            disabled = project_entry.get("disabledMcpjsonServers")
            if isinstance(disabled, Sequence) and not isinstance(disabled, str):
                disabled_in_project = frozenset(str(item) for item in disabled)
    if project_mcp_json.exists():
        for raw in _raw_servers_from(
            _read_json_mapping(project_mcp_json).get("mcpServers"),
            f"claude project file ({project_mcp_json})",
        ):
            if raw.name in disabled_in_project:
                raw = _RawServer(
                    name=raw.name,
                    source=raw.source,
                    mapping=raw.mapping,
                    disabled_reason="disabled in Claude Code for this project",
                )
            servers.append(raw)
    if data:
        servers.extend(
            _raw_servers_from(
                data.get("mcpServers"), f"claude user scope ({claude_json})"
            )
        )
    return servers


def _project_entry(projects: Any, project_dir: Path) -> Mapping[str, Any] | None:
    """Find the projects entry for this directory, surviving symlinks.

    macOS keys these under either form (/tmp vs /private/tmp), so compare
    resolved paths instead of string-matching (reviewer finding on #97).
    """
    if not isinstance(projects, Mapping):
        return None
    target = project_dir.resolve()
    for key, entry in projects.items():
        if not isinstance(entry, Mapping):
            continue
        try:
            if Path(str(key)).resolve() == target:
                return entry
        except OSError:
            continue
    return None


def _discover_codex_servers(home: Path) -> list[_RawServer]:
    codex_toml = home / ".codex" / "config.toml"
    if not codex_toml.exists():
        raise McpImportError(f"no Codex config found (looked for {codex_toml})")
    try:
        data = tomllib.loads(codex_toml.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise McpImportError(f"could not read {codex_toml}: {exc}") from exc
    servers = _raw_servers_from(data.get("mcp_servers"), f"codex ({codex_toml})")
    return [
        _RawServer(
            name=raw.name,
            source=raw.source,
            mapping=raw.mapping,
            disabled_reason=(
                "disabled in Codex config"
                if raw.mapping.get("enabled") is False
                else None
            ),
        )
        for raw in servers
    ]


def _read_json_mapping(path: Path) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpImportError(f"could not read {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise McpImportError(f"{path} is not a JSON object")
    return data


def _raw_servers_from(value: Any, source: str) -> list[_RawServer]:
    if not isinstance(value, Mapping):
        return []
    return [
        _RawServer(name=str(name), source=source, mapping=mapping)
        for name, mapping in value.items()
        if isinstance(mapping, Mapping)
    ]


# --- conversion --------------------------------------------------------------

_CLAUDE_KNOWN_KEYS = frozenset({"type", "command", "args", "env", "url", "headers"})
_CODEX_KNOWN_KEYS = frozenset(
    {
        "command",
        "args",
        "env",
        "url",
        "headers",
        "http_headers",
        "env_http_headers",
        "bearer_token",
        "bearer_token_env_var",
        "enabled",
    }
)


def _convert_server(
    name: str,
    raw: _RawServer,
    *,
    environ: Mapping[str, str],
    plaintext: bool,
    verbatim: bool,
    secret_names_used: set[str],
) -> PlannedServer:
    planned = PlannedServer(name=name, source=raw.source, config={})
    mapping = raw.mapping
    _refuse_preformed_refs(mapping)
    is_codex = raw.source.startswith("codex")
    known = _CODEX_KNOWN_KEYS if is_codex else _CLAUDE_KNOWN_KEYS
    for key in mapping:
        if key not in known:
            planned.notes.append(
                f"dropped {_printable(str(key))!r} (no toolplane equivalent)"
            )

    url = mapping.get("url")
    command = mapping.get("command")
    if isinstance(url, str) and url.strip():
        _convert_url_server(planned, mapping, environ=environ, plaintext=plaintext,
                            secret_names_used=secret_names_used, is_codex=is_codex)
        return planned

    if not isinstance(command, str) or not command.strip():
        raise McpImportError("entry has neither a command nor a url")

    args = mapping.get("args")
    if isinstance(args, str):
        args = [args]
    elif isinstance(args, Sequence):
        args = [str(a) for a in args]
    else:
        args = []

    env = mapping.get("env")
    if not verbatim and not env:
        # a wrapper with env vars may be feeding the bridge configuration
        # the rewrite would lose — those keep the wrapper verbatim
        bridged = _rewrite_remote_bridge(command, args, planned)
        if bridged:
            _note_unused_branch_keys(
                planned, mapping, used=("command", "args", "type", "enabled")
            )
            return planned

    planned.config["command"] = command
    if args:
        planned.config["args"] = args
    if isinstance(env, Mapping) and env:
        cleaned: dict[str, str] = {}
        for key, value in env.items():
            if value is None:
                planned.notes.append(
                    f"dropped env {_printable(str(key))!r} (null value)"
                )
                continue
            cleaned[str(key)] = str(value)
        if cleaned:
            planned.config["env"] = _reference_secrets(
                cleaned,
                planned,
                environ=environ,
                plaintext=plaintext,
                secret_names_used=secret_names_used,
            )
    _note_unused_branch_keys(
        planned, mapping, used=("command", "args", "env", "type", "enabled")
    )
    return planned


def _convert_url_server(
    planned: PlannedServer,
    mapping: Mapping[str, Any],
    *,
    environ: Mapping[str, str],
    plaintext: bool,
    secret_names_used: set[str],
    is_codex: bool,
) -> None:
    planned.config["url"] = mapping["url"]
    transport = mapping.get("type")
    if transport in _TRANSPORT_TYPES:
        # explicit source transport survives; without it fastmcp guesses
        # from the URL path and misreads genuinely-http ".../sse..." URLs
        planned.config["transport"] = transport

    headers: dict[str, Any] = {}
    for headers_key in ("headers", "http_headers"):
        value = mapping.get(headers_key)
        if isinstance(value, Mapping):
            headers.update({str(k): v for k, v in value.items()})
    env_headers = mapping.get("env_http_headers")
    if is_codex and isinstance(env_headers, Mapping):
        # codex shape: header name -> env var NAME; we construct the ref
        for header_name, env_var in env_headers.items():
            headers[str(header_name)] = f"env://{env_var}"
            planned.notes.append(
                f"{_printable(str(header_name))}: wrote env://{env_var} "
                "(from env_http_headers)"
            )
    if headers:
        planned.config["headers"] = _reference_secrets(
            headers,
            planned,
            environ=environ,
            plaintext=plaintext,
            secret_names_used=secret_names_used,
        )

    auth_wired = False
    if is_codex:
        bearer_env_var = mapping.get("bearer_token_env_var")
        bearer_token = mapping.get("bearer_token")
        if isinstance(bearer_env_var, str) and bearer_env_var:
            planned.config["auth"] = f"env://{bearer_env_var}"
            planned.notes.append(
                f"bearer_token_env_var: wrote auth = env://{bearer_env_var} "
                "(fastmcp sends it as a Bearer token)"
            )
            auth_wired = True
        elif isinstance(bearer_token, str) and bearer_token:
            secret_name, needs_store = _unique_secret_name(
                _secret_name_for(planned.name, "bearer-token"),
                bearer_token,
                secret_names_used,
            )
            planned.config["auth"] = f"keyring://{secret_name}"
            if needs_store:
                planned.secrets_to_store.append((secret_name, bearer_token))
                planned.notes.append(
                    "bearer_token: moved to your OS keyring "
                    f"(wrote auth = keyring://{secret_name})"
                )
            else:
                planned.notes.append(
                    f"bearer_token: reused existing keyring secret "
                    f"{secret_name!r} (same value already stored)"
                )
            auth_wired = True

    if not headers and not auth_wired:
        planned.next_steps.append(
            f"toolplane mcp status {planned.name}  # if it reports "
            f"auth_required: toolplane mcp login {planned.name}"
        )
    _note_unused_branch_keys(
        planned,
        mapping,
        used=(
            "url",
            "type",
            "headers",
            "http_headers",
            "env_http_headers",
            "bearer_token",
            "bearer_token_env_var",
            "enabled",
        ),
    )


def _note_unused_branch_keys(
    planned: PlannedServer,
    mapping: Mapping[str, Any],
    *,
    used: tuple[str, ...],
) -> None:
    """Known keys outside the chosen branch drop data — say so.

    A url entry that also carries env, or a command entry that also
    carries headers, previously lost those fields with no report line
    (reviewer finding on #97). Unknown keys were already noted.
    """
    known = _CLAUDE_KNOWN_KEYS | _CODEX_KNOWN_KEYS
    for key in mapping:
        if key in used or key not in known:
            continue
        if mapping.get(key) in (None, "", [], {}):
            continue
        planned.notes.append(
            f"dropped {_printable(str(key))!r} "
            "(not meaningful for this server shape)"
        )


def _refuse_preformed_refs(mapping: Mapping[str, Any], _path: str = "") -> None:
    """Fail closed on source values that are already secret references.

    A hostile checked-in ``.mcp.json`` could otherwise name any local
    keyring entry or env var (``keyring://oauth-storage-key``, ``env://
    AWS_SECRET_ACCESS_KEY``) and pick the remote server it gets sent to
    (reviewer finding on #97). References the importer itself constructs
    are added after this check.
    """
    for key, value in mapping.items():
        location = f"{_path}.{key}" if _path else str(key)
        if isinstance(value, Mapping):
            _refuse_preformed_refs(value, location)
        elif isinstance(value, str) and value.startswith(_REF_PREFIXES):
            raise McpImportError(
                f"refusing to import: {_printable(location)} already "
                "contains a toolplane secret reference "
                f"({_printable(value.split('://', 1)[0])}://...); client "
                "configs never legitimately hold these — add the entry to "
                "toolplane.toml yourself if it is intentional"
            )


def _rewrite_remote_bridge(
    command: str,
    args: Sequence[str],
    planned: PlannedServer,
) -> bool:
    """Rewrite an mcp-remote/fastmcp-remote wrapper to a direct url entry.

    The wrapper's only reason to exist is remote OAuth, so the rewritten
    entry confidently gets ``auth = "oauth"`` — unlike plain url imports,
    where the source config carries no auth signal.
    """
    bridge = next(
        (
            _basename(part)
            for part in (command, *args)
            if _basename(part) in _REMOTE_BRIDGE_BASENAMES
        ),
        None,
    )
    if bridge is None:
        return False
    urls = [part for part in args if part.startswith(("http://", "https://"))]
    if not urls:
        planned.notes.append(
            "looks like an mcp-remote bridge but no URL argument was found; "
            "kept verbatim"
        )
        return False
    extra_args = [
        part
        for part in args
        if part not in urls
        and _basename(part) not in _REMOTE_BRIDGE_BASENAMES
        and part not in ("-y", "--yes")
    ]
    if len(urls) > 1 or extra_args:
        planned.notes.append(
            "mcp-remote bridge carries extra arguments the rewrite would "
            "drop; kept verbatim (--verbatim silences this)"
        )
        return False
    planned.config["url"] = urls[0]
    planned.config["auth"] = "oauth"
    planned.notes.append(
        f"rewrote {bridge} wrapper to a direct url entry "
        "(--verbatim to keep the wrapper)"
    )
    planned.next_steps.append(f"toolplane mcp login {planned.name}")
    return True


def _basename(value: str) -> str:
    return value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _reference_secrets(
    values: dict[str, Any],
    planned: PlannedServer,
    *,
    environ: Mapping[str, str],
    plaintext: bool,
    secret_names_used: set[str],
) -> dict[str, Any]:
    if plaintext:
        return values
    referenced: dict[str, Any] = {}
    for key, value in values.items():
        if not isinstance(value, str) or not _looks_secret(key, value):
            referenced[key] = value
            continue
        if value.startswith(_REF_PREFIXES):
            # constructed by the importer itself (env_http_headers);
            # source-provided refs were refused before conversion
            referenced[key] = value
            continue
        env_var = _matching_env_var(key, value, environ)
        if env_var is not None:
            referenced[key] = f"env://{env_var}"
            planned.notes.append(
                f"{_printable(key)}: wrote env://{env_var} instead of the "
                "literal value"
            )
            continue
        secret_name, needs_store = _unique_secret_name(
            _secret_name_for(planned.name, key), value, secret_names_used
        )
        referenced[key] = f"keyring://{secret_name}"
        if needs_store:
            planned.secrets_to_store.append((secret_name, value))
            planned.notes.append(
                f"{_printable(key)}: moved to your OS keyring as "
                f"{secret_name!r} (wrote keyring://{secret_name})"
            )
        else:
            planned.notes.append(
                f"{_printable(key)}: reused existing keyring secret "
                f"{secret_name!r} (same value already stored)"
            )
    return referenced


def _unique_secret_name(
    base: str,
    value: str,
    secret_names_used: set[str],
) -> tuple[str, bool]:
    """``(name, needs_store)`` — never silently overwrites a secret.

    A candidate holding the identical value is reused without a store;
    a candidate holding a DIFFERENT value is suffixed past (``-2``,
    ``-3``, ...), as are names already claimed this run (reviewer finding
    on #97: two servers can derive the same name, and an import could
    clobber a secret the user set themselves).
    """
    candidate = base
    counter = 2
    while True:
        if candidate not in secret_names_used:
            existing = secret_peek(candidate)
            if existing == value:
                secret_names_used.add(candidate)
                return candidate, False
            if existing is None:
                secret_names_used.add(candidate)
                return candidate, True
        candidate = f"{base}-{counter}"
        counter += 1


def _looks_secret(key: str, value: str) -> bool:
    if not value:
        return False
    if _SECRET_KEY_HINT.search(key):
        return True
    return bool(_SECRET_VALUE_HINT.fullmatch(value))


def _matching_env_var(key: str, value: str, environ: Mapping[str, str]) -> str | None:
    if environ.get(key) == value:
        return key
    # reverse scan: any var holding this exact value. If several do, the
    # first wins — the ref still resolves to the right bytes either way.
    for var, env_value in environ.items():
        if env_value == value:
            return var
    return None


def _secret_name_for(server: str, key: str) -> str:
    slug = _SECRET_NAME_CHARS.sub("-", key.lower()).strip("-.")
    name = f"{server}-{slug}" if slug else server
    if name == OAUTH_KEY_NAME:  # reserved: the OAuth token encryption key
        name = f"{name}-imported"
    return name


def _sanitize_server_name(name: str) -> str | None:
    cleaned = _SERVER_NAME_CHARS.sub("-", name).strip("-")
    if not cleaned or not _SERVER_NAME.fullmatch(cleaned):
        return None
    return cleaned


def _printable(value: str) -> str:
    """Control characters must not reach the report (display spoofing)."""
    return re.sub(r"[\x00-\x1f\x7f]", lambda m: repr(m.group())[1:-1], value)


# --- rendering ---------------------------------------------------------------


def _build_server_table(planned: PlannedServer) -> tomlkit.items.Table:
    server = tomlkit.table()
    for key, value in planned.config.items():
        if isinstance(value, Mapping):
            inline = tomlkit.inline_table()
            inline.update(value)
            server[key] = inline
        else:
            server[key] = value
    if planned.config.get("auth") == "oauth":
        server.add(
            tomlkit.comment(
                "tokens are stored encrypted at rest (key in your OS keyring);"
            )
        )
        server.add(
            tomlkit.comment(f"prime once with: toolplane mcp login {planned.name}")
        )
    return server


def format_import_report(report: ImportReport) -> str:
    lines: list[str] = []
    verb = "would import" if report.dry_run else "imported"
    for planned in report.imported:
        summary = " ".join(
            f"{key}={_printable(str(value))}"
            for key, value in planned.config.items()
            if key in ("url", "command", "auth")
        )
        lines.append(
            f"{verb} {planned.name} ({planned.source}) -> {summary}"
        )
        lines.extend(f"  note: {note}" for note in planned.notes)
        if report.dry_run:
            lines.extend(
                f"  would store secret {name!r} in your OS keyring"
                for name, _ in planned.secrets_to_store
            )
    for skipped in report.skipped:
        lines.append(
            f"skipped {_printable(skipped.name)} ({skipped.source}): "
            f"{skipped.reason}"
        )

    next_steps = [step for planned in report.imported for step in planned.next_steps]
    if next_steps and not report.dry_run:
        lines.append("next steps:")
        lines.extend(f"  {step}" for step in next_steps)

    total_notes = sum(len(planned.notes) for planned in report.imported)
    lines.append(
        f"summary: {'(dry-run) ' if report.dry_run else ''}"
        f"imported {len(report.imported)}, skipped {len(report.skipped)}, "
        f"notes {total_notes}"
    )
    if report.imported and not report.dry_run:
        lines.append(f"wrote {report.config_path}")
    return "\n".join(lines) + "\n"
