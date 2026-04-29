# Examples

These examples are executable smoke tests for the core Toolplane idea: MCP
tools become normal async Python callables inside code mode.

Run the deterministic examples:

```bash
make examples
```

Run one example directly:

```bash
uv run --no-project --with-editable . python examples/ambient_cli_git.py
uv run --no-project --with-editable . python examples/fastmcp_in_process.py
uv run --no-project --with-editable . python examples/mcp_stdio_config.py
```

The Context7 example uses the live remote MCP endpoint, so it is intentionally
not part of `make examples`:

```bash
uv run --no-project --with-editable . python examples/context7_remote.py
```

The full mixed example also requires Deno/Pyodide package loading and live
Context7 access:

```bash
uv run --no-project --with-editable . python examples/mixed_capability_report.py
```
