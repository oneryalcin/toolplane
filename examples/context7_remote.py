"""Call the live Context7 remote MCP server from Toolplane code.

This example depends on network access and the live Context7 service contract,
so it is not part of the deterministic example smoke target.
"""

from __future__ import annotations

import asyncio
import json

from toolplane import Toolplane


async def main() -> None:
    runtime = Toolplane()
    capabilities = await runtime.register_mcp_config(
        {
            "mcpServers": {
                "context7": {
                    "url": "https://mcp.context7.com/mcp",
                }
            }
        }
    )

    result = await runtime.execute(
        """
libraries = await context7_resolve_library_id(
    libraryName="pandas",
    query="pandas dataframe csv examples",
)
docs = await context7_query_docs(
    libraryId="/pandas-dev/pandas",
    query="How do I read a CSV into a DataFrame?",
)
return {"libraries_prefix": libraries[:300], "docs_prefix": docs[:300]}
"""
    )

    print("registered:", [(cap.name, sorted(cap.aliases)) for cap in capabilities])
    print("namespace:", runtime.registry.callable_namespace())
    print("ok:", result.ok)
    if result.ok:
        print("value:", json.dumps(result.value, indent=2))
    else:
        print("error:", result.error)


if __name__ == "__main__":
    asyncio.run(main())
