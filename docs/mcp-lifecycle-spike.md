# MCP Lifecycle CLI Spike

Issue: #23

Date: 2026-07-01

FastMCP version inspected locally: `3.4.2`

## Summary

Toolplane should build the MCP lifecycle CLI as thin config and operator UX over
FastMCP, not as a new OAuth or transport implementation.

The first implementation slice is emit-only:

```bash
toolplane mcp add linear --url https://mcp.linear.app/mcp --auth oauth
```

prints:

```toml
# add this to your toolplane.toml:
[mcp.servers.linear]
url = "https://mcp.linear.app/mcp"
auth = "oauth"
```

It should not mutate `toolplane.toml` in v1. Python has `tomllib` for reading
TOML, but no stdlib TOML writer. In-place editing would force either `tomlkit`
for comment-preserving round trips or a worse alternative. Emit-only keeps v1
dependency-free and cannot destroy comments or formatting in a human-edited
config file.

## FastMCP Findings

- `Client(..., auth="oauth")` and `OAuth(...)` are already implemented. OAuth
  covers browser authorization, PKCE, callback server, token exchange, caching,
  and refresh. See FastMCP's
  [OAuth Authentication](https://gofastmcp.com/clients/auth/oauth.md) docs and
  `fastmcp.client.auth.oauth.OAuth`.
- OAuth is relevant only for HTTP-based transports. Stdio MCP servers should
  use command/env configuration instead. See FastMCP's
  [OAuth Authentication](https://gofastmcp.com/clients/auth/oauth.md) and
  [Client Transports](https://gofastmcp.com/clients/transports.md) docs.
- `OAuth(token_storage=...)` accepts an `AsyncKeyValue` backend. Without one,
  FastMCP uses in-memory token storage and warns that tokens are lost on restart.
  Source checked: `fastmcp.client.auth.oauth.OAuth`.
- FastMCP's encrypted persistent storage example uses `DiskStore` wrapped in
  `FernetEncryptionWrapper`. See FastMCP's
  [OAuth token storage](https://gofastmcp.com/clients/auth/oauth.md#token-storage)
  docs.
- FastMCP config `auth = "oauth"` is not enough for persistent Toolplane tokens:
  `RemoteMCPServer.to_transport()` passes the string to the transport, and the
  transport constructs `OAuth(...)` without a `token_storage`. Toolplane must
  construct and inject `OAuth(token_storage=...)` later if it wants durable
  tokens for direct remote MCP connections. Sources checked:
  `fastmcp.mcp_config.RemoteMCPServer`,
  `fastmcp.client.transports.http.StreamableHttpTransport`, and
  `fastmcp.client.transports.sse.SSETransport`.
- `fastmcp-remote` is already the stdio bridge for remote MCP hosts. It
  auto-enables OAuth for HTTPS, stores tokens under `~/.fastmcp/remote` by
  default, supports `FASTMCP_REMOTE_CONFIG_DIR`, and has `--resource` for token
  isolation. See FastMCP's
  [fastmcp-remote](https://gofastmcp.com/clients/fastmcp-remote.md) docs.
- FastMCP `MCPConfig` is JSON-oriented for file writes. Its `write_to_file()`
  writes JSON, so it does not solve Toolplane's TOML editing problem. See
  FastMCP's
  [`mcp_config`](https://gofastmcp.com/python-sdk/fastmcp-mcp_config.md) docs
  and source checked: `fastmcp.mcp_config.MCPConfig.write_to_file`.
- FastMCP multi-server clients prefix tool names by server. Toolplane's MCP
  adapter already maps configured MCP tools into canonical ids, aliases, and
  scoped namespaces, then normalizes FastMCP `.data` results back to Python
  values for `execute_code`. See FastMCP's
  [Client](https://gofastmcp.com/clients/client.md),
  [Calling Tools](https://gofastmcp.com/clients/tools.md), and
  [Client Transports](https://gofastmcp.com/clients/transports.md#multi-server-configuration)
  docs. Toolplane source checked: `src/toolplane/adapters/mcp.py`.

## Evidence Map

Official FastMCP docs read:

- [OAuth Authentication](https://gofastmcp.com/clients/auth/oauth.md)
- [fastmcp-remote](https://gofastmcp.com/clients/fastmcp-remote.md)
- [Client Transports](https://gofastmcp.com/clients/transports.md)
- [The FastMCP Client](https://gofastmcp.com/clients/client.md)
- [Calling Tools](https://gofastmcp.com/clients/tools.md)
- [Client Commands](https://gofastmcp.com/cli/client.md)
- [`fastmcp.mcp_config`](https://gofastmcp.com/python-sdk/fastmcp-mcp_config.md)

FastMCP source inspected from installed package `fastmcp==3.4.2`:

- `fastmcp.client.auth.oauth.OAuth`
- `fastmcp.client.auth.oauth.TokenStorageAdapter`
- `fastmcp.client.transports.http.StreamableHttpTransport`
- `fastmcp.client.transports.sse.SSETransport`
- `fastmcp.mcp_config.MCPConfig`
- `fastmcp.mcp_config.RemoteMCPServer`
- `fastmcp.mcp_config.StdioMCPServer`

Toolplane source inspected:

- `src/toolplane/config.py`
- `src/toolplane/adapters/mcp.py`
- `tests/test_config.py`
- `tests/test_mcp_adapter.py`

## V1 Command Decisions

### `toolplane mcp add`

V1 is emit-only. It prints a self-documenting TOML block to stdout and writes
nothing.

Remote URL form:

```bash
toolplane mcp add linear --url https://mcp.linear.app/mcp --auth oauth
```

Output:

```toml
# add this to your toolplane.toml:
[mcp.servers.linear]
url = "https://mcp.linear.app/mcp"
auth = "oauth"
```

Stdio command form:

```bash
toolplane mcp add linear --command npx --arg -y --arg mcp-remote --arg https://mcp.linear.app/mcp
```

Output:

```toml
# add this to your toolplane.toml:
[mcp.servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
```

Name validation for v1 should allow only `[A-Za-z0-9_-]+`. This keeps the
unquoted TOML table header safe and avoids dotted or quoted keys in the first
slice.

`--auth` should be explicit. Do not silently infer OAuth from HTTPS in
Toolplane's direct config path because FastMCP direct config does not persist
tokens without a supplied token store. For stdio bridge snippets using
`fastmcp-remote`, FastMCP's bridge owns its own OAuth defaults and token store.

### `toolplane mcp status`

Implemented after `mcp add`. Status is a deterministic connection check over
configured servers using FastMCP `Client` directly, not `Toolplane.from_config`.
It reads config, selects one or more servers, strips FastMCP `auth` from the
probe config, and lists tools under a per-server timeout.

Status must not open a browser or write OAuth tokens. That guarantee is
structural: the probe client is never constructed with FastMCP `auth=...`.
Servers that require credentials are reported as `auth_required` or `error` in
the output.

Exit codes reflect whether the status command itself ran. Bad arguments,
unknown server names, or malformed config return non-zero. A reachable config
with a down, timed-out, or auth-protected server returns zero and reports that
server state as data.

Stdio status checks execute the configured command in order to list tools. That
is the honest connection check, but it means status can run user-configured
processes and those processes may write diagnostics to stderr.

### `toolplane mcp login/logout`

Do not implement until Toolplane chooses token storage.

Direct remote login requires Toolplane to construct `OAuth(token_storage=...)`
instead of passing plain `auth = "oauth"` through TOML. The likely storage
choice is encrypted disk storage via the `AsyncKeyValue` interface. Keychain is
not needed for v1.

`fastmcp-remote` already covers a separate stdio bridge lifecycle and stores
its own tokens under `~/.fastmcp/remote`; Toolplane should not duplicate that
unless it needs tighter integration later.

## FastMCP Remote Bridge Spike

Date: 2026-07-01

Test the bridge path first because it is the delete-code hypothesis: if
`fastmcp-remote` works as a normal stdio MCP server, Toolplane may not need to
own OAuth token storage for remote servers in v1.

### Upstream Behavior

FastMCP's `fastmcp-remote` docs state that HTTPS servers use OAuth by default
and that the first connection opens the browser-based OAuth flow when the server
requires authentication. They also document the relevant lifecycle controls:

- `--auth-timeout` bounds how long the bridge waits for the OAuth callback.
- `--resource` isolates token storage for one remote identity.
- `FASTMCP_REMOTE_CONFIG_DIR` moves the bridge token directory.
- `--auth none` disables OAuth for unauthenticated HTTP development servers.

The installed `uvx fastmcp-remote --help` output in this environment matches
those options.

### No-Auth Bridge Proof

A local FastMCP HTTP server was bridged through stdio with:

```toml
[mcp.servers.bridge]
command = "uvx"
args = [
  "fastmcp-remote",
  "http://127.0.0.1:8791/mcp",
  "--auth",
  "none",
  "--silent",
]
```

Observed results:

```text
toolplane mcp status --config /tmp/toolplane-fastmcp-remote-bridge.toml --timeout 20
-> bridge: ok transport=stdio auth=none tools=1
```

`Toolplane.from_config` registered the bridged tool as `mcp:bridge/add`, and
`execute_code` successfully called it:

```python
return await bridge.add(x=20, y=22)
```

Result: `42`.

Conclusion: from Toolplane's perspective, a `fastmcp-remote` bridge is ordinary
stdio MCP after it initializes.

### Unprimed Auth Bridge Behavior

An unprimed Linear bridge was tested with an isolated token directory, disabled
browser command, and short auth timeout:

```toml
[mcp.servers.linear_bridge]
command = "uvx"
args = [
  "fastmcp-remote",
  "https://mcp.linear.app/mcp",
  "--auth-timeout",
  "1",
  "--resource",
  "toolplane-spike",
  "--silent",
]

[mcp.servers.linear_bridge.env]
FASTMCP_REMOTE_CONFIG_DIR = "/tmp/toolplane-fastmcp-remote-linear-empty"
BROWSER = "/usr/bin/false"
```

Observed result:

```text
toolplane mcp status --config /tmp/toolplane-fastmcp-remote-linear-unprimed.toml --timeout 5
-> linear_bridge: error transport=stdio auth=none
   detail=Client failed to connect: OAuth callback timed out after 1.0 seconds
```

Toolplane returned exit 0 because status ran successfully and reported the
server failure as data. The bridge wrote FastMCP remote metadata into the
isolated temp token directory during the failed OAuth attempt.

Do not put this unprimed behavior in automated tests: a real unprimed
`fastmcp-remote` HTTPS bridge can open a browser and otherwise waits up to the
configured auth timeout.

### Primed Linear Bridge Proof

Linear was then primed once through the bridge using an isolated token
directory:

```toml
[mcp.servers.linear_bridge]
command = "uvx"
args = [
  "fastmcp-remote",
  "https://mcp.linear.app/mcp",
  "--auth-timeout",
  "180",
  "--resource",
  "toolplane-linear-spike",
  "--silent",
]

[mcp.servers.linear_bridge.env]
FASTMCP_REMOTE_CONFIG_DIR = "/tmp/toolplane-fastmcp-remote-linear-prime"
```

After browser consent, `toolplane mcp status` reported:

```text
linear_bridge: ok transport=stdio auth=none tools=47
```

`Toolplane.from_config` registered the Linear tools, `search("issue")` returned
Linear capabilities such as `mcp:linear_bridge/list_issues`, and `execute_code`
successfully called the scoped namespace:

```python
issues = await linear_bridge.list_issues(query="", limit=1)
return {
    "type": type(issues).__name__,
    "count": len(issues) if hasattr(issues, "__len__") else None,
}
```

Observed execution result:

```json
{"type": "str", "count": 1193}
```

The Linear tool returned a string payload, so the count is string length, not
issue count. The important product proof still holds: authenticated Linear
tools reached Toolplane's namespace and were callable inside `execute_code`.

### Slice-2 Decision

V1 remote MCP auth uses the `fastmcp-remote` stdio bridge path. Toolplane will
not build direct `OAuth(token_storage=...)` storage for v1.

Rationale:

- Toolplane needs no OAuth protocol or token-storage code for the bridge path.
- `mcp add --command ... fastmcp-remote ...` already emits the right config
  shape.
- `mcp status`, `Toolplane.from_config`, search, and `execute_code` already
  work once the bridge is primed.
- Logout and token cleanup belong to `fastmcp-remote`'s token directory for this
  path.

The sharp edge is unprimed HTTPS bridges: spawning them can initiate OAuth. The
operator contract should be explicit:

- Prime the bridge deliberately before relying on status or execute workflows.
- Use `--auth-timeout` to avoid long hangs.
- Use `--resource` and `FASTMCP_REMOTE_CONFIG_DIR` when isolation matters.

Direct Toolplane-managed OAuth is deferred. Revisit it only when a concrete
trigger appears, such as a remote MCP server that cannot work through
`fastmcp-remote`, a headless deployment that cannot rely on browser priming, or
a product requirement for Toolplane-owned logout, token encryption, or token
auditing semantics.

### `toolplane doctor`

Doctor should reuse `EffectivePolicy`; it must not re-derive backend or CLI
policy. The policy dataclass exists so operator surfaces share one definition.

## End-to-End Proof Target

The product proof is not "OAuth opens a browser." It is:

```text
configured MCP server
  -> FastMCP client connects
  -> Toolplane registers remote tools
  -> search_capabilities shows them
  -> execute_code can call them through the Toolplane namespace
```

This has already been proven for local stdio MCP servers. The remaining proof is
an authenticated remote or remote-like HTTP server. A live third-party OAuth run
is optional for the spike; most facts are settled by FastMCP source and docs.
When empirical auth proof is needed, prefer a local throwaway FastMCP auth
server before depending on a Linear account.

## First Implementation Slice Acceptance

For `toolplane mcp add` emit-only v1:

- Prints a header comment: `# add this to your toolplane.toml:`.
- Emits a valid `[mcp.servers.<name>]` TOML block to stdout.
- Does not mutate `toolplane.toml`.
- Rejects invalid server names before printing.
- Supports remote URL form with optional explicit `--auth oauth`.
- Supports stdio command form with repeated `--arg`.
- Tests feed stdout back through `load_toolplane_config`.
- Tests feed `config.mcp.to_fastmcp_config()` into `fastmcp.mcp_config.MCPConfig`.
- Tests cover URL quoting and args list rendering; use structured escaping, not
  ad hoc string concatenation.

If in-place editing is added later:

- Use `tomlkit` for comment-preserving TOML edits.
- Error if the server already exists.
- Require `--force` to replace an existing server.
- Never silently overwrite a user-authored config entry.
