# MCP Tools

Toolplane registers MCP tools as capabilities. The runtime exposes both stable
canonical ids and Python-friendly aliases.

```text
mcp:context7/get_docs      # canonical id
context7_get_docs(...)     # flat alias when unambiguous
context7.get_docs(...)     # scoped namespace
```

## In-Process FastMCP

=== "Host setup"

    ```python
    from fastmcp import FastMCP
    from toolplane import Toolplane

    runtime = Toolplane()
    mcp = FastMCP("Demo")

    @mcp.tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    await runtime.register_mcp("demo", mcp)
    ```

=== "Agent code"

    ```python
    value = await demo.add(a=2, b=3)
    return value
    ```

## Standard MCP Config

```python
await runtime.register_mcp_config({
    "mcpServers": {
        "context7": {
            "url": "https://mcp.context7.com/mcp",
        }
    }
})
```

!!! note "Canonical ids remain the escape hatch"

    Friendly aliases are for authoring convenience. Canonical capability ids are
    still available through `call_tool(...)` and remain stable when aliases
    would collide.

## Deterministic Smokes

```bash
uv run --no-config --no-project --with-editable . python examples/fastmcp_in_process.py
uv run --no-config --no-project --with-editable . python examples/mcp_stdio_config.py
```
