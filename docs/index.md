<section class="tp-hero" markdown>

<span class="tp-kicker">Programmable tool surface for code-mode agents</span>

# toolplane

A controlled Python runtime where CLIs, MCP tools, Python functions, and host
helpers are normalized into one namespace that agent-written code can compose.

```text
discover capabilities -> inspect schemas -> execute Python against a curated namespace
```

[:octicons-mark-github-16: View on GitHub](https://github.com/oneryalcin/toolplane){ .md-button .md-button--primary }
[Read the architecture](architecture.md){ .md-button }
[Toolplane and MCP](toolplane-mcp.md){ .md-button }

</section>

## Install

=== "uv"

    ```bash
    uv add toolplane
    ```

=== "pip"

    ```bash
    python -m pip install toolplane
    ```

Then create a runtime and register the capabilities your code-mode surface
should expose:

```python
from toolplane import Toolplane

runtime = Toolplane()
```

!!! tip "Local development"

    The repository examples still use `uv run --no-config --no-project
    --with-editable .` so they exercise the checkout directly. Application code
    can depend on the published `toolplane` package.

## Why It Exists

Agents are strongest when they can use code as the control plane for real work:
looping, branching, filtering, retrying, aggregating, and combining tools
without bouncing through one tool call at a time.

`toolplane` exists to make that code surface explicit, inspectable, and
controlled by the host application.

<div class="grid cards" markdown>

-   __CLI wrappers__

    Use `cli-to-py` commands as Python callables, including lazy ambient access
    for local development workflows.

-   __MCP tools__

    Register FastMCP-compatible servers and expose their tools as async Python
    functions with canonical capability ids.

-   __Host helpers__

    Add application-owned functions to the runtime while keeping credentials,
    authorization, and policy host-side.

</div>

!!! note "Host-controlled by design"

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

## Toolplane And MCP

Toolplane is not only an MCP proxy. Its intended role is a programmable
workbench over MCP tools, CLI wrappers, Python helpers, and libraries.

[Read the Toolplane and MCP boundary](toolplane-mcp.md){ .md-button }

## Current Slice

The first implementation can register Python functions, discover them, inspect
schemas, register explicit `cli-to-py` wrappers, and execute agent-written
Python through:

- `local_unsafe`: development-only in-process execution.
- `pyodide-deno`: experimental Pyodide-in-Deno sandbox execution with package
  loading and host bridge `call_tool` callbacks.

=== "Host setup"

    ```python
    from toolplane import Toolplane

    runtime = Toolplane()

    @runtime.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y
    ```

=== "Agent code"

    ```python
    result = await runtime.execute("""
    value = await call_tool("add", {"x": 2, "y": 3})
    return value
    """)
    ```

=== "Package-backed sandbox"

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

## Ambient CLI

For local/development code mode, CLI binaries on `PATH` are exposed lazily. The
runtime does not parse every CLI at startup; it resolves a binary through
`cli-to-py` when code first calls it.

```python
result = await runtime.execute("""
status = await git.status(short=True).text()
files = await git.diff(name_only=True, _=["HEAD~1", "HEAD"]).lines()
return {"status": status, "files": files}
""")
```

The `cli` root works for non-identifier binary names and explicit access:

```python
result = await runtime.execute("""
files = await cli.git.diff(name_only=True, _=["HEAD~1", "HEAD"]).lines()
version = await cli("docker-compose").version().text()
return {"files": files, "version": version}
""")
```

Hosts can disable this surface with `Toolplane(ambient_cli=False)` when they
need an explicit allowlist or a locked-down execution profile.

!!! warning "`local_unsafe` is a development backend"

    Ambient CLI access is intentionally useful for local shape validation. It is
    not a production sandbox for untrusted code.

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

## Next Steps

<div class="grid cards" markdown>

-   __Understand the boundary__

    Read [Architecture](architecture.md) for the registry, backend, bridge, and
    adapter split.

-   __Pick a backend__

    Read [Code Mode Backends](code-mode-backends.md) for the local unsafe,
    Pyodide+Deno, Docker, Modal, E2B, and Blaxel tradeoffs.

-   __Run examples__

    Start with [Ambient CLI](examples/ambient-cli.md) and
    [MCP Tools](examples/mcp-tools.md).

</div>
