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

Fairness constraints: same model, byte-identical prompts, same deterministic
dataset (`orders_data.py`, formula-based — the server and the validator
cannot drift), fresh empty cwd per run, `--strict-mcp-config` so no other
MCP servers leak in, permissions bypassed identically for both arms.

Known asymmetries and gaps, disclosed: Claude Code's built-in tools (Bash,
ToolSearch, ...) remain available in BOTH arms and are occasionally used —
tool names are recorded but inputs are not (transcript persistence is #104),
so their contents are unauditable in the committed runs. Arm order is fixed
(direct first), so later runs can hit warmer prompt caches within the 5-min
TTL. The "tool calls" metric counts every client tool_use block, built-ins
included — it is NOT API round-trips (clients batch tool calls in parallel;
see the docs piece for the usage-data arithmetic).

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
  measures monty's sequential awaits, gates #109.

The summary table annotates honesty mechanically: **†** where the two
arms' per-rep ranges overlap (median gap inside the noise), and
**cost/pass** = total spend / successful runs (TPS-Bench cost-of-pass).

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

## Run it

```bash
uv run python bench/run.py --reps 3            # full matrix, 24 paid runs
uv run python bench/run.py --reps 1 --tasks single   # cheap smoke
uv run python bench/run.py --reps 3 --tasks loop,single --servers 1,5,15   # M-axis, 36 paid runs
```

Requires the `claude` CLI on PATH and an authenticated session. Results
land in `bench/results/run-<stamp>.json`; the summary table prints at the
end (medians across reps).

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
as billed, wall-clock.

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
