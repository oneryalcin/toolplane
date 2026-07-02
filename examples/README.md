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
uv run --no-project --with-editable . python examples/multi_server.py
uv run --no-project --with-editable . python examples/from_config.py
```

## Multiple servers, mixed transports and auth

`multi_server.py` registers two local stdio MCP servers under one runtime and
composes tools from *both* in a single `execute_code` snippet — the point of
Toolplane: agent code doesn't care which server a tool came from.

`multi_server.toml` shows the same shape as a config file, mixing all the
supported postures per server (stdio/remote transport; none/bearer/OAuth auth).
The two local servers in it actually respond, so you can health-check them:

```bash
toolplane mcp status --config examples/multi_server.toml
# - math: ok transport=stdio auth=none tools=1
# - text: ok transport=stdio auth=none tools=1
```

The remote/OAuth/bearer blocks are commented out because they need external
setup - an OAuth server is added via the `fastmcp-remote` bridge and primed once
(`toolplane mcp login <name>`); a bearer server keeps its token in an
environment variable via `--header`. See the comments in the file for each form.

The Context7 example uses the live remote MCP endpoint, so it is intentionally
not part of `make examples`:

```bash
uv run --no-project --with-editable . python examples/context7_remote.py
```

For a more explicit walkthrough of scoped namespaces, canonical ids, flat
aliases, host Python helpers, and live Context7 MCP calls:

```bash
uv run --no-project --with-editable . python examples/scoped_namespaces_context7.py
```

The full mixed example also requires Deno/Pyodide package loading and live
Context7 access:

```bash
uv run --no-project --with-editable . python examples/mixed_capability_report.py
```

The MCP client capability probe is an instrument, not a demo: point real MCP
clients (Claude Code, Codex) at it to measure their protocol feature support.
Findings and invocation recipes live in `docs/mcp-client-capability-spike.md`:

```bash
uv run --no-project --with-editable . python examples/mcp_client_probe.py --self-test
```
