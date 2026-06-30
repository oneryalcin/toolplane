"""Command-line entrypoint for Toolplane."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from .mcp_facade import serve_mcp_facade


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve" and args.serve_command == "mcp":
        asyncio.run(
            serve_mcp_facade(
                args.config,
                transport=args.transport,
                host=args.host,
                port=args.port,
            )
        )
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

    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
