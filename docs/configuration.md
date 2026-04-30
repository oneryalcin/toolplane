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

## Shape

```toml
[toolplane]
default_backend = "local_unsafe"

[cli]
mode = "allowlist" # disabled | allowlist | ambient
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

## CLI Policy

CLI policy is enforced by the runtime, not only hidden from discovery.

| Mode | Behavior |
| --- | --- |
| `disabled` | No `cli` root and no top-level ambient CLI names. |
| `allowlist` | Only binaries in `allow` can be used through `cli.<name>`, `cli("name")`, or top-level aliases. |
| `ambient` | Development-friendly lazy CLI access for binaries on `PATH`. |

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
