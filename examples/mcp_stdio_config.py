"""Register a stdio MCP server config and call it from Toolplane code."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from toolplane import Toolplane


async def main() -> None:
    runtime = Toolplane()
    server_path = Path(__file__).with_name("mcp_stdio_server.py")

    await runtime.register_mcp_config(
        {
            "mcpServers": {
                "stdio_demo": {
                    "command": sys.executable,
                    "args": [str(server_path)],
                }
            }
        }
    )
    result = await runtime.execute(
        """
value = await stdio_demo.multiply(x=6, y=7)
return {"answer": value["product"], "operands": value["operands"]}
"""
    )

    print("flat aliases:", runtime.registry.callable_namespace())
    print("scoped namespaces:", runtime.registry.scoped_namespace())
    print("ok:", result.ok)
    print("value:", result.value)


if __name__ == "__main__":
    asyncio.run(main())
