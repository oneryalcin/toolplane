"""Tiny FastMCP stdio server for the cold-start benchmark (#118).

Serves one tool immediately unless ``--delay N`` is given, in which case
it sleeps N seconds before serving — an artificially slow/unavailable
upstream for measuring registration behavior.
"""

from __future__ import annotations

import argparse

from fastmcp import FastMCP


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args()

    mcp = FastMCP("Coldstart Smoke")

    @mcp.tool
    def ping() -> str:
        """Respond with pong."""
        return "pong"

    if args.delay > 0:
        import time

        time.sleep(args.delay)
    mcp.run(show_banner=False, log_level="ERROR")


if __name__ == "__main__":
    main()
