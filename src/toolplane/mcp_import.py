"""Import MCP server configs from other clients into a Toolplane TOML.

Sources are read-only: import never mutates the client configs it reads.
Secret-bearing values are never copied into the TOML as literals — they
become ``env://`` or ``keyring://`` references (see #97), and discovered
secrets are moved into the OS keyring via the same machinery as
``toolplane secret set``.
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
from .credentials import secret_set
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
            )
        except McpImportError as exc:
            report.skipped.append(SkippedServer(name, raw.source, str(exc)))
            continue
        if name != raw.name:
            planned.notes.append(f"renamed from {raw.name!r} (invalid characters)")

        report.imported.append(planned)
        servers_table[name] = _build_server_table(planned)
        changed = True

    if changed and not dry_run:
        for planned in report.imported:
            for secret_name, secret_value in planned.secrets_to_store:
                secret_set(secret_name, secret_value)
        write_text_atomic(report.config_path, tomlkit.dumps(document))
    return report


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
    if claude_json.exists():
        data = _read_json_mapping(claude_json)
        projects = data.get("projects")
        if isinstance(projects, Mapping):
            for key in {str(project_dir), str(project_dir.resolve())}:
                entry = projects.get(key)
                if isinstance(entry, Mapping):
                    servers.extend(
                        _raw_servers_from(
                            entry.get("mcpServers"),
                            f"claude project scope ({claude_json})",
                        )
                    )
    if project_mcp_json.exists():
        servers.extend(
            _raw_servers_from(
                _read_json_mapping(project_mcp_json).get("mcpServers"),
                f"claude project file ({project_mcp_json})",
            )
        )
    if data:
        servers.extend(
            _raw_servers_from(
                data.get("mcpServers"), f"claude user scope ({claude_json})"
            )
        )
    return servers


def _discover_codex_servers(home: Path) -> list[_RawServer]:
    codex_toml = home / ".codex" / "config.toml"
    if not codex_toml.exists():
        raise McpImportError(f"no Codex config found (looked for {codex_toml})")
    try:
        data = tomllib.loads(codex_toml.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise McpImportError(f"could not read {codex_toml}: {exc}") from exc
    return _raw_servers_from(data.get("mcp_servers"), f"codex ({codex_toml})")


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

_CLAUDE_KNOWN_KEYS = frozenset(
    {"type", "command", "args", "env", "url", "headers"}
)
_CODEX_KNOWN_KEYS = frozenset({"command", "args", "env", "url", "headers"})


def _convert_server(
    name: str,
    raw: _RawServer,
    *,
    environ: Mapping[str, str],
    plaintext: bool,
    verbatim: bool,
) -> PlannedServer:
    planned = PlannedServer(name=name, source=raw.source, config={})
    mapping = raw.mapping
    known = _CLAUDE_KNOWN_KEYS if "claude" in raw.source else _CODEX_KNOWN_KEYS
    for key in mapping:
        if key not in known:
            planned.notes.append(f"dropped {key!r} (no toolplane equivalent)")

    url = mapping.get("url")
    command = mapping.get("command")
    if isinstance(url, str) and url.strip():
        planned.config["url"] = url
        transport = mapping.get("type")
        if transport == "sse":
            planned.config["transport"] = "sse"
        headers = mapping.get("headers")
        if isinstance(headers, Mapping) and headers:
            planned.config["headers"] = _reference_secrets(
                dict(headers), planned, environ=environ, plaintext=plaintext
            )
        else:
            planned.next_steps.append(
                f"toolplane mcp status {name}  # if it reports auth_required: "
                f"toolplane mcp login {name}"
            )
        return planned

    if not isinstance(command, str) or not command.strip():
        raise McpImportError("entry has neither a command nor a url")

    args = mapping.get("args")
    args = [str(a) for a in args] if isinstance(args, Sequence) else []

    if not verbatim:
        bridged = _rewrite_remote_bridge(command, args, planned)
        if bridged:
            return planned

    planned.config["command"] = command
    if args:
        planned.config["args"] = args
    env = mapping.get("env")
    if isinstance(env, Mapping) and env:
        planned.config["env"] = _reference_secrets(
            {str(k): str(v) for k, v in env.items()},
            planned,
            environ=environ,
            plaintext=plaintext,
        )
    return planned


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
    url = next(
        (part for part in args if part.startswith(("http://", "https://"))), None
    )
    if url is None:
        planned.notes.append(
            "looks like an mcp-remote bridge but no URL argument was found; "
            "kept verbatim"
        )
        return False
    planned.config["url"] = url
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
) -> dict[str, Any]:
    if plaintext:
        return values
    referenced: dict[str, Any] = {}
    for key, value in values.items():
        if not isinstance(value, str) or not _looks_secret(key, value):
            referenced[key] = value
            continue
        if value.startswith(("env://", "keyring://")):
            referenced[key] = value
            continue
        env_var = _matching_env_var(key, value, environ)
        if env_var is not None:
            referenced[key] = f"env://{env_var}"
            planned.notes.append(
                f"{key}: wrote env://{env_var} instead of the literal value"
            )
            continue
        secret_name = _secret_name_for(planned.name, key)
        referenced[key] = f"keyring://{secret_name}"
        planned.secrets_to_store.append((secret_name, value))
        planned.notes.append(
            f"{key}: moved to your OS keyring as {secret_name!r} "
            f"(wrote keyring://{secret_name})"
        )
    return referenced


def _looks_secret(key: str, value: str) -> bool:
    if not value:
        return False
    if _SECRET_KEY_HINT.search(key):
        return True
    return bool(_SECRET_VALUE_HINT.fullmatch(value))


def _matching_env_var(
    key: str, value: str, environ: Mapping[str, str]
) -> str | None:
    if environ.get(key) == value:
        return key
    for var, env_value in environ.items():
        if env_value == value:
            return var
    return None


def _secret_name_for(server: str, key: str) -> str:
    slug = _SECRET_NAME_CHARS.sub("-", key.lower()).strip("-.")
    return f"{server}-{slug}" if slug else server


def _sanitize_server_name(name: str) -> str | None:
    cleaned = _SERVER_NAME_CHARS.sub("-", name).strip("-")
    if not cleaned or not _SERVER_NAME.fullmatch(cleaned):
        return None
    return cleaned


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
            f"{key}={_summary_value(value)}"
            for key, value in planned.config.items()
            if key in ("url", "command", "auth")
        )
        lines.append(f"{verb} {planned.name} ({planned.source}) -> {summary}")
        lines.extend(f"  note: {note}" for note in planned.notes)
        if report.dry_run:
            lines.extend(
                f"  would store secret {name!r} in your OS keyring"
                for name, _ in planned.secrets_to_store
            )
    for skipped in report.skipped:
        lines.append(f"skipped {skipped.name} ({skipped.source}): {skipped.reason}")

    next_steps = [
        step for planned in report.imported for step in planned.next_steps
    ]
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


def _summary_value(value: Any) -> str:
    return str(value)
