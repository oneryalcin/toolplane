# toolplane

A controlled Python code-mode runtime where CLIs, MCP tools, and libraries are
normalized into one programmable tool surface.

## Why It Exists

Agents are strongest when they can use code as the control plane for real work:
looping, branching, filtering, retrying, aggregating, and combining tools
without bouncing through one tool call at a time.

Python already has the right orchestration model. What is missing is a clean
way to expose different tool sources through one curated runtime:

- CLI tools wrapped as Python callables.
- MCP server tools exposed as async Python functions.
- Regular Python libraries such as `pandas`, `httpx`, and project SDKs.
- Host-provided domain helpers with explicit permissions and limits.

`toolplane` is the runtime layer for that surface. The agent writes Python; the
host decides which capabilities exist, how credentials are handled, and what
resource or security boundaries apply.

## Relationship To cli-to-py

[`cli-to-py`](https://github.com/oneryalcin/cli-to-py) turns CLI binaries into
Python APIs. In `toolplane`, that is one adapter in a broader adapter stack.

The goal is that agent-written code should not need to care whether a capability
came from a CLI, an MCP server, or a normal Python package. It should see typed,
validated Python functions with predictable return values.

## Prior Art

[FastMCP Code Mode](https://gofastmcp.com/servers/transforms/code-mode) is a
strong reference point. It replaces a large MCP tool catalog with a smaller set
of meta-tools for progressive discovery and code execution: search for relevant
tools, inspect the schemas that matter, then execute Python that orchestrates
tool calls in a sandbox.

`toolplane` follows the same basic shape:

```text
discover capabilities -> inspect schemas -> execute Python against a curated namespace
```

The difference is scope. FastMCP Code Mode is centered on MCP server tools.
`toolplane` aims to generalize that pattern across MCP tools, CLI wrappers, and
regular Python libraries.

See [Code Mode Backends](docs/code-mode-backends.md) for the initial backend
strategy and [Architecture](docs/architecture.md) for the code organization
approach.

## Design Goals

- Make code-mode agents useful for multi-step tool orchestration.
- Normalize heterogeneous tools into a Python-first API surface.
- Keep the exposed runtime curated rather than ambiently powerful.
- Preserve host control over credentials, authorization, filesystems, network
  access, timeouts, and cancellation.
- Prefer structured return values and validation errors over raw text where
  practical.
- Keep adapters small enough to be understandable and replaceable.

## Docs

```bash
pip install -e ".[docs]"
mkdocs serve
```

## Status

Early implementation. The first slice can register Python functions, discover
them, inspect schemas, and execute agent-written Python against them through the
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

Next backend target: Pyodide+Deno.
