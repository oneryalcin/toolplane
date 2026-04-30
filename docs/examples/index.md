# Examples

The examples are intentionally small and executable. They are meant to prove the
runtime contract rather than demonstrate a full agent framework.

Run the deterministic examples locally:

```bash
make examples
```

The current examples cover:

| Example | What it proves |
| --- | --- |
| `ambient_cli_git.py` | Agent code can call local CLI binaries lazily through Python. |
| `fastmcp_in_process.py` | FastMCP tools become Toolplane capabilities and scoped namespaces. |
| `mcp_stdio_config.py` | Standard `mcpServers` stdio config can register MCP-backed capabilities. |
| `from_config.py` | `Toolplane.from_config(...)` can bootstrap CLI policy and MCP servers from TOML. |

!!! tip "Keep examples executable"

    If an example explains a runtime feature, keep it wired into `make examples`
    unless it needs live network credentials or a remote service.

## Optional Live Smoke

The Context7 example talks to a remote MCP service and is intentionally opt-in:

```bash
uv run --no-config --no-project --with-editable . python examples/context7_remote.py
```

Use it when you want to verify the real remote MCP transport path.
