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

Flags that must precede the subcommand (`git -C <path>`,
`kubectl --context`) go in the `_global` dict on any of those forms —
`await git("log", _global={"C": "/path/to/repo"})` renders
`git -C /path/to/repo log` (cli-to-py ≥ 0.2).

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

## Session

On the monty backend, variables persist across `execute_code` runs within
one served session: assignments and function definitions from one run are
available in the next, like notebook cells. On by default:

```toml
[session]
enabled = true
max_memory_mb = 512 # cap on the accumulated interpreter heap
```

The contract, chosen from the [#77 spike](monty-session-spike.md):

- A failed run keeps the session; a **timed-out** run is rolled back to
  its pre-run namespace (a cancelled monty run can otherwise leave partial
  state or poison the interpreter — pydantic/monty#533). Rollback covers
  VM state only: capability calls, CLI commands, and saves the run made
  before timing out stand, and the timeout error says so.
- `await reset_session()` clears the namespace after the current run;
  results and artifacts are unaffected.
- The memory cap is enforced cumulatively; hitting it is a catchable
  `MemoryError` and the session survives — reassign large variables to
  `None` (monty has no `del`) or reset.
- Snippets that assign to a Toolplane binding name (`save_result = ...`)
  are rejected up front: in a session the assignment would persist and
  mask the binding — including `reset_session` itself — until reset.
- Per-run `inputs` are rejected in session mode: everything fed to a
  session persists, so accepting them would silently turn one-shot host
  data (including secrets) into durable state. Seed state inside a
  snippet instead, or disable sessions for input-driven runs.

Like the stores, sessions are per-process state: multi-client transports
(`serve mcp --transport http`) disable them automatically rather than share
one namespace (and one interpreter lock) across clients. That automatic
gate lives in the config-driven path (`serve mcp`,
`build_mcp_facade_from_config`); if you construct `Toolplane()` yourself
and serve it on a multi-client transport via `build_mcp_facade`, pass
`Toolplane(sessions=False)` — sessions default on for the default backend
set. Passing `backends=[...]` explicitly means you own session mode:
construct `MontyBackend(session=True)` yourself if you want it.

## Result Store

With sessions enabled, plain variables already persist between runs; the
store remains the seam for values that must survive a session reset, cross
a backend override, or be read directly as an MCP resource.

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

## Audit Log

An opt-in JSONL event stream recording what actually ran: every run
(snippet hash, backend, duration, outcome), every dispatch through the
bridge (capability or CLI binary, duration, error type), and every
escalation decision (granted / declined / abandoned / error). Everything
flows through one choke point, so the log is structurally complete.

```toml
[audit]
enabled = true
# path = "~/.toolplane/audit.jsonl"  # the default when enabled
```

**Events are metadata only.** Call arguments and results are never
written — payloads can carry secrets. What a human approved, when, and
what the snippet touched is on the record; the data that moved is not.

Run and dispatch events carry a `run_id`; escalation events do not —
they can resolve after their run has ended (an abandoned prompt's
cancellation lands on a later event-loop tick), so instead of a
possibly-wrong id they carry none: correlate them through the run's
`run_end.escalations_cancelled` list and timestamps.

The intended consumer is you and a terminal:

```bash
tail -f ~/.toolplane/audit.jsonl | jq .

# every CLI invocation with its outcome
jq 'select(.event == "dispatch" and .binary)' ~/.toolplane/audit.jsonl

# who approved what, this session
jq 'select(.event == "escalation")' ~/.toolplane/audit.jsonl

# slow runs
jq 'select(.event == "run_end" and .duration_ms > 5000)' ~/.toolplane/audit.jsonl
```

A write failure (unwritable path, full disk) disables the log with one
stderr warning; it never breaks a run.

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

!!! note "OAuth tokens persist encrypted"

    `auth = "oauth"` wires FastMCP's own OAuth helper to an encrypted
    token store at `~/.toolplane/oauth` (Fernet at rest, via FastMCP's
    storage wrappers). The encryption key lives in the OS keyring;
    `TOOLPLANE_STORAGE_KEY` overrides it on headless hosts, and with
    neither available Toolplane refuses loudly rather than writing
    plaintext. Prime once with `toolplane mcp login <name>` — one browser
    consent, then every status probe, serve, and run reuses the stored
    tokens. Toolplane writes no token-handling code and rolls no crypto;
    losing the key just means one re-consent.

Local stdio server:

```toml
[mcp.servers.local_docs]
command = "python"
args = ["examples/mcp_stdio_server.py"]
```

## Secrets

Config values in MCP `headers` and stdio `env` tables can reference
secrets instead of holding them, so `toolplane.toml` stays committable:

```toml
[mcp.servers.internal_docs]
url = "https://docs.example.com/mcp"

[mcp.servers.internal_docs.headers]
Authorization = "keyring://internal-docs-token"  # or "env://DOCS_MCP_TOKEN"
```

- `keyring://<name>` reads the OS keyring; store values with
  `toolplane secret set <name>` (the value is read from stdin or an
  interactive prompt — never from argv, which process lists leak).
  `toolplane secret list` shows names only; `toolplane secret rm <name>`
  deletes.
- `env://<VAR>` reads the process environment.
- A missing secret fails loudly at registration with the command that
  fixes it — never a silently-empty credential.

## Non-Goals

The config surface intentionally does not include:

- project/user config auto-discovery.
- Python helper import strings.
- custom backend imports.
- plugin or entrypoint discovery.

Those features need more policy and lifecycle decisions than the current
deterministic bootstrap path.
