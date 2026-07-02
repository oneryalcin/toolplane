"""Command-line entrypoint for Toolplane."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import ToolplaneConfig, load_toolplane_config
from .doctor import doctor_exit_code, format_doctor_checks, run_doctor_checks
from .errors import UnsafeFacadeConfigError
from .execution import ExecutionResult
from .mcp_facade import serve_mcp_facade
from .mcp_lifecycle import (
    McpAddError,
    McpLoginError,
    McpStatusError,
    check_mcp_status,
    format_mcp_list,
    format_mcp_login,
    format_mcp_status,
    login_mcp_server,
    render_mcp_add_snippet,
    write_mcp_add_config,
)
from .policy import EffectivePolicy, ensure_safe_facade_policy, format_effective_policy

_INIT_TEMPLATE = """\
# Toolplane project configuration.
# Safe defaults: sandboxed monty backend, CLI disabled.

[toolplane]
default_backend = "monty" # monty | pyodide-deno | local_unsafe (dev only)

[cli]
mode = "disabled" # disabled | allowlist | ambient (dev only)
# allow = ["git", "gh", "rg"]

# Add MCP servers with: toolplane mcp add <name> --url <url>
# [mcp.servers.context7]
# url = "https://mcp.context7.com/mcp"

# Inspect: toolplane config check / toolplane doctor / toolplane mcp list
# Serve:   toolplane serve mcp
"""


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(_normalize_repeated_arg_values(raw_argv))
    match (args.command, _subcommand(args)):
        case ("serve", "mcp"):
            return _cmd_serve_mcp(args)
        case ("init", None):
            return _cmd_init(args)
        case ("config", "check"):
            return _cmd_config_check(args)
        case ("doctor", None):
            return _cmd_doctor(args)
        case ("run", None):
            return _cmd_run(args)
        case ("mcp", "list"):
            return _cmd_mcp_list(args)
        case ("mcp", "add"):
            return _cmd_mcp_add(args)
        case ("mcp", "login"):
            return _cmd_mcp_login(args)
        case ("mcp", "status"):
            return _cmd_mcp_status(args)
        case _:
            parser.print_help()
            return 2


def _subcommand(args: argparse.Namespace) -> str | None:
    for attribute in ("serve_command", "config_command", "mcp_command"):
        value = getattr(args, attribute, None)
        if value is not None:
            return value
    return None


def _load_config(config_path: str) -> ToolplaneConfig | None:
    """Load and validate a config, or print the error and return None."""
    try:
        return load_toolplane_config(config_path)
    except (OSError, ValueError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return None


def _cmd_serve_mcp(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    try:
        policy = EffectivePolicy.from_config(config, allow_unsafe=args.unsafe)
        ensure_safe_facade_policy(policy)
        print(format_effective_policy(policy), file=sys.stderr)
        asyncio.run(
            serve_mcp_facade(
                config,
                transport=args.transport,
                host=args.host,
                port=args.port,
                allow_unsafe=args.unsafe,
            )
        )
    except UnsafeFacadeConfigError as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if path.exists() and not args.force:
        print(
            f"toolplane: {path} already exists; use --force to overwrite",
            file=sys.stderr,
        )
        return 2
    try:
        path.write_text(_INIT_TEMPLATE, encoding="utf-8")
    except OSError as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {path}")
    return 0


def _cmd_config_check(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    print(_format_config_summary(args.config, config), end="")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    checks = run_doctor_checks(config)
    print(format_doctor_checks(checks), end="")
    return doctor_exit_code(checks)


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        code = Path(args.script).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2

    async def execute() -> ExecutionResult:
        from .runtime import Toolplane

        runtime = await Toolplane.from_config(args.config)
        return await runtime.execute(code)

    try:
        result = asyncio.run(execute())
    except (OSError, ValueError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2

    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.error is not None:
        print(
            f"toolplane: {result.error.type}: {result.error.message}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result.value, indent=2, default=str))
    return 0


def _cmd_mcp_list(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    print(format_mcp_list(config), end="")
    return 0


def _cmd_mcp_add(args: argparse.Namespace) -> int:
    try:
        if args.print:
            snippet = render_mcp_add_snippet(
                args.name,
                url=args.url,
                command=args.command_value,
                args=tuple(args.args or ()),
                auth=args.auth,
            )
            print(snippet, end="")
        else:
            path = write_mcp_add_config(
                args.config,
                args.name,
                url=args.url,
                command=args.command_value,
                args=tuple(args.args or ()),
                auth=args.auth,
                force=args.force,
            )
            print(f"Added MCP server {args.name!r} to {path}")
    except (McpAddError, OSError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_mcp_login(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    if args.name in config.mcp.servers:
        print(
            f"Logging in to {args.name!r}; a browser window may open "
            "to complete authentication...",
            file=sys.stderr,
        )
    try:
        status = asyncio.run(
            login_mcp_server(
                config,
                args.name,
                timeout_seconds=args.timeout,
            )
        )
    except (McpLoginError, McpStatusError, OSError, ValueError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    message = format_mcp_login(status, timeout_seconds=args.timeout)
    if status.state == "ok":
        print(message, end="")
        return 0
    print(f"toolplane: {message}", end="", file=sys.stderr)
    return 2


def _cmd_mcp_status(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    if config is None:
        return 2
    try:
        statuses = asyncio.run(
            check_mcp_status(
                config,
                names=tuple(args.names or ()),
                timeout_seconds=args.timeout,
            )
        )
    except (McpStatusError, OSError, ValueError) as exc:
        print(f"toolplane: {exc}", file=sys.stderr)
        return 2
    print(format_mcp_status(statuses), end="")
    return 0


def _format_config_summary(config_path: str, config: ToolplaneConfig) -> str:
    cli = config.cli.mode
    if cli == "allowlist":
        cli += f" [{', '.join(config.cli.allow)}]"
    servers = ", ".join(sorted(config.mcp.servers)) or "none"
    lines = [
        f"config: {config_path}",
        f"backend: {config.toolplane.default_backend}",
        f"cli: {cli}",
        f"mcp servers: {servers}",
    ]
    if config.toolplane.default_backend == "local_unsafe" or config.cli.mode == "ambient":
        lines.append("note: serve mcp will require --unsafe with this config")
    lines.append("ok")
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toolplane")
    subcommands = parser.add_subparsers(dest="command")

    serve = subcommands.add_parser("serve", help="Serve Toolplane surfaces")
    serve_subcommands = serve.add_subparsers(dest="serve_command")

    mcp = serve_subcommands.add_parser("mcp", help="Serve the Toolplane MCP facade")
    mcp.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )
    mcp.add_argument(
        "--transport",
        choices=("stdio", "http", "sse", "streamable-http"),
        default="stdio",
        help="FastMCP transport to serve",
    )
    mcp.add_argument("--host", help="Host for HTTP-based transports")
    mcp.add_argument("--port", type=int, help="Port for HTTP-based transports")
    mcp.add_argument(
        "--unsafe",
        action="store_true",
        help=(
            "Allow local_unsafe backend or ambient CLI policy for trusted local "
            "development"
        ),
    )

    init = subcommands.add_parser("init", help="Write a starter toolplane.toml")
    init.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to write the Toolplane TOML config file",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config file",
    )

    config_root = subcommands.add_parser("config", help="Inspect Toolplane config")
    config_subcommands = config_root.add_subparsers(dest="config_command")
    config_check = config_subcommands.add_parser(
        "check",
        help="Validate a config file and print a summary without network calls",
    )
    config_check.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    doctor = subcommands.add_parser(
        "doctor",
        help="Check local prerequisites for a configured runtime",
    )
    doctor.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    run_parser = subcommands.add_parser(
        "run",
        help="Execute a Python snippet file against the configured runtime",
    )
    run_parser.add_argument("script", help="Path to a Python snippet file")
    run_parser.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    mcp_root = subcommands.add_parser("mcp", help="Manage MCP server snippets")
    mcp_subcommands = mcp_root.add_subparsers(dest="mcp_command")

    mcp_list = mcp_subcommands.add_parser(
        "list",
        help="List configured MCP servers without connecting",
    )
    mcp_list.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )

    mcp_add = mcp_subcommands.add_parser(
        "add",
        help="Add an MCP server to a Toolplane TOML config",
    )
    mcp_add.add_argument("name", help="MCP server name")
    mcp_add.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )
    mcp_add.add_argument(
        "--print",
        action="store_true",
        help="Print the TOML snippet instead of editing the config file",
    )
    mcp_add.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing MCP server with the same name",
    )
    source = mcp_add.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Remote MCP endpoint URL")
    source.add_argument(
        "--command",
        dest="command_value",
        help="Local stdio MCP server command",
    )
    mcp_add.add_argument(
        "--arg",
        dest="args",
        action="append",
        help="Argument for --command; repeat for multiple arguments",
    )
    mcp_add.add_argument(
        "--auth",
        choices=("oauth",),
        help="Authentication mode for remote URL snippets",
    )

    mcp_login = mcp_subcommands.add_parser(
        "login",
        help="Prime an MCP server interactively (may open a browser)",
    )
    mcp_login.add_argument("name", help="MCP server name to log in")
    mcp_login.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )
    mcp_login.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Login timeout in seconds, including the browser flow",
    )

    mcp_status = mcp_subcommands.add_parser(
        "status",
        help="Check configured MCP servers without authenticating",
    )
    mcp_status.add_argument(
        "names",
        nargs="*",
        help="Optional MCP server name(s) to check",
    )
    mcp_status.add_argument(
        "--config",
        default="toolplane.toml",
        help="Path to a Toolplane TOML config file",
    )
    mcp_status.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-server status timeout in seconds",
    )

    return parser


def _normalize_repeated_arg_values(argv: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--arg" and index + 1 < len(argv):
            normalized.append(f"--arg={argv[index + 1]}")
            index += 2
            continue
        normalized.append(item)
        index += 1
    return normalized


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
