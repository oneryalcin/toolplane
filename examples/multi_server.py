"""Register multiple MCP servers and compose their tools in one snippet.

Two local stdio FastMCP servers are registered under one runtime. Agent-written
code then calls tools from *both* servers in a single ``execute_code`` block,
without caring which server each tool came from.

The servers here are local and unauthenticated so the example runs with no
external setup. See ``multi_server.toml`` for the same shape mixing remote,
OAuth (via the ``fastmcp-remote`` bridge), and bearer-token servers.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from toolplane import Toolplane


async def main() -> None:
    runtime = Toolplane()
    here = Path(__file__).parent

    await runtime.register_mcp_config(
        {
            "mcpServers": {
                "math": {
                    "command": sys.executable,
                    "args": [str(here / "mcp_stdio_server.py")],
                },
                "text": {
                    "command": sys.executable,
                    "args": [str(here / "mcp_text_server.py")],
                },
            }
        }
    )

    # One snippet, tools from two different servers, composed in normal Python.
    result = await runtime.execute(
        """
product = await math.multiply(x=6, y=7)
counted = await text.word_count(text="the quick brown fox")
return {
    "product": product["product"],
    "word_count": counted["count"],
    "combined": product["product"] + counted["count"],
}
"""
    )

    print("scoped namespaces:", runtime.registry.scoped_namespace())
    print("ok:", result.ok)
    print("value:", result.value)


if __name__ == "__main__":
    asyncio.run(main())
