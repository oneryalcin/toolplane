<div align="center">

<h1>
  <img alt="toolplane. Your agent writes one program. Its tools are the functions." src="https://raw.githubusercontent.com/oneryalcin/toolplane/main/docs/assets/readme/hero.webp">
</h1>

**One sandboxed Python namespace for your agent's tools: MCP servers, command-line programs and your own functions.<br>The agent writes one short program instead of a hundred tool calls.**

[![PyPI version](https://img.shields.io/pypi/v/toolplane?style=flat-square)](https://pypi.org/project/toolplane/)
[![Python versions](https://img.shields.io/pypi/pyversions/toolplane?style=flat-square)](https://pypi.org/project/toolplane/)
[![CI](https://img.shields.io/github/actions/workflow/status/oneryalcin/toolplane/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/oneryalcin/toolplane/actions/workflows/ci.yml)
[![MIT license](https://img.shields.io/badge/license-MIT-555555?style=flat-square)](https://github.com/oneryalcin/toolplane/blob/main/LICENSE)

**[Docs](https://oneryalcin.github.io/toolplane/) · [Paper](https://github.com/oneryalcin/toolplane/blob/main/paper/main.pdf) · [Talk (9 min)](https://github.com/oneryalcin/toolplane/releases/tag/talk-video-2026-10) · [Benchmark](https://oneryalcin.github.io/toolplane/code-mode-benchmark/)**

[Why](#why) · [Quickstart](#quickstart) · [What your agent gets](#what-your-agent-gets) · [Use it as a library](#use-it-as-a-library) · [Backends](#backends)

<code>uvx toolplane init</code>

<br><br>

<a href="https://github.com/oneryalcin/toolplane/releases/tag/talk-video-2026-10"><img src="https://raw.githubusercontent.com/oneryalcin/toolplane/main/docs/assets/readme/demo.gif" alt="100 lookups as tool calls: every call is written by the model and every answer returns to it. The same 100 lookups as one program: the calls run in a sealed room and one answer returns. The model writes 8,178 tokens against 1,127, and the cost per answer is $0.0116 against $0.0058." width="900"></a>

<sub>Measured at 100 lookups: Claude Haiku 5.5 in Claude Code, 8 runs per arm. A single lookup or a step-by-step chain is cheaper as plain tool calls; the <a href="https://oneryalcin.github.io/toolplane/code-mode-benchmark/">benchmark</a> shows where each wins.</sub>

</div>

<br>

Define your agent's tool surface **once** and get the same curated, sandboxed
Python namespace under every agent client you use: Claude Code, Codex,
opencode, or your own.

## Why

Agents are strongest when they use code as the control plane: looping,
filtering, retrying, and combining tools in one pass instead of bouncing
through one tool call at a time. Toolplane is the runtime for that:

```text
discover capabilities -> inspect schemas -> execute Python against a curated namespace
```

Instead of registering N MCP servers in every agent client (and paying for
N × dozens of tool schemas in every context window, in every client, with
every client's different quirks), you register **one** server that exposes
three meta-tools. Your agent searches for what it needs, reads the exact
schemas, and writes a snippet:

```python
# one execute_code call replaces a dozen round-trips
rows = []
for repo in ["toolplane", "cli-to-py"]:
    prs = await github_list_pull_requests(repo=repo, state="merged", limit=25)
    for pr in prs:
        rows.append({"repo": repo, "title": pr["title"], "days_open": pr["days_open"]})
handle = await save_result(rows, label="pr-audit")
return {"count": len(rows), "handle": handle}
```

What makes toolplane different from other code-mode runtimes:

- **CLI binaries are first-class, policy-gated capabilities.** `git`, `gh`,
  `jq` become async Python functions behind an allowlist you control — not a
  raw shell. No other code-mode runtime ships this lane.
- **Policy escalates to a human instead of dead-ending.** If a snippet needs
  a binary outside the allowlist, your agent client shows *you* a form:
  allow it for this session, or refuse. Declines stick; nothing is ever
  written back to config. (MCP elicitation on handshake-era clients, Multi
  Round-Trip Requests on 2026-07-28 ones such as current Claude Code; degrades
  to a plain refusal on clients that can't prompt.)
- **A measured envelope, not a slogan.** We benchmarked code mode against
  plain MCP tool calls in a real client, pre-registered the confirmation
  study, and published where it *loses*. On Claude Haiku 5.5 in Claude Code
  (8 runs per cell): at 100 lookups one program costs half as much per
  correct answer ($0.0058 vs $0.0116), and the two cross between 5 and 20
  lookups. Plain calls stay cheaper for a single lookup (+11%), a
  chain the agent walks one step at a time (+12%), and a tool that already
  returns everything in one call (+5% to +31%). Round trips are not the
  mechanism, because clients batch tool calls in parallel: the tokens the
  model writes and the context it carries are. Harness and raw results in
  [`bench/`](bench/); the earlier releases, variance and limitations in
  [The Code-Mode Envelope, Measured](https://oneryalcin.github.io/toolplane/code-mode-benchmark/);
  the paper: [When Should an Agent Write a Program?](paper/main.pdf)
- **Safe by default, with zero infrastructure.** The default backend
  ([Monty](https://github.com/pydantic/monty)) is a sandboxed interpreter
  with no filesystem or network access — a pure `pip install`, no Docker, no
  Deno, no daemon.
- **Credentials handled like they matter.** OAuth servers: log in once in a
  browser, tokens persist Fernet-encrypted with the key in your OS keyring —
  every later session connects silently. API keys: reference them as
  `keyring://<name>` so `toolplane.toml` stays committable with zero secret
  material. Toolplane contains no token-handling code of its own — the
  flows, refresh, and encryption are FastMCP's; toolplane only configures
  where they happen.
- **Engineered for how agents actually behave.** Every dead end emits a
  signpost (no-match searches say how to browse; policy errors name what is
  allowed), errors are catchable by builtin type on every backend, and the
  live namespace manifest (`toolplane://namespace`) never lies about the
  server's configuration. This surface is certified by cold-start agent
  sessions, not just unit tests — the design lessons are published in
  [Agents Don't Find Resources](https://oneryalcin.github.io/toolplane/agents-dont-find-resources/)
  and the firsthand
  [MCP Client Capability Matrix](https://oneryalcin.github.io/toolplane/mcp-client-capability-matrix/).

Toolplane is developed and CI-tested on macOS and Linux (Python 3.11–3.13).
Windows is currently untested — reports welcome. Found a bug or a rough
edge? [Open an issue](https://github.com/oneryalcin/toolplane/issues) —
cold-start friction reports are especially valuable.

## Quickstart

Set up a config (safe defaults: sandboxed backend, CLI disabled):

```bash
uvx toolplane init
uvx toolplane cli allow git
uvx toolplane mcp add context7 --url https://mcp.context7.com/mcp
uvx toolplane doctor
```

Prove it runs — put this in `first.py`:

```python
v = await git("version")
return {"ok": v["ok"], "git": v["stdout"].strip()}
```

```bash
uvx toolplane run first.py
```

Then register it with your agent client (use the absolute path to your
`toolplane.toml`):

```bash
# Claude Code
claude mcp add toolplane -- uvx toolplane serve mcp --config /path/to/toolplane.toml

# Codex
codex mcp add toolplane -- uvx toolplane serve mcp --config /path/to/toolplane.toml
```

```jsonc
// opencode (~/.config/opencode/opencode.json)
{
  "mcp": {
    "toolplane": {
      "type": "local",
      "command": ["uvx", "toolplane", "serve", "mcp", "--config", "/path/to/toolplane.toml"]
    }
  }
}
```

**Tip: name the registration after what it serves.** The name you register
under becomes the tools' prefix (`mcp__toolplane__execute_code`), and
clients that defer tool loading show the model only those names before it
decides what to search for. If your config fronts one domain, a name like
`orders` tells the model where that data lives: in a benchmark, the same
facade registered as `orders` instead of `toolplane` let the agent skip a
discovery call (0/60 vs 7/60 runs, Haiku 5.5). The effect was small, so if
the config fronts several unrelated domains, `toolplane` is a fine default.

Every client now sees the same three tools — `search_capabilities`,
`get_capability_schemas`, `execute_code` — plus the live manifest resource
`toolplane://namespace` and a bundled usage skill. Ask your agent to read the
manifest and go.

### Already using MCP servers?

Bring your existing Claude Code or Codex servers over in one command:

```bash
uvx toolplane mcp import --from claude --dry-run   # see what would happen
uvx toolplane mcp import --from claude
uvx toolplane mcp import --from codex
```

Import never copies secrets into the TOML: secret-looking values are moved
into your OS keyring (written as `keyring://...` references), and
`mcp-remote`-style OAuth wrapper entries are rewritten to direct `url`
entries that use toolplane's encrypted token storage instead. The report
tells you exactly what it did and what to run next.

### Private servers and credentials

For an OAuth-protected MCP server (Linear, Notion, ...), log in once —
tokens persist encrypted (key in your OS keyring), and every later agent
session connects silently:

```bash
uvx toolplane mcp add linear --url https://mcp.linear.app/mcp --auth oauth
uvx toolplane mcp login linear   # one browser consent, ever
```

For API-key servers, store the key in the OS keyring and reference it —
your `toolplane.toml` stays committable with no secret material in it:

```bash
uvx toolplane secret set docs-api-token   # value via prompt or stdin
```

```toml
[mcp.servers.internal_docs]
url = "https://docs.example.com/mcp"

[mcp.servers.internal_docs.headers]
Authorization = "keyring://docs-api-token"   # or "env://DOCS_TOKEN" for CI
```

See [configuration](https://oneryalcin.github.io/toolplane/configuration/)
for the full credential story.

## What your agent gets

- **Three meta-tools instead of a tool catalog.** Search is exact-word (an
  empty query lists everything); schemas come back only for the capabilities
  the snippet will actually use.
- **A flat, async Python namespace.** MCP tools as flat functions —
  `await context7_get_docs(...)` — or canonically as
  `await call_tool("mcp:context7/get_docs", {...})`; allowlisted CLI
  binaries as `await git("log", oneline=True, max_count=3)` returning
  `{'stdout', 'stderr', 'exit_code', 'ok'}`. (Scoped sugar like
  `context7.get_docs(...)` exists on the non-default backends; the monty
  default is flat-only — the manifest always shows the shapes that work.)
- **State between runs, off the context window.** Variables persist across
  runs like notebook cells (a timed-out run rolls back cleanly;
  `await reset_session()` starts fresh). For values that must survive a
  reset or be read as MCP resources: `save_result`/`load_result` for
  JSON-shaped data, `save_artifact`/`load_artifact` for bytes (CSVs,
  images, parquet) — on Claude Code, a saved artifact materializes as a
  real local file.
- **Errors written for agents, not log files.** The real exception type and
  message on every backend, catchable by builtin type (`except ValueError`
  for store failures, `except PermissionError` for CLI policy,
  `except LookupError` for unknown capabilities) — identically on all three
  backends.

## Use it as a library

The same runtime embeds directly in Python — register your own functions and
execute agent-written snippets against them, sandboxed, with no server in
between:

```python
from toolplane import Toolplane

runtime = Toolplane()

@runtime.tool(tags={"math"})
def add(x: int, y: int) -> int:
    """Add two numbers."""
    return x + y

result = await runtime.execute("""
value = await call_tool("add", {"x": 2, "y": 3})
return value
""")
```

And if you already have an agent framework, `as_tool()` packages the whole
runtime as one `run_code` tool — a plain async function with a generated
description (compact enough for OpenAI's tool-description cap) that
pydantic-ai, the OpenAI Agents SDK, and LangChain/LangGraph all accept
directly. It is sandboxed monty by default: model-authored code never
inherits the `local_unsafe` dev backend implicitly — that requires an
explicit `backend="local_unsafe"` opt-in:

```python
from pydantic_ai import Agent

agent = Agent("anthropic:claude-sonnet-5", tools=[runtime.as_tool()])
```

To put your own functions in front of Claude Code or Codex, a short launcher
registers them and serves the same MCP facade — see
[`examples/python_functions_mcp.py`](examples/python_functions_mcp.py).

MCP servers, explicit CLI wrappers, and scoped Python namespaces register the
same way — see the [library guide](https://oneryalcin.github.io/toolplane/)
and [examples](examples/README.md) for the full surface, including
config-driven setup (`Toolplane.from_config`), the result/artifact stores,
and runnable `as_tool` examples for all three frameworks.

## Backends

| Backend | Use | Status |
| --- | --- | --- |
| `monty` | Default. Sandboxed by construction (no filesystem/network), pure pip install. Flat Toolplane callable namespace; no third-party imports. | Active |
| `pyodide-deno` | Opt-in for package-capable snippets (pandas/NumPy-style) via Pyodide in Deno. | Supported, feature-frozen |
| `local_unsafe` | In-process execution for development only. | Dev only |

A container backend (real CPython, arbitrary packages) lands when a concrete
use case needs it. See [Code Mode Backends](docs/code-mode-backends.md) for
the design record.

## Design goals

- Normalize heterogeneous tools into a Python-first API surface; JSON is the
  wire format, not the programming model.
- Keep the exposed runtime curated rather than ambiently powerful; the host
  decides which capabilities exist and what boundaries apply.
- Durable policy lives in config; session policy (escalation grants) dies
  with the process.
- Canonical capability ids are qualified (`mcp:server/tool`); friendly
  aliases exist only when unambiguous.
- Every slice earns its place through behavior, tests, or live certification.

## Development

```bash
make test
make examples
make ci
make publish-check
```

Docs: `make docs` / `make docs-serve`. Publishing:
`PYPI_TOKEN=... make publish` — see the
[release checklist](docs/development/release-checklist.md).

See [ROADMAP.md](ROADMAP.md) for sequencing and
[Architecture](docs/architecture.md) for code organization.
