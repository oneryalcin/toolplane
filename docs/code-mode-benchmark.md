# The Code-Mode Envelope, Measured

*2026-07-08 · toolplane 0.4.0 · Claude Code 2.1.204 · claude-sonnet-5 ·
harness and raw results in [`bench/`](https://github.com/oneryalcin/toolplane/tree/main/bench)*

The code-mode thesis — one Python snippet looping over tools beats N
individual tool calls — is repeated in vendor blog posts and was the
founding premise of this project. We had never measured it ourselves, and
the industry numbers predate aggressive prompt caching and parallel tool
use in production clients. So we measured it, and we are publishing where
code mode **loses** along with where it wins, because a benchmark you would
only publish if it wins is marketing, not evidence.

The short version: **code mode wins at scale, but for a different reason
than the folklore says.** The round-trip story is dead — production clients
batch tool calls in parallel. What actually scales O(N) in classic MCP
usage is *generated output* (every tool invocation is output tokens) and
*context growth* (every result becomes conversation history). Code mode
moves the loop into one snippet, so both stay flat.

## Setup

Two arms, same model, byte-identical prompts, same deterministic MCP server
(an order store: `list_order_ids`, `get_order(id)` — fetch-one-record
shape, so aggregate questions force per-record tool use):

- **direct** — the server registered straight into Claude Code.
- **toolplane** — the same server behind the toolplane facade. The agent
  discovers capabilities and writes Python snippets against them.

Each cell is the median of 3 headless `claude -p` runs from a fresh empty
directory with `--strict-mcp-config` (no other MCP servers, no CLAUDE.md);
ranges across the 3 reps are shown in parentheses. Correctness is validated
programmatically against the shared dataset.

Definitions, precisely:

- **tool invocations** counts every client tool_use block — MCP tools plus
  Claude Code built-ins (ToolSearch, Bash) that the client used on its own
  initiative in both arms. It is NOT the number of API round-trips; see
  "What actually happened" below.
- **uncached in** is input + cache-creation tokens. Cache *reads* are
  priced ~10x lower and are **not symmetric**: the toolplane arm reads
  2.6–4.5x more cached input than direct (its facade schemas and skill ride
  in every request). That asymmetry is fully priced into the `cost`
  column, which is the client's own billed total.

## Results

**Single record lookup** (one `get_order`):

| arm | ok | tool invocations | output tokens | cost | wall |
|---|---|---|---|---|---|
| direct | 3/3 | 2 | 171 | **$0.14** (0.13–0.14) | **6.9s** (6.4–7.6) |
| toolplane | 3/3 | 9 (7–9) | 888 (648–898) | $0.26 (0.23–0.26) | 21.7s (19.2–22.1) |

**Aggregate over 30 records** (per-region totals):

| arm | ok | tool invocations | output tokens | cost | wall |
|---|---|---|---|---|---|
| direct | 3/3 | 32 | 2394 | **$0.20** (0.19–0.21) | 23.2s (21.4–26.2) |
| toolplane | 3/3 | 10 (7–10) | 1297 (995–1670) | $0.24 (0.21–0.24) | 26.8s (22.2–28.1) |

**Filtered count over 30 records**:

| arm | ok | tool invocations | output tokens | cost | wall |
|---|---|---|---|---|---|
| direct | 3/3 | 32 | 1984 | **$0.19** (0.188–0.189) | 20.6s (19.6–20.8) |
| toolplane | 3/3 | 7 (6–8) | 1016 (861–1110) | $0.22 (0.19–0.23) | 22.5s (18.6–22.9) |

**Aggregate over 100 records**:

| arm | ok | tool invocations | output tokens | cost | wall |
|---|---|---|---|---|---|
| direct | 3/3 | 103 | 7569 (6890–7580) | $0.33 (0.32–0.33) | 53.6s (51.2–55.4) |
| toolplane | 3/3 | **11** (10–14) | **1760** (1700–3330) | **$0.28** (0.26–0.33) | **36.6s** (36.1–52.6) |

## The M axis: does server count change the picture? (2026-07-09 follow-up)

The tables above froze M=1 configured server with 2 tools — toolplane's
worst case, since the facade's fixed overhead buys nothing when there is
almost nothing to aggregate. The folklore predicts that at realistic server
counts (5–15 servers, dozens of tools), direct registration drowns in tool
definitions and code mode wins everywhere. We tested it: same tasks, same
arms (Claude Code 2.1.205 this time; the fresh M=1 baseline matches the
2.1.204 tables above on cost and token counts, but wall times ran 30–40%
slower across the board with non-overlapping ranges — treat cross-version
latency comparisons as unsupported; every M comparison below is same-day,
same-version), adding 4 or 14 distractor servers (realistic-but-irrelevant
CRM / calendar / tickets / wiki / payments / analytics / files surfaces,
~0.6–1k tokens of definitions each by a chars/4 estimate over the
tool-list JSON; 82 distractor tools at M=15, since the 7 profiles register
twice as separate workspaces) to **both** arms — registered directly in arm A, behind the facade in arm B.
36 fresh runs, 36/36 correct, medians of 3:

**Aggregate over 30 records** (the task where M should matter most):

| M | direct cost | toolplane cost | direct wall | toolplane wall |
|---|---|---|---|---|
| 1 | **$0.20** (0.194–0.208) | $0.22 (0.214–0.255) | 29.9s | 31.3s |
| 5 | $0.20 (0.198–0.200) | **$0.18** (0.174–0.237) | 27.4s | 27.3s |
| 15 | $0.23 (0.205–0.232) | $0.23 (0.227–0.250) | 31.0s | 34.7s |

**Single lookup**: direct stays flat and dominant at every M
($0.13–0.14 vs $0.25–0.27); the discovery tax never amortizes over one
call, no matter how many servers are configured.

The folklore got a second mechanism wrong. **Claude Code's deferred tool
loading neutralized the M axis before code mode could exploit it**: every
direct run at every M made exactly one ToolSearch call and loaded only the
orders tools — the 82 distractor definitions never entered context. We
verified the mechanism with a raw-transcript probe rather than inferring
it from the call counts: at M=15 the session init event lists zero
`mcp__` tools (all deferred), and the agent's first action is
`ToolSearch("order status")` followed directly by the loaded
`get_order`. The probe transcript is committed
(`bench/results/probe-m15-direct-single.jsonl`; client-environment
fields redacted from the init event, tool inventory untouched).

The residual is smaller than the summary table suggests. Two of three
M=15 direct runs took an extra Bash turn (~2.5k uncached tokens each) and
the median pairs those against Bash-free M=1 runs — a behavioral
covariate, not an M mechanism. Like-for-like (Bash-free runs only),
direct grows **~185 uncached tokens per added server, +3% cost from M=1
to M=15** on the loop task; the table's median-vs-median reading (~360
tokens/server, +12%) is the upper bound with the covariate left in. Either
way it is a fraction of the ~11k tokens the raw definitions would have
added at M=15 had they actually ridden in context. This is the same story as the
round-trip folklore above: a mechanism argument for code mode that modern
clients have already engineered away — here via exactly the
"platform-native deferred loading" cluster from
[the landscape survey](code-mode-landscape.md).

Three honest observations from the same data:

- **No reliable M-trend in the crossover at n=3.** Direct-minus-toolplane
  cost at N=30 is non-monotone across M=1/5/15 (−$0.02, +$0.02, −$0.00),
  and toolplane's cost tracks its own discovery-turn count far more
  tightly than it tracks M (5 facade calls → ~$0.17, 10 → ~$0.26,
  regardless of M). The M=5 "win" in the table is two low-turn runs, and
  the M=15 "dead heat" is cost-only — direct still wins wall there (31.0s
  vs 34.7s). The honest statement: at N=30, adding servers does not
  rescue code mode, because deferral means there is little to rescue it
  from.
- **Toolplane's context also grows with M — attribution pending.** Its
  arm-level uncached-input slopes are ~160–290 tokens/server (loop,
  single), though the loop M=1→5 slope is negative, so noise is
  comparable to signal. The namespace manifest — which lists *every*
  capability and is read once per run — is the prime suspect, and at M=15
  this growth offsets roughly 40% of direct's own residual. But the
  committed data cannot isolate the manifest turn
  ([#104](https://github.com/oneryalcin/toolplane/issues/104)); treat the
  filterable-manifest idea in
  [#106](https://github.com/oneryalcin/toolplane/issues/106) as a
  hypothesis with a suspect, not a measured verdict.
- **No selection-accuracy degradation either arm**: 36/36 correct, and no
  run in either arm ever invoked a distractor tool. At 84 tools, with
  deferred loading, the "too many tools confuses the model" failure mode
  did not appear — with the caveat that our distractors are semantically
  distant from the task (CRM/calendar/wiki vs orders); near-miss
  distractors (an `invoices` or `orders-legacy` server) are the harder
  test and unmeasured. (Anthropic's published accuracy gains from Tool
  Search were measured at much larger surfaces, without deferral as
  baseline.)

Caveats: distractor definitions are lean (~0.8k tokens/server; a GitHub-
scale server ships ~50k) — but since definitions are deferred, definition
*weight* mostly stops mattering; what scales is the stub list. This
matrix ran only `loop` (N=30) and `single`: `loop100`, the task toolplane
wins at M=1, was left out for cost, so whether M widens the N=100 win is
unmeasured. Deferred
loading was default-on in this client version (2.1.205, headless) — a
client without it, or with it disabled, should reproduce the folklore
scenario, which we did not measure. Same n=3, same machine, same day
caveats as above. Raw data: `bench/results/run-20260709-114215.json`.

## What actually happened (read the usage data, not the folklore)

The naive reading of the N=100 table is "103 round-trips vs 11." The
committed usage data refutes it. Calibrating per-request cache reads from
the 3-turn single/direct runs (~45k tokens/request), direct's loop100 runs
read ~159k cached tokens — roughly **3–4 API requests**, not 104. Claude
Code batched the `get_order` calls in parallel inside a handful of
requests. The toolplane arm's calls (search → schemas → execute, plus
snippet retries) are inherently sequential: ~9 requests. **In API
round-trip terms, the toolplane arm made 2–3x MORE round-trips than direct
in every aggregate task — and still won at N=100.**

So where did direct's money and time go? Output tokens. Every tool
invocation is *generated output* (~70 tokens of tool_use block per
`get_order`), and every result lands in the conversation. At N=100 that is
7.5k output tokens and ~50 seconds of generation for a three-line answer,
plus a conversation that now carries 100 order records as context for
whatever comes next. Code mode generates one snippet instead: output and
context stay flat in N.

This matters beyond this benchmark: latency-focused "round-trip"
arguments for code mode are stale where clients parallelize tool calls.
The durable arguments are **output-token scaling and context growth** —
which is what these numbers actually show.

## The envelope

Two measured points; nothing measured between them:

- **At N=30 tool interactions per task, direct is ~20% cheaper** (and
  wall-clock is a wash — the ranges overlap). Toolplane already uses 3x
  fewer tool invocations and ~half the output tokens, but its fixed
  overhead (facade schemas in context, discovery calls, snippet writing
  and retries) still outweighs what it saves.
- **At N=100, toolplane wins on median across every metric**: ~15%
  cheaper, ~32% faster, 4.3x fewer output tokens. Honest caveat at n=3:
  the reps spread — one of three toolplane runs (a heavy snippet-retry
  run) cost more than every direct run. The direction is consistent; the
  margin is not tight.
- **Single lookups: direct wins decisively** — half the cost, a third of
  the wall-clock. The discovery tax never amortizes over one call.

**The crossover sits somewhere between 30 and 100 tool interactions per
task in today's Claude Code; we did not measure between those points.**
Below it, plain MCP is the right tool — which is why toolplane serves
both: any server behind the facade can also be registered directly.

We resist extrapolating further. Slower (network-bound) tools cut both
ways — direct pays per-call latency but parallelizes; monty snippets await
calls sequentially. Long multi-task sessions should compound direct's
context growth (five such tasks ≈ 500 records of history vs ~50), but
client auto-compaction exists precisely to trim that, and we have not
measured it. Those are hypotheses, labeled as such.

## Honesty file

- **Post-hoc rescore, disclosed**: the first validator string-compared
  `4520.5` to the agents' `4520.50` (which the prompt itself requested) and
  marked numerically-correct answers wrong. We fixed the validator to
  compare semantically and rescored from the recorded raw answers — no
  runs were re-executed, no answers changed. The commit history has both
  states.
- **Built-in tools were available and used in both arms** (that is what a
  real client session is). The raw data records tool *names* — Bash
  appears occasionally in both arms — but not inputs, so we cannot show
  what those calls did. Transcript persistence and retry classification
  are filed as [#104](https://github.com/oneryalcin/toolplane/issues/104).
- Every toolplane run used multiple `execute_code` calls (median 2, max 8
  in the worst N=100 run) plus one namespace-manifest read — a mix of
  deliberate staged execution and failed-snippet retries (the monty
  dialect tax) that transcripts will let us split. All of it is priced
  into every toolplane number above.
- **Fixed run order** (direct before toolplane, same 5-minute window)
  means reps are not independent: prompt-cache warmth from earlier runs is
  visible in the raw usage of later ones. Small at these sizes, but real.
- One client, one model, one machine, one day, n=3. The harness is
  checked in and cheap to rerun — treat this as one data point with error
  bars, not a law.
- The two-tool server shape (no bulk-fetch tool) is the shape that
  maximally penalizes direct; many real servers look like this, but a
  server offering `get_orders(bulk)` would move the envelope toward
  direct. The facade's richer self-description (instructions, skill,
  manifest) versus the orders server's one-line docstrings is
  products-as-shipped, and its token cost is priced into the toolplane
  column.

## Prior work, and what is actually new here

A full survey lives in [Code Mode in the Wild](code-mode-landscape.md);
the short version, so this page's claims are positioned rather than
implied:

- **The pattern is not ours.** CodeAct (Wang et al., ICML 2024,
  arXiv:2402.01030) established code-actions-beat-JSON-tool-calling (up to
  20% higher success, ~30% fewer turns), and Anthropic, Cloudflare, Block
  (goose), fastmcp, and others shipped production implementations through
  2025–2026. Anthropic's Programmatic Tool Calling reports 37% token
  reduction with flat accuracy; the AAIF/Port-of-Context case study is the
  only production A/B we know of (100% vs 56% delivery, half the cost, one
  workload).
- **The measurement gap is what this page fills.** Every published number
  we could find is a static context-size comparison (150K→2K, 1.17M→1K,
  72K→8.7K) with no task-level cost or latency. CodeAct-lineage baselines
  predate prompt caching and parallel tool batching — both standard in
  production clients now, and both accounted for here. The nearest
  academic neighbor (arXiv:2602.15945, Feb 2026) found qualitatively that
  code execution's advantage grows with task complexity and can reverse
  on complex orchestration; goose's team concedes single-tool tasks are
  slower under code mode. Nobody publishes a numeric crossover.
- **Three claims we believe are novel** (corrections welcome): the numeric
  cost/latency crossover in a real client; the mechanism decomposition
  under modern client economics (output-token scaling + context growth,
  with the code-mode arm making MORE API round-trips and winning anyway);
  and the discovery tax priced in model turns. The turn-cost of discovery
  in particular is engineered around by every vendor (deferred loading,
  same-response tool search) and measured by none.
- **What prior work does better**: accuracy evaluation at scale
  (Anthropic's MCP evals), production evidence (AAIF), tool retrieval at
  thousand-tool scale (AnyTool, MCP-Zero), security analysis
  (arXiv:2602.15945's attack catalog), and peer review — this page has
  none. Treat our envelope as a first measurement in an unmeasured field.

## Reproduce it

```bash
git clone https://github.com/oneryalcin/toolplane && cd toolplane
uv run python bench/run.py --reps 3          # full matrix: 24 paid runs
uv run python bench/run.py --reps 1 --tasks single   # cheap smoke
```

Raw per-run JSON for every table above is committed under
[`bench/results/`](https://github.com/oneryalcin/toolplane/tree/main/bench/results).
