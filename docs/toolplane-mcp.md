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

The first stable command-line surface should optimize for explicit setup. This
section is the target lifecycle. Today, `toolplane mcp add` emits config
snippets, `toolplane mcp status` checks configured servers, and
`toolplane serve mcp` serves the configured facade; login, CLI allow, doctor, and
in-place config writing are still planned.

```bash
toolplane init
toolplane mcp add linear --url https://mcp.linear.app/mcp
toolplane mcp login linear
toolplane cli allow git gh rg
toolplane doctor
```

The current `mcp add` command prints a block for the user to add to project
config:

```toml
# add this to your toolplane.toml:
[mcp.servers.linear]
url = "https://mcp.linear.app/mcp"
```

Toolplane should also accept stdio-style upstream server definitions, including
bridges used by stdio-only hosts:

```bash
toolplane mcp add linear --command npx --arg -y --arg mcp-remote --arg https://mcp.linear.app/mcp
```

which maps to:

```toml
[mcp.servers.linear]
command = "npx"
args = ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
```

The current `mcp status` command reads the project config and reports each
configured MCP server as data:

```bash
toolplane mcp status --config ./toolplane.toml
```

Status probes do not construct FastMCP OAuth providers, so they do not open a
browser or write OAuth tokens. A protected remote server is reported as
`auth_required` or `error` instead. Stdio servers are checked by executing the
configured command and listing tools, so status can surface child-process
diagnostics from the configured server.

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

## Walking Skeleton

Build `toolplane serve mcp` before the full auth lifecycle. The facade is the
highest-information slice: it proves whether clients can use Toolplane as one
MCP server that offers progressive discovery and code execution over a curated
namespace.

The current implementation provides this no-auth skeleton, exposes only the
three Toolplane meta-tools, and guards the config-backed MCP facade from unsafe
defaults. On successful `toolplane serve mcp` startup, it prints the effective
backend, CLI, MCP-server, and unsafe-override policy to stderr for the operator.
It does not yet include MCP auth login, durable token storage, or client install
helpers.

The first slice should deliberately avoid remote auth:

```bash
toolplane serve mcp --config ./toolplane.toml
```

For trusted local development with the `local_unsafe` backend or ambient CLI
policy, the operator must opt in explicitly:

```bash
toolplane serve mcp --config ./toolplane.toml --unsafe
```

Validate it against a config with only no-auth capabilities, such as host Python
helpers, allowlisted CLI binaries, and a local stdio MCP server. Then connect an
MCP client and run:

```text
search_capabilities -> get_capability_schemas -> execute_code
```

This skeleton is not the production-ready completion state for this issue. It is
the early risk-reduction step before implementing OAuth lifecycle and durable
token storage.

## Auth Boundary

Remote MCP authentication belongs to the host process, not to agent-written
Python.

For remote MCP servers, adding a server and authenticating to it should remain
separate operations. The add command records how to reach the server. The login
command discovers or negotiates the required authentication flow and stores
credentials outside project TOML.

For a remote server:

```toml
[mcp.servers.linear]
url = "https://mcp.linear.app/mcp"
```

Toolplane should delegate the actual MCP OAuth flow to FastMCP's client layer,
while owning the durable token storage and lifecycle around that lower-level
machinery:

- first verify the current FastMCP client behavior for remote MCP OAuth,
  including whether `Client(..., auth="oauth")` can authenticate to real servers
  and which token storage hooks are available.
- browser-based authorization code flow with PKCE.
- dynamic client registration when the server supports it.
- token refresh handled by the MCP client implementation.
- persistent token storage owned by Toolplane and injected into FastMCP's OAuth
  provider.

Toolplane should provide host commands around that lower-level machinery:

```bash
toolplane mcp login linear
toolplane mcp status
toolplane mcp logout linear
```

For non-interactive environments, secrets should be referenced, not stored in
plain TOML:

```toml
[mcp.servers.linear]
url = "https://mcp.linear.app/mcp"

[mcp.servers.linear.auth]
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
- The first public MCP facade should not rely on FastMCP's default in-memory
  OAuth token storage, because that requires users to reauthenticate after every
  Toolplane process restart.
- `toolplane.toml` should describe upstream MCP servers and policy, not contain
  long-lived secrets.

## What Toolplane-MCP Should Expose

`toolplane-mcp` currently exposes a small meta-tool surface:

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

## FastMCP CodeMode Decision

FastMCP's experimental `CodeMode` transform already provides the same broad
shape as the Toolplane facade: staged discovery, schema lookup, and code
execution through a sandbox provider.

A throwaway spike showed this is technically viable:

- CodeMode can own the client-visible `search`, `get_schema`, and `execute`
  tools.
- Toolplane-backed capabilities can be adapted into the hidden FastMCP catalog.
- A custom sandbox provider can inject Toolplane-style scoped namespaces such as
  `demo.add(...)` while delegating calls through CodeMode's `external_functions`.

The spike also exposed the deciding semantic difference:

- Scalar tool results are wrapped as `{"result": value}` inside CodeMode's
  intra-snippet execution path. This means an agent composing normal Python would
  receive `{"result": 12}` from a scalar tool call instead of `12`.
- Toolplane's custom execution path preserves normal Python values inside the
  snippet, which is central to the product contract. At the final MCP boundary,
  Toolplane returns an explicit `ExecutionResult` object such as
  `{"value": 12, ...}`.

The product decision is therefore: keep Toolplane's custom execute/namespace
core, and treat CodeMode as a parts bin for commodity pieces where it is clearly
better:

- BM25-style search instead of Toolplane's current token-count baseline.
- Discovery-tool patterns.
- Execute-time tool-call caps.

Do not adopt CodeMode wholesale unless FastMCP makes scalar unwrapping
configurable or Toolplane intentionally changes its Python-first composition
semantics.

Remaining integration caveats:

- CodeMode's discovery and schema rendering are FastMCP-native, not
  Toolplane-native.
- Tool names exposed to CodeMode need safe FastMCP wrapper names, so canonical
  Toolplane ids and aliases need a stable mapping.
- CodeMode is currently documented as experimental, so depending on it makes
  FastMCP's transform API part of Toolplane's compatibility surface.

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

`toolplane-mcp` should front-load the facade skeleton once config and policy are
real, then harden auth before calling the public surface complete:

```text
config-driven runtime setup
  -> CLI policy: disabled, allowlist, ambient
  -> MCP server config loading
  -> minimal toolplane serve mcp walking skeleton without remote auth
  -> FastMCP OAuth/token-storage behavior verification
  -> MCP auth command surface and durable token storage
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
