"""Small MCP lifecycle helpers for Toolplane's CLI."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tomlkit

from .config import ToolplaneConfig


class McpAddError(ValueError):
    """Raised when an MCP add snippet cannot be rendered."""


class McpStatusError(ValueError):
    """Raised when MCP status cannot inspect the requested config."""


class McpLoginError(ValueError):
    """Raised when an MCP server cannot be logged in from the CLI."""


McpStatusState = Literal["ok", "auth_required", "timeout", "error"]
McpServerKind = Literal["url", "stdio", "unknown"]


@dataclass(frozen=True)
class McpServerStatus:
    """Operator-facing status for one configured MCP server."""

    name: str
    kind: McpServerKind
    auth: str
    state: McpStatusState
    tool_count: int | None = None
    detail: str = ""
    warning: str = ""


_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_AUTH_REQUIRED_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "authentication",
    "authorization",
)
_DIRECT_OAUTH_WARNING = (
    "direct OAuth tokens are ephemeral in Toolplane v1; use a fastmcp-remote "
    "bridge for persistent login"
)


def render_mcp_add_snippet(
    name: str,
    *,
    url: str | None = None,
    command: str | None = None,
    args: Sequence[str] = (),
    auth: str | None = None,
) -> str:
    """Render a self-contained Toolplane TOML snippet for one MCP server."""
    _validate_mcp_add_request(name, url=url, command=command, args=args, auth=auth)
    document = tomlkit.document()
    servers = _ensure_table(_ensure_table(document, "mcp"), "servers")
    servers[name] = _build_mcp_server_table(
        name,
        url=url,
        command=command,
        args=args,
        auth=auth,
    )
    snippet = tomlkit.dumps(document)
    return "# add this to your toolplane.toml:\n" + snippet


def write_mcp_add_config(
    config_path: str | os.PathLike[str],
    name: str,
    *,
    url: str | None = None,
    command: str | None = None,
    args: Sequence[str] = (),
    auth: str | None = None,
    force: bool = False,
) -> Path:
    """Add or replace one MCP server in a Toolplane TOML config file."""
    _validate_mcp_add_request(name, url=url, command=command, args=args, auth=auth)

    path = Path(config_path).expanduser()
    if path.exists():
        try:
            document = tomlkit.parse(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise McpAddError(f"Could not parse {path}: {exc}") from exc
    else:
        document = tomlkit.document()

    mcp = _ensure_table(document, "mcp")
    servers = _ensure_table(mcp, "servers")
    if name in servers and not force:
        raise McpAddError(
            f"MCP server {name!r} already exists; use --force to replace it"
        )

    servers[name] = _build_mcp_server_table(
        name,
        url=url,
        command=command,
        args=args,
        auth=auth,
    )
    _write_text_atomic(path, tomlkit.dumps(document))
    return path


def _write_text_atomic(path: Path, text: str) -> None:
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


def _validate_mcp_add_request(
    name: str,
    *,
    url: str | None,
    command: str | None,
    args: Sequence[str],
    auth: str | None,
) -> None:
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


def _build_mcp_server_table(
    name: str,
    *,
    url: str | None,
    command: str | None,
    args: Sequence[str],
    auth: str | None,
) -> tomlkit.items.Table:
    server = tomlkit.table()
    if url is not None:
        server["url"] = url
        if auth is not None:
            server["auth"] = auth
            for comment in _direct_oauth_warning_comments():
                server.add(tomlkit.comment(comment))
        return server

    server["command"] = command or ""
    if args:
        server["args"] = list(args)
    if _is_fastmcp_remote_bridge(command or "", args):
        server.add(
            tomlkit.comment("prime this bridge before relying on status or execute:")
        )
        server.add(tomlkit.comment(f"toolplane mcp login {name}"))
    return server


def _ensure_table(
    parent: MutableMapping[str, Any],
    key: str,
) -> MutableMapping[str, Any]:
    value = parent.get(key)
    if value is None:
        table = tomlkit.table()
        parent[key] = table
        return table
    if not isinstance(value, MutableMapping):
        raise McpAddError(f"Config key {key!r} must be a TOML table")
    return value


def _is_fastmcp_remote_bridge(command: str, args: Sequence[str]) -> bool:
    return any(_command_basename(part) == "fastmcp-remote" for part in (command, *args))


def _command_basename(value: str) -> str:
    return value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


async def check_mcp_status(
    config: ToolplaneConfig,
    *,
    names: Sequence[str] = (),
    timeout_seconds: float = 5.0,
) -> tuple[McpServerStatus, ...]:
    """Check configured MCP servers without triggering OAuth flows."""
    if timeout_seconds <= 0:
        raise McpStatusError("--timeout must be greater than zero")

    servers = config.mcp.servers
    missing = tuple(name for name in names if name not in servers)
    if missing:
        joined = ", ".join(missing)
        raise McpStatusError(f"Unknown MCP server: {joined}")

    selected_names = tuple(names) if names else tuple(sorted(servers))
    statuses: list[McpServerStatus] = []
    for name in selected_names:
        server_config = servers[name]
        statuses.append(
            await _check_one_mcp_server(
                name,
                server_config,
                timeout_seconds=timeout_seconds,
            )
        )
    return tuple(statuses)


def format_mcp_status(statuses: Sequence[McpServerStatus]) -> str:
    """Render MCP status output for humans and simple scripts."""
    lines = ["MCP servers:"]
    if not statuses:
        lines.append("(none)")
        return "\n".join(lines) + "\n"

    for status in statuses:
        parts = [
            f"- {status.name}:",
            status.state,
            f"transport={status.kind}",
            f"auth={status.auth}",
        ]
        if status.tool_count is not None:
            parts.append(f"tools={status.tool_count}")
        if status.detail:
            parts.append(f"detail={status.detail}")
        if status.warning:
            parts.append(f"warning={status.warning}")
        lines.append(" ".join(parts))
    return "\n".join(lines) + "\n"


async def login_mcp_server(
    config: ToolplaneConfig,
    name: str,
    *,
    timeout_seconds: float = 180.0,
) -> McpServerStatus:
    """Prime one MCP server interactively, allowing OAuth browser flows."""
    if timeout_seconds <= 0:
        raise McpLoginError("--timeout must be greater than zero")

    servers = config.mcp.servers
    if name not in servers:
        raise McpLoginError(f"Unknown MCP server: {name}")

    server_config = servers[name]
    if _is_direct_oauth_server(server_config):
        raise McpLoginError(
            f"MCP server {name!r} uses direct OAuth; its tokens are ephemeral "
            "in Toolplane v1, so login would not persist. Re-add it as a "
            "fastmcp-remote bridge (mcp add --command uvx --arg fastmcp-remote "
            "--arg <url>), then login."
        )

    return await _probe_mcp_server(
        name,
        server_config,
        _login_server_config(server_config),
        timeout_seconds=timeout_seconds,
    )


def format_mcp_login(status: McpServerStatus, *, timeout_seconds: float) -> str:
    """Render one login attempt as a human-facing result line."""
    if status.state == "ok":
        return f"Login succeeded for {status.name!r}: {status.tool_count} tools\n"
    if status.state == "timeout":
        return (
            f"Login timed out for {status.name!r} after {timeout_seconds:g}s; "
            "complete the browser flow faster or retry with a larger "
            "--timeout\n"
        )
    return f"Login failed for {status.name!r}: {status.detail}\n"


async def _check_one_mcp_server(
    name: str,
    server_config: Mapping[str, Any],
    *,
    timeout_seconds: float,
) -> McpServerStatus:
    return await _probe_mcp_server(
        name,
        server_config,
        _status_probe_server_config(server_config),
        timeout_seconds=timeout_seconds,
    )


async def _probe_mcp_server(
    name: str,
    server_config: Mapping[str, Any],
    probe_config: Mapping[str, Any],
    *,
    timeout_seconds: float,
) -> McpServerStatus:
    kind = _server_kind(server_config)
    auth = _auth_label(server_config)
    warning = _server_warning(server_config)

    try:
        tools = await asyncio.wait_for(
            _list_mcp_tools(name, probe_config, timeout_seconds=timeout_seconds),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return McpServerStatus(
            name=name,
            kind=kind,
            auth=auth,
            state="timeout",
            detail=f"timed out after {timeout_seconds:g}s",
            warning=warning,
        )
    except Exception as exc:
        detail = _one_line_error(exc)
        return McpServerStatus(
            name=name,
            kind=kind,
            auth=auth,
            state="auth_required" if _looks_auth_required(detail) else "error",
            detail=detail,
            warning=warning,
        )

    return McpServerStatus(
        name=name,
        kind=kind,
        auth=auth,
        state="ok",
        tool_count=len(tools),
        warning=warning,
    )


async def _list_mcp_tools(
    name: str,
    server_config: Mapping[str, Any],
    *,
    timeout_seconds: float,
) -> list[Any]:
    try:
        from fastmcp import Client
        from fastmcp.mcp_config import MCPConfig
    except ImportError as exc:  # pragma: no cover - exercised only without dependency
        raise ImportError(
            "Toolplane MCP status requires FastMCP. Install Toolplane with "
            "its dependencies or add `fastmcp` to the environment."
        ) from exc

    config = MCPConfig.from_dict({"mcpServers": {name: dict(server_config)}})
    async with Client(
        config,
        timeout=timeout_seconds,
        init_timeout=timeout_seconds,
    ) as client:
        return list(await client.list_tools())


def _status_probe_server_config(server_config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a probe config that cannot trigger FastMCP OAuth."""
    sanitized = _sanitized_probe_config(server_config)
    if _server_kind(sanitized) == "stdio":
        env = _merged_stdio_env(sanitized.get("env"))
        env["BROWSER"] = _disabled_browser_command()
        sanitized["env"] = env
    return sanitized


def _login_server_config(server_config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a probe config that may open a browser to complete OAuth."""
    prepared = _sanitized_probe_config(server_config)
    if _server_kind(prepared) == "stdio":
        prepared["env"] = _merged_stdio_env(prepared.get("env"))
    return prepared


def _sanitized_probe_config(server_config: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = dict(server_config)
    sanitized.pop("auth", None)
    sanitized.pop("authentication", None)
    if _server_kind(sanitized) == "stdio":
        sanitized["keep_alive"] = False
    return sanitized


def _merged_stdio_env(configured_env: Any) -> dict[str, str]:
    env = dict(os.environ)
    if configured_env is not None:
        if not isinstance(configured_env, Mapping):
            raise McpStatusError("stdio MCP server env must be a table")
        env.update({str(key): str(value) for key, value in configured_env.items()})
    return env


def _disabled_browser_command() -> str:
    if os.path.exists("/usr/bin/false"):
        return "/usr/bin/false"
    return "false"


def _server_kind(server_config: Mapping[str, Any]) -> McpServerKind:
    if "url" in server_config:
        return "url"
    if "command" in server_config:
        return "stdio"
    return "unknown"


def _auth_label(server_config: Mapping[str, Any]) -> str:
    auth = server_config.get("auth")
    if auth is not None:
        return str(auth)
    authentication = server_config.get("authentication")
    if isinstance(authentication, Mapping):
        auth_type = authentication.get("type")
        if auth_type is not None:
            return str(auth_type)
        return "configured"
    return "none"


def _server_warning(server_config: Mapping[str, Any]) -> str:
    if _is_direct_oauth_server(server_config):
        return _DIRECT_OAUTH_WARNING
    return ""


def _is_direct_oauth_server(server_config: Mapping[str, Any]) -> bool:
    return (
        _server_kind(server_config) == "url"
        and server_config.get("auth") == "oauth"
    )


def _direct_oauth_warning_comments() -> list[str]:
    return [
        "warning: direct OAuth tokens are ephemeral in Toolplane v1.",
        "use a fastmcp-remote bridge for persistent login.",
    ]


def _looks_auth_required(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker in lowered for marker in _AUTH_REQUIRED_MARKERS)


def _one_line_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return " ".join(message.split())
