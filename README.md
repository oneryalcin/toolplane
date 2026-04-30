# toolplane

[![PyPI version](https://img.shields.io/pypi/v/toolplane)](https://pypi.org/project/toolplane/)
[![Python versions](https://img.shields.io/pypi/pyversions/toolplane)](https://pypi.org/project/toolplane/)
[![CI](https://github.com/oneryalcin/toolplane/actions/workflows/ci.yml/badge.svg)](https://github.com/oneryalcin/toolplane/actions/workflows/ci.yml)
[![Docs](https://github.com/oneryalcin/toolplane/actions/workflows/pages.yml/badge.svg)](https://github.com/oneryalcin/toolplane/actions/workflows/pages.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A controlled Python code-mode runtime where CLIs, MCP tools, and libraries are
normalized into one programmable tool surface.

Full documentation: https://oneryalcin.github.io/toolplane/

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

[OpenAI Agents SDK sandboxes](https://developers.openai.com/api/docs/guides/agents/sandboxes)
are another useful reference: they separate the sandbox session/provider from
the tools exposed to the model. Toolplane follows that boundary too. Backends
execute code; adapters expose capabilities; bridges let sandboxed code call host
capabilities when direct local calls are not appropriate.

See [Code Mode Backends](docs/code-mode-backends.md) for the initial backend
strategy and [Architecture](docs/architecture.md) for the code organization
approach.

See [ROADMAP.md](ROADMAP.md) for the current sequencing.

## Design Goals

- Make code-mode agents useful for multi-step tool orchestration.
- Normalize heterogeneous tools into a Python-first API surface.
- Keep the exposed runtime curated rather than ambiently powerful.
- Preserve host control over credentials, authorization, filesystems, network
  access, timeouts, and cancellation.
- Prefer structured return values and validation errors over raw text where
  practical.
- Keep adapters small enough to be understandable and replaceable.
- Treat JSON as a wire format, not the programming model. Agent-written code
  should compose normal Python values and callables.
- Make canonical capability ids qualified, and expose friendly Python aliases
  only when they are unambiguous.

## Docs

```bash
make docs
make docs-serve
```

## Development

```bash
make test
make examples
make ci
make publish-check
```

Publishing uses the same local release surface:

```bash
PYPI_TOKEN=... make publish
```

See the [release checklist](docs/development/release-checklist.md) for the full
publish flow.

## Status

Early implementation. Toolplane can register Python functions, explicit
`cli-to-py` wrappers, and FastMCP-backed MCP tools, then discover them, inspect
schemas, and execute agent-written Python through:

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

The current Pyodide+Deno smoke target works with pandas:

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

CLI tools are also available through ambient lazy proxies in the execution
namespace. Toolplane does not parse every binary at startup; it resolves a CLI
through `cli-to-py` only when code first calls it:

```python
result = await runtime.execute("""
status = await git.status(short=True).text()
files = await git.diff(name_only=True, _=["HEAD~1", "HEAD"]).lines()
return {"status": status, "files": files}
""")
```

For binaries that are not valid Python identifiers, use the `cli` root:

```python
result = await runtime.execute("""
version = await cli("docker-compose").version().text()
return version
""")
```

CLI tools can be exposed as capabilities during host setup:

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

MCP servers can be exposed the same way. An in-process FastMCP app:

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
value = await demo.add(a=2, b=3)
return value
""")
```

Or a standard `mcpServers` config, including stdio or remote HTTP servers:

```python
await runtime.register_mcp_config({
    "mcpServers": {
        "context7": {
            "url": "https://mcp.context7.com/mcp",
        }
    }
})
```

Registered MCP tools get canonical ids such as `mcp:context7/get_docs` and safe
Python aliases such as `context7_get_docs`. They are also available through a
scoped namespace, so agent-written code can call `context7.get_docs(...)`
without caring that the capability came from MCP.

Host Python helpers can be grouped the same way:

```python
from pathlib import Path
from toolplane import Toolplane

runtime = Toolplane()

def read_text(path: str) -> str:
    return Path(path).read_text()

runtime.register_python_namespace("repo", {"read_text": read_text})

result = await runtime.execute("""
text = await repo.read_text(path="README.md")
return text.splitlines()[0]
""")
```

See [examples](examples/README.md) for executable FastMCP in-process, stdio
config, and live Context7 remote MCP smokes.
