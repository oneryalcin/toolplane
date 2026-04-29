"""Register an in-process FastMCP app and call it from Toolplane code."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from toolplane import Toolplane


async def main() -> None:
    runtime = Toolplane()
    mcp = FastMCP("Demo")

    @mcp.tool
    def add(a: int, b: int) -> dict[str, object]:
        """Add two numbers and return structured data."""
        return {"sum": a + b, "inputs": [a, b]}

    capabilities = await runtime.register_mcp("demo", mcp)
    result = await runtime.execute(
        """
value = await demo_add(a=2, b=3)
return {"answer": value["sum"], "inputs": value["inputs"]}
"""
    )

    print("registered:", [(cap.name, sorted(cap.aliases)) for cap in capabilities])
    print("ok:", result.ok)
    print("value:", result.value)


if __name__ == "__main__":
    asyncio.run(main())
