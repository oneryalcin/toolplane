# Toolplane And MCP

Toolplane is a programmable workbench over tools. MCP is one important source of
those tools, but Toolplane should not become only an MCP proxy.

Implementation tracking issue:
[#16](https://github.com/oneryalcin/toolplane/issues/16).

The durable product boundary is:

```text
toolplane
  runtime, registry, config, policy, auth wiring, and execution backends

toolplane-mcp
  optional MCP facade that lets Claude Code, Codex, Cursor, and other MCP
  clients use a configured Toolplane runtime
```

## Product Definition

MCP gives agents tools. Toolplane gives agents a Python workbench over tools.

That distinction matters when a workflow needs code-shaped composition:

```python
issues = await linear.list_issues(query="label:bug")
docs = await context7.query_docs("FastMCP OAuth")
diff = await git.diff(name_only=True, _=["HEAD~1", "HEAD"]).lines()

return {
    "issue_count": len(issues),
    "docs_prefix": docs[:500],
    "changed_files": diff,
}
```

The agent writes normal Python. The host controls which capabilities exist, how
credentials are acquired, which CLIs are available, and which backend executes
the code.

## Why This Is Not Just MCP Code Mode

FastMCP Code Mode is a useful reference point. It wraps one MCP server catalog
with discovery and execution meta-tools.

If a user has ten MCP servers and each server enables its own code mode, the
client still sees ten separate code-mode islands:

```text
linear.search
linear.get_schema
linear.execute

github.search
github.get_schema
github.execute

context7.search
context7.get_schema
context7.execute
```

Toolplane's target shape is one workbench over a unified capability registry:

```text
toolplane.search_capabilities
toolplane.get_capability_schemas
toolplane.execute_code
```

Inside `execute_code`, the namespace can contain capabilities from MCP servers,
CLI wrappers, Python functions, host helpers, and Python packages.

```python
issues = await linear.list_issues(query="assignee:me")
repo_status = await git.status(short=True).text()
table = pandas.DataFrame(issues)

return table[["identifier", "title"]].head(10).to_dict("records")
```

That is the product value: not "one MCP server to call other MCP servers", but a
controlled Python runtime where multiple capability sources become composable.

## User Flow

The first stable command-line surface should optimize for explicit setup:

```bash
toolplane init
toolplane mcp add linear --url https://mcp.linear.app/mcp --auth oauth
toolplane mcp login linear
toolplane cli allow git gh rg
toolplane doctor
```

This writes project config:

```toml
[cli]
mode = "allowlist"
allow = ["git", "gh", "rg"]

[mcpServers.linear]
url = "https://mcp.linear.app/mcp"
auth = "oauth"
```

Then a user can connect Toolplane to an MCP client:

```bash
codex mcp add toolplane -- toolplane serve mcp --config ./toolplane.toml
```

or, for Claude Code:

```bash
claude mcp add toolplane -- toolplane serve mcp --config ./toolplane.toml
```

A later Claude plugin can make this lower friction:

```text
/plugin install toolplane@...
```

The plugin is distribution sugar. The core product remains the configured
Toolplane runtime.

## Auth Boundary

Remote MCP authentication belongs to the host process, not to agent-written
Python.

For OAuth-backed remote MCP servers:

```toml
[mcpServers.linear]
url = "https://mcp.linear.app/mcp"
auth = "oauth"
```

Toolplane should delegate the actual MCP OAuth flow to FastMCP's client layer:

- browser-based authorization code flow with PKCE.
- dynamic client registration when the server supports it.
- token refresh handled by the MCP client implementation.
- persistent token storage owned by Toolplane.

Toolplane should provide host commands around that lower-level machinery:

```bash
toolplane mcp login linear
toolplane mcp status
toolplane mcp logout linear
```

For non-interactive environments, secrets should be referenced, not stored in
plain TOML:

```toml
[mcpServers.linear]
url = "https://mcp.linear.app/mcp"

[mcpServers.linear.auth]
type = "bearer"
env = "LINEAR_MCP_TOKEN"
```

Rules:

- Agent code never receives raw OAuth tokens, refresh tokens, or API keys.
- Toolplane does not silently borrow Claude Code or Codex's private MCP auth
  sessions.
- Headless execution requires pre-login or explicit environment-backed bearer
  credentials.
- Token storage must be encrypted or delegated to the operating system keychain
  before it is marketed as a production feature.

## What Toolplane-MCP Should Expose

`toolplane-mcp` should expose a small meta-tool surface:

```text
search_capabilities(query, tags?)
get_capability_schemas(names, detail?)
execute_code(code, backend?, packages?)
```

Maybe later:

```text
list_capabilities(tags?)
explain_policy()
```

It should not re-export every underlying tool as a flat MCP catalog by default.
That recreates context bloat and loses the workbench model.

## Non-Goals

Toolplane should not:

- become a full agent framework.
- pretend it can automatically access sibling MCP servers already configured in
  Claude Code, Codex, Cursor, or another client.
- become a general credential manager.
- mutate a user's Claude/Codex config without an explicit install command.
- expose ambient local Python and arbitrary CLI execution through MCP by
  default.
- implement OAuth itself when the MCP client library already owns the protocol.

## Dependency Order

`toolplane-mcp` should wait until config and policy are real:

```text
config-driven runtime setup
  -> CLI policy: disabled, allowlist, ambient
  -> MCP server config loading
  -> MCP auth command surface
  -> toolplane serve mcp
  -> client install helpers
  -> Claude plugin packaging
```

The first public MCP facade should default to safe policy:

```toml
[cli]
mode = "disabled"
```

or:

```toml
[cli]
mode = "allowlist"
allow = ["git", "gh", "rg"]
```

`ambient` CLI mode is useful for local development, but it should be an explicit
project choice before Toolplane is exposed as an MCP server.

## Design Test

A good Toolplane workflow should answer yes to all of these:

- Can the agent compose more than one capability source in one Python snippet?
- Can the user inspect what capabilities are available before execution?
- Can the host explain and enforce CLI, MCP, and backend policy?
- Can credentials stay outside the agent-visible namespace?
- Can the same configured runtime be used directly from Python and through MCP?

If the answer is no, the feature probably belongs in a narrower adapter or a
later iteration.
