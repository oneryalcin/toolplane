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

- `loop` — aggregate all 30 orders into per-region totals. Round-trip-heavy;
  the shape code-mode exists for.
- `filter` — count EMEA orders over 500. Moderate.
- `single` — one record lookup. The shape where code-mode's discovery
  overhead should LOSE; published anyway.

Correctness is validated programmatically against the shared dataset —
a cheap wrong answer counts as a loss, not a win.

## Run it

```bash
uv run python bench/run.py --reps 3            # full matrix, 24 paid runs
uv run python bench/run.py --reps 1 --tasks single   # cheap smoke
```

Requires the `claude` CLI on PATH and an authenticated session. Results
land in `bench/results/run-<stamp>.json`; the summary table prints at the
end (medians across reps).

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
