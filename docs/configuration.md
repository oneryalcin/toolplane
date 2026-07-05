# Configuration

`toolplane.toml` is a deterministic host bootstrap recipe. It is for stable
runtime setup, not for agent snippets to register tools dynamically.

The imperative API remains first-class:

```python
runtime = Toolplane()
```

Use config when an application or project wants the same backend, CLI policy,
and MCP servers every time:

```python
from toolplane import Toolplane

runtime = await Toolplane.from_config("toolplane.toml")
```

## CLI Workflow

The CLI covers the whole lifecycle from empty directory to served facade:

```bash
toolplane init                  # write a starter toolplane.toml (safe defaults)
toolplane config check          # validate and summarize, no network calls
toolplane cli allow git gh rg   # switch to allowlist CLI policy
toolplane mcp add linear --command uvx --arg fastmcp-remote --arg <url>
toolplane mcp list              # what is configured, without connecting
toolplane doctor                # local prerequisites (backends, binaries)
toolplane mcp login linear      # prime an OAuth bridge interactively
toolplane mcp status            # probe configured servers (connects)
toolplane run snippet.py        # execute a snippet against the runtime
toolplane serve mcp             # serve the facade to MCP clients
```

`config check`, `doctor`, and `mcp list` never open network connections, so
they are safe in CI. `doctor` warns (without failing) when a config would
require `serve mcp --unsafe`, and fails when an allowlisted binary or a
required runtime like Deno is missing.

## Shape

```toml
[toolplane]
default_backend = "monty" # the default: sandboxed, no filesystem or network

[cli]
mode = "allowlist" # disabled (default) | allowlist | ambient
allow = ["git", "gh", "rg"]

[mcp.servers.linear]
url = "https://mcp.linear.app/mcp"
auth = "oauth"

[mcp.servers.local_docs]
command = "python"
args = ["examples/mcp_stdio_server.py"]
```

Toolplane-native TOML uses `[mcp.servers.<name>]`. Internally, Toolplane maps
that to FastMCP's `{"mcpServers": ...}` config shape.

Config defaults are safe: `default_backend = "monty"` and `cli.mode =
"disabled"`, so a fresh config can be served with `toolplane serve mcp` and no
`--unsafe` flag. On the monty backend, capabilities are called through flat
aliases (`math_multiply(...)`) or `call_tool(...)` rather than scoped
`math.multiply(...)` namespaces — see the backends page. Opting into
`local_unsafe` or `ambient` CLI mode is an explicit per-project choice.

## CLI Policy

CLI policy is enforced by the runtime, not only hidden from discovery.

| Mode | Behavior |
| --- | --- |
| `disabled` | No `cli` root and no top-level ambient CLI names. |
| `allowlist` | Only binaries in `allow` can be used through `cli.<name>`, `cli("name")`, or top-level aliases. |
| `ambient` | Development-friendly lazy CLI access for binaries on `PATH`. |

On the default `monty` backend, CLI access is flat: each allowed binary is a
top-level async function (`await git("status", short=True)`) and
`cli_run(binary, subcommand, options)` covers names that are not Python
identifiers. The `cli.<name>` object forms need `local_unsafe` or
`pyodide-deno`. The allowlist is enforced host-side by the bridge on every
call, regardless of backend.

In allowlist mode, non-identifier binaries can still be listed and used through
the explicit root:

```toml
[cli]
mode = "allowlist"
allow = ["git", "docker-compose"]
```

```python
version = await cli("docker-compose").version()
```

Only safe Python identifiers become top-level aliases.

!!! warning "`ambient` is for trusted local development"

    Do not expose ambient CLI mode through a client-facing MCP facade unless the
    project has explicitly chosen that risk.

## Result Store

Snippets can pass data between runs without routing it through the model's
context: `save_result(value, label=None)` returns a `res_` handle, and
`load_result(handle)` retrieves the value in a later run within the same
long-lived process. Values are canonicalized to JSON at save time; anything
that does not serialize is rejected loudly. See the
[design record](result-store-design.md) for the full contract.

The store is on by default with conservative caps, all tunable:

```toml
[results]
enabled = true
max_entries = 64
max_total_bytes = 33554432 # 32 MiB
max_entry_bytes = 8388608  # 8 MiB
ttl_seconds = 3600
```

The store is in-memory and session-scoped: handles die with the process, and
`toolplane run` builds a fresh runtime per invocation, so handles only survive
across `execute_code` calls inside one `serve mcp` or embedded runtime.
On multi-client transports (`serve mcp --transport http`) the store is
disabled automatically rather than shared across clients.

## MCP Servers

MCP server tables are preserved and passed through to FastMCP. Toolplane
validates its own config, but it does not try to own every MCP transport and
auth field.

Remote OAuth-style server:

```toml
[mcp.servers.linear]
url = "https://mcp.linear.app/mcp"
auth = "oauth"
```

!!! warning "Direct OAuth is ephemeral"

    FastMCP's direct `auth = "oauth"` config stores tokens in memory, and
    Toolplane never persists tokens itself — that boundary is permanent, not
    a version gap. Use a `fastmcp-remote` stdio bridge for persistent OAuth
    login; the bridge owns its own token cache.

Local stdio server:

```toml
[mcp.servers.local_docs]
command = "python"
args = ["examples/mcp_stdio_server.py"]
```

Environment-backed bearer token shape:

```toml
[mcp.servers.internal_docs]
url = "https://docs.example.com/mcp"

[mcp.servers.internal_docs.headers]
Authorization = "Bearer ${DOCS_MCP_TOKEN}"
```

The current config loader registers MCP servers through the existing
`register_mcp_config(...)` path. OAuth login commands and encrypted token
storage belong to the later Toolplane MCP facade/auth work.

## Non-Goals

The first config slice intentionally does not include:

- project/user config auto-discovery.
- Python helper import strings.
- custom backend imports.
- plugin or entrypoint discovery.
- secret management.
- OAuth browser login commands.

Those features need more policy and lifecycle decisions than the initial
deterministic bootstrap path.
