# Code-mode benchmark (#72)

Measures the code-mode thesis on real agent runs: does one Python snippet
looping over tools beat N individual tool-call round-trips — and where does
it lose?

## Arms

- **direct**: the `orders` MCP server registered directly in Claude Code.
  Classic usage: every record fetch is a tool-call round-trip.
- **toolplane**: the same server behind the toolplane facade
  (`search_capabilities` / `get_capability_schemas` / `execute_code`).
  The agent discovers the namespace and writes snippets.
- **hybrid** (`--arms ...,hybrid`, #114): the toolplane facade served with
  `--hybrid`, which additionally re-exports every capability as an ordinary
  MCP tool. The agent can call a native tool for single/adaptive tasks or
  `execute_code` for loops — the harness measures which it chooses.

Fairness constraints: same model, byte-identical prompts, same deterministic
dataset (`orders_data.py`, formula-based — the server and the validator
cannot drift), fresh empty cwd per run, `--strict-mcp-config` so no other
MCP servers leak in, permissions bypassed identically for both arms.

Hardening (#116): the code under test is **frozen at matrix start** — the
toolplane facade is built into a wheel and installed into a scratch venv,
and the fixture servers are snapshotted beside it, so editing the working
tree mid-run cannot reach a running measurement (the #111 contamination
incident). Every result row records the git SHA + dirty flag + wheel, harness,
and fixture sha256s. Arm order is **counterbalanced deterministically** (odd
reps reverse it — recomputable from the rep number; use even rep counts
for headline cells, odd counts leave the first-position split uneven by
one). Each row also records `model_requests`: the count of unique API
`request_id`s in the transcript — the exact number of model requests,
not an inference from cache arithmetic.

Known asymmetries and gaps, disclosed: Claude Code's built-in tools (Bash,
ToolSearch, ...) remain available in BOTH arms and are occasionally used —
tool names are recorded, and since #104 the full transcripts make them
auditable. The "tool calls" metric counts every client tool_use block,
built-ins included — it is NOT API round-trips (clients batch tool calls
in parallel; `model_requests` is the round-trip truth). Runs before
2026-07-10 predate the frozen-code and counterbalancing hardening: they
served from the working tree and always ran direct first.

## Tasks (the envelope, not a slogan)

- `loop` / `loop100` — aggregate all orders into per-region totals.
  Round-trip-heavy; the shape code-mode exists for.
- `loop5` / `loop10` / `loop20` — the same task at small N; localizes the
  crossover (#107 item 1).
- `filter` — count EMEA orders over 500. Moderate.
- `single` — one record lookup. The shape where code-mode's discovery
  overhead should LOSE; published anyway.
- `chain` — follow a 4-hop follow-up thread where each order's prose note
  names the next order AND a decoy, inviting judgment per hop (#107
  item 2, the shape prior work says code mode loses — it does; see the
  docs piece). The templated notes are heuristically separable — a
  keyword regex can walk the chain in one snippet — so this measures
  what agents choose to do, not impossibility; disclosed in the docs.
- `loop_lat100` — `loop` with 100ms per-call server latency
  (`BENCH_TOOL_LATENCY_MS`, async so the fixture never serializes);
  the #109 gate cell. Pre-port monty awaited sequentially (N x latency);
  the 0.0.19 pool API dispatches external calls eagerly, so a
  fire-then-await snippet should overlap (verified through the real
  bridge: 30 calls at 100ms in 0.24s vs 3.3s serial) — the cell proves
  whether agents actually write the pattern and the wall advantage
  survives a model.

The summary table annotates honesty mechanically: **†** where the two
arms' observed per-rep ranges overlap (the median gap is unresolved at
that rep count — an observation about the samples, not a noise verdict),
and **cost/pass** = total spend / successful runs (TPS-Bench
cost-of-pass; unknown-cost timeouts render n/a).

Correctness is validated programmatically against the shared dataset —
a cheap wrong answer counts as a loss, not a win.

## The M axis (server count)

`--servers 1,5,15` adds distractor MCP servers to BOTH arms — registered
directly in arm A, behind the facade in arm B. `distractor_server.py`
ships 7 realistic-but-irrelevant profiles (crm, calendar, tickets, wiki,
payments, analytics, files; ~0.6–1k tokens of tool definitions each by a
chars/4 estimate over the tool-list JSON);
M=15 wraps the profiles into a second `-eu` workspace. Every distractor
tool returns an inert empty result, so a run that strays is visible in
the recorded tool names rather than corrupted. M counts total configured
servers including `orders`. Finding (2026-07-09): Claude Code's deferred
tool loading neutralizes most of the M axis — see the docs piece.

## Payload and API-granularity axes (#117)

`--record-bytes 0,2000,20000` sets deterministic padding bytes on every
order. `--granularity fetch-one,bulk` changes the orders server's public API:

- `fetch-one` exposes only `list_order_ids` + `get_order`;
- `bulk` exposes only `get_orders`, returning the whole dataset.

The profiles are mutually exclusive so the model cannot choose the preferred
endpoint inside a cell. This measures the interaction between response size
and API shape; it does not treat a deliberately missing bulk endpoint as an
inherent limitation of direct MCP.

## Longitudinal sessions (#119)

`longitudinal.py` sends six related user turns through one streaming Claude
Code process, so the conversation and the stdio MCP process stay alive
together. It measures per-turn cumulative-cost deltas, peak request context,
real fixture calls, session reuse, compaction events, and a separately scored
`reset_session` + refetch turn. Prompts are sent only after the prior `result`
event; pre-buffering stream-json messages can steer an in-flight turn instead
of creating a clean next turn.

The same script separately measures Monty's required pre-run snapshot at
1 KB, 100 KB, and 10 MB of live namespace state. The snapshot worker runs
against the frozen wheel, and result provenance includes the longitudinal
harness hash as well as the ordinary wheel, fixture, and base-harness hashes.

## Run it

```bash
uv run python bench/run.py --reps 3            # full matrix, 24 paid runs
uv run python bench/run.py --reps 1 --tasks single   # cheap smoke
uv run python bench/run.py --reps 3 --tasks loop,single --servers 1,5,15   # M-axis, 36 paid runs
uv run python bench/run.py --reps 3 --tasks loop --arms direct,toolplane --servers 1 --record-bytes 0,2000,20000 --granularity fetch-one,bulk  # #117, 36 runs
uv run python bench/longitudinal.py --reps 4 --model sonnet   # #119, 8 six-turn sessions
uv run python bench/longitudinal.py --snapshot-only          # free local snapshot microbench
```

Requires the `claude` CLI on PATH and an authenticated session. Results
land in `bench/results/run-<stamp>.json`; the summary table prints at the
end (medians across reps).

## Codex CLI lane (#113)

`--client codex` runs the same tasks, fixtures, validators, and frozen code
under test through `codex exec --json` instead of Claude Code. Each isolation
setting was found necessary by a probe, not added on spec:

- `--ignore-user-config` keeps `~/.codex` MCP servers out of the run (the
  equivalent of `--strict-mcp-config`).
- `mcp_servers.<name>.default_tools_approval_mode="approve"`: headless Codex
  otherwise fails every MCP call ("requires approval, but approval policy is
  never").
- `features.shell_tool=false` and `features.unified_exec=false`: with a shell,
  Codex read the fixture **source** and derived answers from the data
  generator, a cheat that is not a measurement.
- `features.apps=false` and `features.plugins=false`: account connectors
  otherwise join the tool surface and cost turns.
- `mcp_servers.<name>.required=true`: without it, a short prompt once answered
  "unknown" from an empty tool list before the frozen-venv server had started.
- Server names are passed as **bare** TOML keys. In a `-c` override, quotes
  become part of the name (`'"orders"'`, which is invalid). With `required=true`
  Codex aborts; without it the server silently never starts and the agent
  answers from zero tools.

- An isolated `HOME` and `CODEX_HOME` per matrix, holding only a copy of the
  login (deleted with the run's temp dir), plus multi-agent, memories, hooks,
  host skill discovery, skill search, and web search flags off. Without this,
  the operator's `~/.codex/AGENTS.md` and `~/.agents/skills` rode in every
  request: a minimal prompt measured 20.7k input tokens instead of 9.3k.
  Not everything goes away: Codex's collaboration tools and its four
  built-in skills (`imagegen`, `openai-docs`, `skill-creator`,
  `skill-installer`) remain in both arms. `skip_host_skill_discovery` is an
  under-development flag, and every transcript carries its warning item.

**What `direct` means depends on the model.** Codex 0.160.0 gates its
built-in JavaScript code mode on per-model metadata. `gpt-6.1-sol` and
`gpt-6-luna` reach MCP tools only through a code-mode tool, `exec`: the model
writes JS that calls `tools.mcp__<server>__<tool>(...)`, discovers tools over
`ALL_TOOLS`, fans out with `Promise.allSettled`, and persists with
`store()`/`load()`. On those models `direct` is Codex-native code mode, and
`toolplane` is that same JS calling `execute_code`. `gpt-5.5` does not
advertise code mode and makes ordinary per-tool calls, so it is the
plain-tool-calling baseline. Each row records `codex_mode`
(`code_mode`/`direct_calls`) and `model_call_names`, read from Codex's
session rollout. That rollout is the only place the model's calls appear,
because `--json` hides the `exec` layer. The rollout is written into the
private home and never saved; the rows keep only derived metadata, including
`model_requests` and `peak_context_tokens` from its per-request usage events.

Codex on a ChatGPT plan reports tokens, not dollars, so `cost_usd` is `None`,
never estimated, and the comparison is by tokens and wall time. `input_tokens`
is the sum over the run's model requests, not a context size;
`peak_context_tokens` is the largest single request. `tool_calls` counts the
MCP invocations the run made (from `--json`); `model_call_names` counts what
the model itself called.

```bash
uv run python bench/run.py --client codex --model gpt-6.1-sol --reps 4 \
    --tasks single,loop,loop100
```

## Transcripts and classification (#104)

Every run's full stream-json transcript is persisted under
`bench/results/transcripts/run-<stamp>/`. Redaction covers the
init-event client-environment fields (plugins, commands, memory paths)
only — tool inputs and outputs are verbatim by design (they are the
evidence), so transcripts can contain local paths and usernames from
commands the agent chose to run. `bench/classify.py <transcripts-dir>`
splits extra
`execute_code` calls into retries-after-error vs staged-after-success
(snippet failures are *successful* MCP calls carrying a non-null
`ExecutionResult.error`), extracts the snippet error taxonomy, and
measures the discovery anatomy — calls before the first execute and
per-surface result sizes. Its output (`classified.json`) is what turned
"median 2 executes" folklore into "return-shape probes" and gated the
#106 fix.

## Metrics

Per run: correctness, tool-call count (from `stream-json` tool_use events),
API turns, output tokens, uncached input tokens (input + cache-creation;
cache reads reported separately since they are ~10x cheaper), cost in USD
as billed, wall-clock. Since #116 also: `peak_context_tokens` (the largest
single request, input + cache write + cache read: what actually hits the
context window), `non_api_s` (wall minus model-API time: client startup,
MCP/tool dispatch, and sandbox execution; splitting those further needs
per-event timestamps that stream-json does not emit), and `builtins`
(`default` or `restricted`).

The summary adds a **cost-of-pass difference vs direct with a 95% bootstrap
CI** per cell (each arm's runs resampled independently, seeded). It
bootstraps cost-of-pass, not raw spend, so a cheap wrong answer can never
read as a win. At 3–4 reps the intervals are wide; use 5–10 reps for
headline cells.

`--restrict-builtins` removes the client's general-purpose built-ins (Bash,
Read, Write, Edit, Glob, Grep, NotebookEdit, WebFetch, WebSearch, Task) in
**both** arms, so shortcuts around the MCP surface are impossible rather
than merely auditable. `ToolSearch` stays, because deferred tool loading
depends on it.

## Honesty notes

- The published numbers come from this harness; runs are not
  cherry-picked. Medians across reps with ranges, all raw JSON committed.
  One disclosed post-hoc correction: the original answer validator
  string-compared and rejected numerically-correct answers (`4520.50` vs
  `4520.5`); correctness fields were rescored from the recorded raw
  answers after fixing it — no runs re-executed, no answers modified.
- Client-side effects (Claude Code's own system prompt, caching behavior)
  are part of the measurement by design: the claim under test is about
  real production clients, not idealized API loops.
- One client (Claude Code), one model per run table. The envelope may
  differ elsewhere; the harness is cheap to rerun.
