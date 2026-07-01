"""Tiny second FastMCP server used by multi_server.py."""

from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP("Text Smoke")


@mcp.tool
def word_count(text: str) -> dict[str, object]:
    """Count the words in a string."""
    words = text.split()
    return {"count": len(words), "words": words}


if __name__ == "__main__":
    mcp.run(show_banner=False, log_level="ERROR")
