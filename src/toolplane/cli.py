"""Command-line entrypoint for Toolplane."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from .config import load_toolplane_config
from .errors import UnsafeFacadeConfigError
from .mcp_facade import serve_mcp_facade
from .mcp_lifecycle import (
    McpAddError,
    McpStatusError,
    check_mcp_status,
    format_mcp_status,
    render_mcp_add_snippet,
    write_mcp_add_config,
)
from .policy import EffectivePolicy, ensure_safe_facade_policy, format_effective_policy


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(_normalize_repeated_arg_values(raw_argv))
    if args.command == "serve" and args.serve_command == "mcp":
        try:
            config = load_toolplane_config(args.config)
            policy = EffectivePolicy.from_config(
                config,
                allow_unsafe=args.unsafe,
            )
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
    if args.command == "mcp" and args.mcp_command == "add":
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
    if args.command == "mcp" and args.mcp_command == "status":
        try:
            config = load_toolplane_config(args.config)
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
    parser.print_help()
    return 2


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

    mcp_root = subcommands.add_parser("mcp", help="Manage MCP server snippets")
    mcp_subcommands = mcp_root.add_subparsers(dest="mcp_command")

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
