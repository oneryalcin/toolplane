# toolplane

[![PyPI version](https://img.shields.io/pypi/v/toolplane)](https://pypi.org/project/toolplane/)
[![Python versions](https://img.shields.io/pypi/pyversions/toolplane)](https://pypi.org/project/toolplane/)
[![CI](https://github.com/oneryalcin/toolplane/actions/workflows/ci.yml/badge.svg)](https://github.com/oneryalcin/toolplane/actions/workflows/ci.yml)
[![Docs](https://github.com/oneryalcin/toolplane/actions/workflows/pages.yml/badge.svg)](https://github.com/oneryalcin/toolplane/actions/workflows/pages.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Define your agent's tool surface **once** — MCP servers, CLI binaries, Python
functions — and get the same curated, sandboxed Python namespace under every
agent client you use: Claude Code, Codex, opencode, or your own.

Full documentation: https://oneryalcin.github.io/toolplane/

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
  written back to config. (MCP elicitation; degrades to a plain refusal on
  clients that can't prompt.)
- **Safe by default, with zero infrastructure.** The default backend
  ([Monty](https://github.com/pydantic/monty)) is a sandboxed interpreter
  with no filesystem or network access — a pure `pip install`, no Docker, no
  Deno, no daemon.
- **Engineered for how agents actually behave.** Every dead end emits a
  signpost (no-match searches say how to browse; policy errors name what is
  allowed), errors are catchable by builtin type on every backend, and the
  live namespace manifest (`toolplane://namespace`) never lies about the
  server's configuration. This surface is certified by cold-start agent
  sessions, not just unit tests — the design lessons are published in
  [Agents Don't Find Resources](https://oneryalcin.github.io/toolplane/agents-dont-find-resources/)
  and the firsthand
  [MCP Client Capability Matrix](https://oneryalcin.github.io/toolplane/mcp-client-capability-matrix/).

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

Every client now sees the same three tools — `search_capabilities`,
`get_capability_schemas`, `execute_code` — plus the live manifest resource
`toolplane://namespace` and a bundled usage skill. Ask your agent to read the
manifest and go.

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

MCP servers, explicit CLI wrappers, and scoped Python namespaces register the
same way — see the [library guide](https://oneryalcin.github.io/toolplane/)
and [examples](examples/README.md) for the full surface, including
config-driven setup (`Toolplane.from_config`), the result/artifact stores,
and runnable `as_tool` examples for all three frameworks.

## Backends

| Backend | Use | Status |
| --- | --- | --- |
| `monty` | Default. Sandboxed by construction (no filesystem/network), pure pip install. Flat callable namespace — no classes, no third-party imports. | Active |
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
