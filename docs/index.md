# toolplane

A controlled Python code-mode runtime where CLIs, MCP tools, and libraries are
normalized into one programmable tool surface.

## Why It Exists

Agents are strongest when they can use code as the control plane for real work:
looping, branching, filtering, retrying, aggregating, and combining tools
without bouncing through one tool call at a time.

`toolplane` exposes a curated Python runtime where capabilities can come from:

- Python functions and libraries.
- MCP tools.
- CLI wrappers such as `cli-to-py`.
- Host application helpers.

The agent writes Python. The host controls which capabilities exist, how
credentials are handled, and which backend executes the code.

## Design Invariants

- JSON is a wire format, not the programming model. Agent-written code should
  receive Python values such as `dict`, `list`, `str`, numbers, booleans, and
  `None`.
- MCP tools, CLI wrappers, and regular Python functions are all capabilities.
  They differ by adapter, not by how user code composes them.
- Canonical capability ids are qualified. Friendly Python names are aliases and
  must never silently shadow each other.
- Sandboxed and remote backends call host capabilities through a bridge unless a
  capability is explicitly safe to ship into the execution environment.

## Current Slice

The first implementation can register Python functions, discover them, inspect
schemas, register explicit `cli-to-py` wrappers, and execute agent-written
Python through:

- `local_unsafe`: development-only in-process execution.
- `pyodide-deno`: experimental Pyodide-in-Deno sandbox execution with package
  loading and host bridge `call_tool` callbacks.

```python
from toolplane import Toolplane

runtime = Toolplane()

@runtime.tool(tags={"math"})
def add(x: int, y: int) -> int:
    """Add two numbers."""
    return x + y

result = await runtime.execute("""
value = await call_tool("add", {"x": 2, "y": 3})
return value
""")
```

The Pyodide+Deno backend can run package-backed code and call host capabilities:

```python
result = await runtime.execute(
    """
import pandas as pd

x = await call_tool("add", {"x": 2, "y": 3})
df = pd.DataFrame([{"value": x}])
return int(df["value"].sum())
""",
    backend="pyodide-deno",
    packages=["pandas"],
)
```

## CLI Adapter

`cli-to-py` commands can be registered as normal Toolplane capabilities. The
host chooses which commands to expose; Toolplane does not scan `PATH` or expose
local CLIs automatically.

```python
from cli_to_py import convert
from toolplane import Toolplane

runtime = Toolplane()
python = await convert("python3", subcommands=False)

runtime.register_cli(
    "python_version",
    python,
    description="Return the Python interpreter version.",
    tags={"python", "cli"},
)

result = await runtime.execute("""
version = await call_tool("python_version", {"version": True})
return version["stdout"] + version["stderr"]
""")
```

## MCP Adapter

FastMCP-compatible servers can be registered as capabilities too. Toolplane
uses FastMCP's client machinery for discovery, transport, and tool calls.

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

result = await runtime.execute("""
value = await demo_add(a=2, b=3)
return value
""")
```

Standard `mcpServers` config works for stdio and remote servers:

```python
await runtime.register_mcp_config({
    "mcpServers": {
        "context7": {
            "url": "https://mcp.context7.com/mcp",
        }
    }
})
```

Each MCP tool gets a canonical id such as `mcp:context7/get_docs` and a safe
Python alias such as `context7_get_docs`.

The repo includes executable examples for in-process FastMCP apps, stdio
`mcpServers` config, and an opt-in live Context7 remote MCP smoke:

```bash
make examples
uv run --no-project --with-editable . python examples/context7_remote.py
```

## Design Notes

- [Architecture](architecture.md)
- [Code Mode Backends](code-mode-backends.md)
