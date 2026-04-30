"""Walk through scoped capability namespaces with the live Context7 MCP server.

This is a teaching example, not a deterministic CI smoke. It depends on network
access and the live Context7 service contract, so it is intentionally excluded
from `make examples`.

What this example demonstrates:

1. Host setup stays outside the executed code.
2. Host Python helpers are grouped under a scoped namespace: `repo.*`.
3. Remote MCP tools are grouped under a scoped namespace: `context7.*`.
4. Flat aliases such as `context7_query_docs` still exist for simple cases.
5. Canonical ids such as `mcp:context7/query-docs` remain the stable escape
   hatch through `call_tool(...)`.

Run it with:

    uv run --no-project --with-editable . python examples/scoped_namespaces_context7.py
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

from toolplane import Capability, Toolplane


AGENT_CODE = """
# The code author only sees normal Python names. They do not know or care
# whether a callable came from a local host helper or a remote MCP server.
readme = await repo.read_text(path="README.md")
readme_heading = await repo.first_heading(text=readme)

# `context7` is a scoped namespace created from the MCP server name in the
# host config. Tool names are normalized into safe Python attribute names.
libraries = await context7.resolve_library_id(
    libraryName="pandas",
    query="pandas DataFrame groupby reset_index to_dict records",
)
docs = await context7.query_docs(
    libraryId="/pandas-dev/pandas",
    query="How do I group rows and convert a DataFrame to records?",
)

# Canonical ids are still available for dynamic/reflection-heavy code. The
# host passes this string as an input so the code does not guess tool ids.
docs_via_canonical_id = await call_tool(
    query_docs_capability,
    {
        "libraryId": "/pandas-dev/pandas",
        "query": "What does DataFrame.to_dict('records') return?",
    },
)

return {
    "readme_heading": readme_heading,
    "library_lookup_prefix": libraries[:360],
    "scoped_docs_prefix": docs[:360],
    "canonical_docs_prefix": docs_via_canonical_id[:360],
}
"""


async def main() -> None:
    runtime = Toolplane()

    def read_text(path: str) -> str:
        """Read a repository text file on the host side."""
        return Path(path).read_text(encoding="utf-8", errors="ignore")

    def first_heading(text: str) -> str:
        """Return the first Markdown heading in a text blob."""
        for line in text.splitlines():
            if line.startswith("#"):
                return line.lstrip("#").strip()
        return ""

    python_capabilities = runtime.register_python_namespace(
        "repo",
        {
            "read_text": read_text,
            "first_heading": first_heading,
        },
        tags={"repo", "filesystem"},
    )

    mcp_capabilities = await runtime.register_mcp_config(
        {
            "mcpServers": {
                "context7": {
                    "url": "https://mcp.context7.com/mcp",
                }
            }
        }
    )
    query_docs_capability = _capability_id_for(mcp_capabilities, "query_docs")

    print("# Registered capabilities")
    for capability in [*python_capabilities, *mcp_capabilities]:
        print(
            json.dumps(
                {
                    "canonical_id": capability.name,
                    "aliases": sorted(capability.aliases),
                    "namespace": _namespace_label(capability),
                    "source": capability.source,
                },
                sort_keys=True,
            )
        )

    print("\n# Flat callable aliases")
    print(json.dumps(runtime.registry.callable_namespace(), indent=2, sort_keys=True))

    print("\n# Scoped namespace map")
    print(json.dumps(runtime.registry.scoped_namespace(), indent=2, sort_keys=True))

    print("\n# Code executed by Toolplane")
    print(textwrap.indent(AGENT_CODE.strip(), "    "))

    result = await runtime.execute(
        AGENT_CODE,
        inputs={"query_docs_capability": query_docs_capability},
    )

    print("\n# Result")
    print("ok:", result.ok)
    if result.ok:
        print(json.dumps(result.value, indent=2))
    else:
        print(result.error)


def _capability_id_for(capabilities: list[Capability], tool_name: str) -> str:
    for capability in capabilities:
        actual = capability.name.rsplit("/", 1)[-1].replace("-", "_")
        if actual == tool_name:
            return capability.name
    available = ", ".join(capability.name for capability in capabilities)
    raise LookupError(f"Could not find MCP tool {tool_name!r}. Available: {available}")


def _namespace_label(capability: Capability) -> str | None:
    if capability.namespace is None or capability.namespace_member is None:
        return None
    return f"{capability.namespace}.{capability.namespace_member}"


if __name__ == "__main__":
    asyncio.run(main())
