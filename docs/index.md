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
schemas, and execute agent-written Python against them through the
development-only `local_unsafe` backend.

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

## Design Notes

- [Architecture](architecture.md)
- [Code Mode Backends](code-mode-backends.md)
