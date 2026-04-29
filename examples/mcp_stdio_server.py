"""Tiny FastMCP server used by mcp_stdio_config.py."""

from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP("Stdio Smoke")


@mcp.tool
def multiply(x: int, y: int) -> dict[str, object]:
    """Multiply two numbers."""
    return {"product": x * y, "operands": [x, y]}


if __name__ == "__main__":
    mcp.run(show_banner=False, log_level="ERROR")
