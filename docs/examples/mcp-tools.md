# MCP Tools

Toolplane registers MCP tools as capabilities. The runtime exposes both stable
canonical ids and Python-friendly aliases.

```text
mcp:context7/get_docs      # canonical id
context7_get_docs(...)     # flat alias when unambiguous
context7.get_docs(...)     # scoped namespace
```

The default `monty` backend supports the flat aliases and `call_tool(...)`;
the scoped `context7.get_docs(...)` form needs the `local_unsafe` or
`pyodide-deno` backends.

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

## Serve Your Own Python Functions

`toolplane serve mcp` serves what the config file names: MCP servers and
allowed CLI binaries. Python functions are registered in code, so serving them
to an agent takes a short launcher. Build the runtime from the same config,
register the functions, then serve the facade:

```python
from toolplane import Toolplane, build_mcp_facade, load_toolplane_config
from toolplane.policy import EffectivePolicy, ensure_safe_facade_policy


def sla_breach(severity: str, days_open: int) -> bool:
    """True when a high-severity ticket has been open more than 5 days."""
    return severity == "high" and days_open > 5


async def serve() -> None:
    config = load_toolplane_config("toolplane.toml")
    runtime = await Toolplane.from_config(config)
    runtime.register_python_namespace("policy", {"sla_breach": sla_breach})
    policy = EffectivePolicy.from_config(config)
    ensure_safe_facade_policy(policy)
    app = build_mcp_facade(runtime, policy=policy)
    await app.run_async(transport="stdio", show_banner=False)
```

Then point the client at the script instead of `toolplane serve mcp`:

```bash
claude mcp add my-tools -- python /abs/path/to/launcher.py
```

The agent sees the function as `await policy_sla_breach(severity=..., days_open=...)`,
callable in the same program as the configured MCP tools and CLI binaries.

!!! warning "Pass the policy"

    `policy` is what pins `execute_code` to the configured backend. A facade
    built without it accepts `backend="local_unsafe"` from the client, which
    runs the snippet outside the sandbox.

Register functions before calling `build_mcp_facade`: the tool descriptions
are a snapshot of the registry at build time. The runnable version is
`examples/python_functions_mcp.py`.

## Deterministic Smokes

```bash
uv run --no-config --no-project --with-editable . python examples/fastmcp_in_process.py
uv run --no-config --no-project --with-editable . python examples/mcp_stdio_config.py
```
