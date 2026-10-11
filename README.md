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

[The idea](#the-idea) · [When it pays](#when-it-pays-and-when-it-doesnt) · [Quickstart](#quickstart) · [What you get](#what-you-get) · [Library](#use-it-as-a-library) · [Learn more](#learn-more)

<code>uvx toolplane init</code>

<br><br>

<a href="https://github.com/oneryalcin/toolplane/releases/tag/talk-video-2026-10"><img src="https://raw.githubusercontent.com/oneryalcin/toolplane/main/docs/assets/readme/demo.gif" alt="100 lookups as tool calls: every call is written by the model and every answer returns to it. The same 100 lookups as one program: the calls run in a sealed room and one answer returns. The model writes 8,178 tokens against 1,127, and the cost per answer is $0.0116 against $0.0058." width="900"></a>

<sub>Measured at 100 lookups: Claude Haiku 5.5 in Claude Code, 8 runs per arm. A single lookup or a step-by-step chain is cheaper as plain tool calls; the <a href="https://oneryalcin.github.io/toolplane/code-mode-benchmark/">benchmark</a> shows where each wins.</sub>

</div>

<br>

## The idea

Ask an agent to total 100 orders and it makes 100 tool calls. Clients send
them in batches, so the trips are quick. The job still costs twice what it
needs to. So what did you pay for?

**You paid for glue.** The model wrote out every call, token by token, and
every answer came back into its context so it could be handed to the next
step. None of that needed a model.

So move the glue into a program. The agent writes one short script, the
script makes the calls in a sandbox (the sealed room in the picture above),
and one answer comes back. Once the glue
is code, the kind of tool stops mattering: an MCP tool, a command-line program
and your own Python function are all just functions that one program can loop
over, branch on and join.

```python
# Which enterprise customers does this release fix something for?
import re
log = await git("log", format="%s", _=["v1.4..HEAD"])
ids = re.findall(r"TCK-\d+", log["stdout"])
pending = [tickets_get_ticket(id=i) for i in ids]
hits = []
for p in pending:
    ticket = await p
    if ticket["reported_by_customer"]:
        customer = await customers_get_customer(id=ticket["customer_id"])
        if await policy_sla_breach(ticket=ticket, customer=customer):
            hits.append({"customer": customer["name"], "ticket": ticket["id"]})
return {"commits": len(ids), "fixed_for": hits}
```

That program makes 181 calls: `git` once, two MCP servers 140 times, and a
Python function of our own 40 times. It returns one 419-byte answer. The 25 KB
of tool results behind it stay in the program, out of the model's context. We wrote
this one ourselves and recorded the run in [`pitch/example`](pitch/example/).

**Toolplane is the runtime for this.** You name your MCP servers and the
commands you allow in one config file. Every agent client you use (Claude
Code, Codex, opencode, or your own) then gets the same three tools: search
for a capability, read its schema, run a program.

## When it pays, and when it doesn't

A program is not always the cheaper way, so we measured where the line is.

| Task | Cheaper way | One program vs plain tool calls |
| --- | --- | --- |
| 100 small lookups | one program | $0.0058 vs $0.0116 (half) |
| 20 lookups | about level | $0.0053 vs $0.0057 |
| 5 lookups | plain tool calls | $0.0053 vs $0.0047 |
| A single lookup | plain tool calls | 11% more |
| A chain the agent walks one step at a time | plain tool calls | 12% more |
| One call that already returns everything | plain tool calls | 5% to 31% more |

Cost per correct answer on Claude Haiku 5.5 in Claude Code, 8 runs per arm,
in a pre-registered study. As a rule of thumb: write a program for many small
calls or large intermediate results, and call directly for one lookup, a
chain where each step is its own decision, or a tool that returns everything
at once.

The saving does not come from fewer round trips, since clients already batch
calls. It comes from the tokens the model no longer writes and the context it
no longer carries. The full method, variance and limitations are in
[the benchmark](https://oneryalcin.github.io/toolplane/code-mode-benchmark/), [the paper](paper/main.pdf) and
[a 9-minute talk](https://github.com/oneryalcin/toolplane/releases/tag/talk-video-2026-10).

## Quickstart

Set up a config. The defaults are safe: a sandboxed backend, and no commands
allowed until you allow them.

```bash
uvx toolplane init
uvx toolplane cli allow git
uvx toolplane mcp add context7 --url https://mcp.context7.com/mcp
uvx toolplane doctor
```

Prove it runs. Put this in `first.py`:

```python
v = await git("version")
return {"ok": v["ok"], "git": v["stdout"].strip()}
```

```bash
uvx toolplane run first.py
```

Then register it with your agent client, using the absolute path to your
`toolplane.toml`:

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

Your agent now sees three tools: `search_capabilities`,
`get_capability_schemas` and `execute_code`. Ask it to do something that
takes many calls.

Three things you may want next:

- **You already have MCP servers.** `uvx toolplane mcp import --from claude`
  (or `--from codex`) brings them over and moves any secrets into your OS
  keyring. Add `--dry-run` to see the plan first.
  [Importing](https://oneryalcin.github.io/toolplane/configuration/#importing-from-other-clients)
- **A server needs a login or an API key.** `uvx toolplane mcp login <name>`
  does the browser consent once; API keys are stored with
  `uvx toolplane secret set <name>` and referenced as `keyring://<name>`, so
  the config file holds no secrets. [Secrets](https://oneryalcin.github.io/toolplane/configuration/#secrets)
- **Your config serves one domain.** Register it under that domain's name
  instead of `toolplane`: the name becomes the tools' prefix, which is what
  the model sees first. [Why it helps](https://oneryalcin.github.io/toolplane/toolplane-mcp/)

## What you get

- **Three tools instead of a catalogue.** The agent searches, reads only the
  schemas it will use, and runs a program. Your context does not fill with
  hundreds of tool descriptions.
- **Commands as functions, behind an allowlist.**
  `await git("log", oneline=True, max_count=3)` returns
  `{'stdout', 'stderr', 'exit_code', 'ok'}`. There is no shell. If a program
  needs a command you have not allowed, clients that can prompt ask *you*:
  allow it for this session, or refuse. Nothing is written back to your
  config. [Escalation](https://oneryalcin.github.io/toolplane/configuration/#escalation)
- **A sandbox with nothing to install.** Programs run in
  [Monty](https://github.com/pydantic/monty), an interpreter with no
  filesystem or network access. It is a plain `pip install`: no Docker, no
  daemon. Commands and tools run outside it, which is why they pass through
  your allowlist. [Backends](docs/code-mode-backends.md)
- **Credentials that stay out of the config.** OAuth tokens are stored
  encrypted with the key in your OS keyring. API keys are references, so
  `toolplane.toml` is safe to commit.
- **State between runs.** Variables persist from one program to the next,
  like notebook cells. `save_result` and `save_artifact` keep data and files
  out of the context window.
  [Sessions and stores](https://oneryalcin.github.io/toolplane/configuration/#session)
- **Errors an agent can act on.** A program can catch real exception types
  (`PermissionError` for policy, `LookupError` for an unknown capability),
  and every dead end says what to try next.
  [Agents Don't Find Resources](https://oneryalcin.github.io/toolplane/agents-dont-find-resources/)

## Use it as a library

The same runtime embeds in Python. Register your own functions and run
agent-written programs against them, with no server in between:

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

If you already use an agent framework, `runtime.as_tool()` packages the whole
runtime as one `run_code` tool that pydantic-ai, the OpenAI Agents SDK and
LangChain/LangGraph accept directly. It runs in the sandbox by default.

```python
from pydantic_ai import Agent

agent = Agent("anthropic:claude-sonnet-5", tools=[runtime.as_tool()])
```

To put your own functions in front of Claude Code or Codex, a short launcher
registers them and serves the same three tools:
[`examples/python_functions_mcp.py`](examples/python_functions_mcp.py). More
runnable examples are in [`examples/`](examples/README.md).

## Learn more

- [Documentation](https://oneryalcin.github.io/toolplane/): the full guide.
- [Configuration](https://oneryalcin.github.io/toolplane/configuration/): the config file, command policy,
  sessions, stores, audit log, servers and secrets.
- [Toolplane and MCP](https://oneryalcin.github.io/toolplane/toolplane-mcp/): how it sits between your client
  and your servers.
- [Backends](docs/code-mode-backends.md): the sandbox, and the opt-in backend
  for programs that need packages such as pandas.
- [Architecture](docs/architecture.md) and [roadmap](ROADMAP.md).
- [The benchmark](https://oneryalcin.github.io/toolplane/code-mode-benchmark/), [the paper](paper/main.pdf)
  and [what other code-mode systems do](https://oneryalcin.github.io/toolplane/code-mode-landscape/).
- [MCP client capability matrix](https://oneryalcin.github.io/toolplane/mcp-client-capability-matrix/): what
  Claude Code and Codex support, measured firsthand.

## Status and contributing

Toolplane is developed and tested on macOS and Linux with Python 3.11 to
3.13. Windows is untested; reports are welcome. If you hit a bug or a rough
edge, [open an issue](https://github.com/oneryalcin/toolplane/issues).
Reports from a first-time setup are especially useful.

```bash
make test       # unit tests
make examples   # the deterministic examples, run for real
make ci         # what CI runs
```

Releases follow the [release checklist](docs/development/release-checklist.md).
MIT licensed.
