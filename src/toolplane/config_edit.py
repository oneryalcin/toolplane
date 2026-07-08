"""Comment-preserving, atomic TOML config edits shared by CLI commands."""

from __future__ import annotations

import os
import tempfile
from collections.abc import MutableMapping, Sequence
from pathlib import Path
from typing import Any

import tomlkit


class ConfigEditError(ValueError):
    """Raised when a Toolplane TOML config cannot be edited."""


def parse_config_document(path: Path) -> tomlkit.TOMLDocument:
    """Parse an existing config file, or start a fresh document."""
    if not path.exists():
        return tomlkit.document()
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigEditError(f"Could not parse {path}: {exc}") from exc


def ensure_table(
    parent: MutableMapping[str, Any],
    key: str,
) -> MutableMapping[str, Any]:
    value = parent.get(key)
    if value is None:
        table = tomlkit.table()
        parent[key] = table
        return table
    if not isinstance(value, MutableMapping):
        raise ConfigEditError(f"Config key {key!r} must be a TOML table")
    return value


def write_text_atomic(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def write_cli_deny_config(
    config_path: str | os.PathLike[str],
    binaries: Sequence[str],
) -> tuple[Path, tuple[str, ...]]:
    """Remove binaries from the CLI allowlist; returns the remaining list.

    Unknown names are an error (typo protection: a deny that silently
    matched nothing would read as done). Denying the last binary flips
    the mode to "disabled" — equally fail-closed, and the config model's
    teaching validator (allowlist requires a non-empty allow) cannot
    tell a deliberate empty list from a forgotten one.
    """
    path = Path(config_path).expanduser()
    document = parse_config_document(path)
    cli = ensure_table(document, "cli")

    existing = cli.get("allow", [])
    if not isinstance(existing, Sequence) or isinstance(existing, str):
        raise ConfigEditError("Config key 'cli.allow' must be an array of strings")
    allowed = [str(binary) for binary in existing]
    unknown = [binary for binary in binaries if binary not in allowed]
    if unknown:
        current = ", ".join(allowed) or "(empty)"
        message = (
            f"not in the allowlist: {', '.join(unknown)}; "
            f"currently allowed: {current}"
        )
        mode = cli.get("mode") or "disabled"
        if mode != "allowlist":
            message += (
                f"; note: cli mode is {str(mode)!r} and deny only applies "
                "in allowlist mode — set one with: toolplane cli allow "
                "<binary>"
            )
        raise ConfigEditError(message)

    remaining = [binary for binary in allowed if binary not in set(binaries)]
    if remaining:
        cli["allow"] = remaining
    else:
        cli["mode"] = "disabled"
        del cli["allow"]
    write_text_atomic(path, tomlkit.dumps(document))
    return path, tuple(remaining)


def write_cli_allow_config(
    config_path: str | os.PathLike[str],
    binaries: Sequence[str],
) -> tuple[Path, tuple[str, ...]]:
    """Set allowlist CLI policy and merge binaries into the allow list."""
    for binary in binaries:
        if not binary or binary != binary.strip() or any(c.isspace() for c in binary):
            raise ConfigEditError(f"Invalid CLI binary name: {binary!r}")

    path = Path(config_path).expanduser()
    document = parse_config_document(path)
    cli = ensure_table(document, "cli")

    existing = cli.get("allow", [])
    if not isinstance(existing, Sequence) or isinstance(existing, str):
        raise ConfigEditError("Config key 'cli.allow' must be an array of strings")
    merged = [str(binary) for binary in existing]
    for binary in binaries:
        if binary not in merged:
            merged.append(binary)

    cli["mode"] = "allowlist"
    cli["allow"] = merged
    write_text_atomic(path, tomlkit.dumps(document))
    return path, tuple(merged)
