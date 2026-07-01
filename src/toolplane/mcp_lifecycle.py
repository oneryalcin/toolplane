"""Small MCP lifecycle helpers for Toolplane's CLI."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence


class McpAddError(ValueError):
    """Raised when an MCP add snippet cannot be rendered."""


_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def render_mcp_add_snippet(
    name: str,
    *,
    url: str | None = None,
    command: str | None = None,
    args: Sequence[str] = (),
    auth: str | None = None,
) -> str:
    """Render a self-contained Toolplane TOML snippet for one MCP server."""
    if not _SERVER_NAME.fullmatch(name):
        raise McpAddError(
            "MCP server name must contain only letters, numbers, underscores, "
            "and hyphens"
        )
    if (url is None) == (command is None):
        raise McpAddError("Specify exactly one of --url or --command")
    if url is not None and not url.strip():
        raise McpAddError("--url must be non-empty")
    if command is not None and not command.strip():
        raise McpAddError("--command must be non-empty")
    if url is not None and args:
        raise McpAddError("--arg is only valid with --command")
    if auth is not None and auth != "oauth":
        raise McpAddError("Only --auth oauth is supported")
    if command is not None and auth is not None:
        raise McpAddError("--auth is only valid with --url")

    lines = [
        "# add this to your toolplane.toml:",
        f"[mcp.servers.{name}]",
    ]
    if url is not None:
        lines.append(f"url = {_toml_string(url)}")
        if auth is not None:
            lines.append(f"auth = {_toml_string(auth)}")
    else:
        lines.append(f"command = {_toml_string(command or '')}")
        if args:
            rendered_args = ", ".join(_toml_string(arg) for arg in args)
            lines.append(f"args = [{rendered_args}]")

    return "\n".join(lines) + "\n"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
