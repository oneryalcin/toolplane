"""Command-line entrypoint for Toolplane."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from .config import load_toolplane_config
from .errors import UnsafeFacadeConfigError
from .mcp_facade import serve_mcp_facade
from .policy import EffectivePolicy, ensure_safe_facade_policy, format_effective_policy


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
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

    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
