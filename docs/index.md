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

## Design Notes

- [Architecture](architecture.md)
- [Code Mode Backends](code-mode-backends.md)
