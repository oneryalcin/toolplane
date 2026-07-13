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

## Cutting the discovery tax (2026-07-09 follow-up, toolplane 0.4.0+)

The envelope above identified toolplane's entire small-N disadvantage as
a fixed discovery tax: 3–5 sequential model turns (search → schemas →
namespace manifest, per the facade's own teaching) plus a median of one
extra execute_code before the answer. Transcript-level instrumentation
([#104](https://github.com/oneryalcin/toolplane/issues/104), now in the
harness: per-run stream-json under `bench/results/transcripts/`, classified
by `bench/classify.py`) showed why: the three things an agent needs before
its first snippet — canonical name, parameters, Python binding name —
each lived on a different discovery surface. The same-day instrumented
runs also exposed a second failure class: with call shapes but no
return-shape teaching, agents guessed that bindings return a wrapped
envelope (`ids["result"]`, `o["value"]`), hit a TypeError, and burned a
turn printing the type. (No transcripts exist for 0.4.0 itself —
persistence lands with this change — so attributing the same mechanism
to 0.4.0's median-2-executes is an inference from the matching teaching
gap, not an observation.)

The fix ([#106](https://github.com/oneryalcin/toolplane/issues/106)):
`search_capabilities` results now carry each hit's exact awaitable call
shape (`await orders_get_order(order_id=<string>)` — keywords only,
required first, binding names resolved exactly as the sandbox binds them)
plus a short footer with the snippet rules, the return-shape contract
(values arrive plain, never enveloped), and the namespace surfaces search
does not list. The facade instructions and skill teach search-first with
escalation instead of the old four-read ceremony.

Same harness, same tasks, 18 fresh runs (18/18 correct), medians of 3,
against the published 0.4.0 numbers above:

| task | toolplane 0.4.0 | toolplane after | direct (same day) |
|---|---|---|---|
| single | $0.26 · 10 turns · 21.7s | **$0.18 · 5 turns · 15.1s** | $0.14 · 3 turns · 9.1s |
| loop (N=30) | $0.24 · 11 turns · 26.8s | **$0.18 · 6 turns · 20.9s** | $0.20 · 33 turns · 30.4s |
| loop100 | $0.28 · 12 turns · 36.6s | **$0.18 · 6 turns · 20.2s** | $0.33 · 104 turns · 57.3s |

(Turns are the client's `num_turns` in every cell. The 0.4.0 wall times
come from client 2.1.204 — conservative here, since the same-version
pre-fix walls in `run-20260709-114215.json` are slightly worse.)

The direct arm is the control: its same-day medians reproduce the
published tables to the cent ($0.14/$0.20/$0.33), so the toolplane-arm
movement is the facade change, not client or day drift. What the
committed transcripts show after the change: **zero namespace-manifest
reads** (previously every run read the ~2.7k-char manifest), one
`search_capabilities` call per run, median **1** execute_code with **zero
failed snippets and zero retries** across all nine toolplane runs
(previously median 2 executes overall and 5 in the worst task, loop100).
The TypeError probes appear in the same-day runs served the intermediate
teaching — call shapes without the return-shape sentence — while the two
genuinely pre-fix runs in the mixed-facade transcripts spent their extra
turns on the manifest ceremony instead.

What this does to the envelope: **the crossover moved from "between 30
and 100 tool interactions" to below 30** — at N=30 toolplane now wins on
median cost ($0.18 vs $0.20; per-rep ranges still overlap at n=3) and
cleanly on wall (20.9s vs 30.4s, ranges disjoint), and at N=100 it is
~45% cheaper and ~2.8x faster. Single lookups still lose ($0.18 vs
$0.14, 15.1s vs 9.1s): the floor is one search turn plus one execute turn
against direct's ToolSearch-plus-call, and the facade schemas still ride
in context. Where between 1 and 30 the crossover now sits is unmeasured.
*(Since measured: parity in the 20–30 region — next section.)*

Honesty notes for this section: n=3 per cell, one day, client 2.1.205
(cost-controlled by the direct arm as above; wall comparisons to the
2.1.204 tables are not supported). The `filter` task from the published
tables was not re-run. A first same-day "before" matrix was
accidentally contaminated — the facade change landed mid-run, so later
reps were served the new code; it is committed as
`run-20260709-220556-mixed-facade` (transcripts partition cleanly by a
footer fingerprint) and excluded from every number above, with the
published 0.4.0 tables serving as the before-side instead. The
return-shape footer sentence was added *after* observing the TypeError
probes in those mixed transcripts and before the clean 18-run matrix —
one teaching iteration, disclosed; note the sentence was written against
the exact envelope keys agents guessed on this two-tool server, and its
generalization to other tool surfaces is untested. Post-review copy
edits to the footer and call-shape rendering (a real-allowlist CLI
example, scoping the rules to capability bindings, `call_tool` fallbacks
for shapes that cannot render faithfully) landed *after* the measured
runs — the committed transcripts record the exact text each run saw.

## The envelope, completed: a crossover curve, an adversarial shape, and latency (2026-07-10)

Three remaining #107 axes, measured with the same harness (30 fresh runs,
30/30 correct, medians of 3, same client version 2.1.205 as the section
above, same evening — roughly 90 minutes after the discovery-tax matrix,
whose N=30 and N=100 cells this section reuses). The summary table now
does two honesty checks mechanically: a **†** wherever the two arms'
observed per-rep ranges overlap — a statement about the samples, not a
noise verdict; it means the median gap is unresolved at this rep count —
and **cost-of-pass** (total spend / successful runs; a cheap wrong
answer prices as a loss, an unknown-cost timeout renders "n/a" rather
than pricing as free).

**The crossover curve.** The two-point "somewhere between 30 and 100"
from 0.4.0, then "below 30" after the discovery-tax fix, is now a curve
(N=5/10/20 from this run; N=30/100 from the discovery-tax matrix):

| records touched | 5 | 10 | 20 | 30 | 100 |
|---|---|---|---|---|---|
| direct | **$0.13** | **$0.14** | $0.18† | $0.20† | $0.33 |
| toolplane | $0.18 | $0.17 | $0.18† | $0.18† | **$0.18** |

Toolplane is flat ~$0.18 at every N — including N=5, where the loop
saves almost nothing and the price is pure facade overhead. Direct climbs
linearly. **Below ~10 direct is clearly cheaper (ranges disjoint). The
medians cross between 20 and 30 — at N=20 direct is still a hair ahead
($0.1787 vs $0.1813 unrounded, wall too), at N=30 toolplane is — with
per-rep ranges overlapping at both, so the region 20–30 is parity within
this sample. At N=100 toolplane wins with disjoint ranges.** The flat
line is the product thesis in one row: with the discovery tax gone,
code-mode cost is task-size-independent.

**The adversarial shape (where prior work says code mode loses — it
does).** The `chain` task follows a 4-hop follow-up thread where each
order's prose note names the next order *and a decoy* (cancelled
duplicate, unrelated reference), with template and mention-order varying
per hop — a task that *invites* judgment per hop. Result: **direct wins,
$0.17 vs $0.24 (+38% on unrounded medians) and 22.9s vs 27.9s, ranges
disjoint, both arms 3/3 correct.** The committed transcripts show the
mechanism exactly: every toolplane rep, deterministically, used 5
execute_code calls — one per node (the start plus 4 hops), zero errors,
all staged — the agent treated each hop as a judgment step and degraded
into tool-calling with a heavier per-step envelope. This confirms
arXiv:2602.15945's qualitative finding with a mechanism and a price:
**on sequentially-adaptive tasks, code mode is direct tool calling with
extra steps.** Design honesty: the notes are template-generated, and the
fixture is *heuristically separable* — a keyword regex over the
committed templates walks the whole chain in one snippet
(review-verified), and the hop-2 note even names the just-visited order
as its decoy. Neither arm's agent attempted anything of the sort, so
what this task measures is what agents *choose* to do with an
adaptive-looking thread — the realistic behavior — not impossibility. A
fixture whose judgment steps genuinely resist code (free-form prose,
contradictory phrasing) is future work.

**The latency axis (the #109 gate).** Same loop task, 100ms of
(server-parallel, verified) latency per tool call. Toolplane's wall
went 20.9s → 23.6s against the same-evening baseline — **median +2.7s,
consistent with the 31 sequential awaits in the committed snippets
(one list plus 30 gets → +3.1s expected)**: the microbenchmarked monty
sequential-await behavior, now visible in vivo. Direct's wall *dropped*
(30.4s → 25.6s): its parallel batching absorbs 100ms/call below the
between-run noise floor. At N=30/100ms toolplane still wins wall
despite the penalty (same-run ranges disjoint: 22.3–23.7 vs
24.3–27.0). The penalty scales as N × per-call latency — but be
careful with extrapolation in the other direction too: at N=100
toolplane's baseline lead is so large (20.2s vs 57.3s) that even +10s
of sequential awaits leaves it ~27s ahead; flipping the N=100 wall
verdict would take roughly 370ms per call. What
[#109](https://github.com/oneryalcin/toolplane/issues/109)'s host-side
parallel fan-out buys is therefore the N × latency term itself — real
seconds, not (at these scales) a verdict change; cost is untouched
either way (latency is wall-only). n=3 caveat: the baseline and
lat100 toolplane ranges overlap by 1.2s (20.7–23.5 vs 22.3–23.7,
cross-run within one evening), so by this page's own † standard the
+2.7s is a median estimate whose arithmetic checks out, not a
resolved measurement.

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

## The hybrid facade: route natively or run code, per task (2026-07-11)

#110 showed deferred-loading clients do not pay a schema tax for many
tools, which undercuts the reason to hide everything behind three
meta-tools. So the **hybrid facade** (`serve mcp --hybrid`, #114)
re-exports every capability as an ordinary MCP tool *alongside*
`search_capabilities` / `execute_code`. The bet: the model calls a native
tool for a single lookup or an adaptive chain (where the pure facade pays
a discovery tax) and reaches for `execute_code` only when the work is a
loop or a join. Third arm, same harness, Sonnet 5, M=1, n=2, frozen wheel
(`run-20260711-001559`):

| task | direct | toolplane | hybrid | hybrid routed to |
|---|---|---|---|---|
| single | 3 reqs, $0.128 | 3 reqs, $0.15 | 3 reqs, **$0.141** | native `orders_get_order` |
| chain | 6 tools, $0.17 | $0.18 | 7.5 tools, **$0.23** | native tool per hop |
| loop (N=30) | $0.20 | $0.18 | **$0.15** | `execute_code` |
| loop100 | 103 tools, $0.33 | $0.18 | **$0.15** | `execute_code` |

**Routing was reliable at M=1** — identical across both reps, no failures.
The loop tasks went to `execute_code` every time (the routing-failure risk
the issue named — tool-calling a 100-item loop — never fired); single and
chain went to native tool calls every time. The transcripts show the tool
sequence directly: `loop100` hybrid is `ToolSearch → execute_code` (3 tool
uses total), `single` is `ToolSearch → orders_get_order`. (At M=15 this
reliability breaks down — see below.)

**Single is a request tie, not a cost tie.** Both arms take 3 model
requests, but hybrid costs ~11% more (per-rep $0.1412/$0.1413 vs direct's
$0.1279/$0.1275). The native tool call is one round trip either way; the
gap is the facade server's own overhead (its meta-tool schemas ride along
in context). Directionally consistent across both reps, so despite the
range technicality this is a small, real loss — not a wash.

**Hybrid wins the loops outright** — cheaper than *both* other arms. The
mechanism, corrected against the traces: *neither* arm ever calls
`search_capabilities` on the loop tasks (the earlier claim that hybrid
"skips search" was wrong — there was nothing to skip). The real difference
is snippet count: pure toolplane issued 2–3 `execute_code` calls per loop
run, hybrid issued exactly 1. Hybrid's re-exported tool descriptions give
the model the call shapes up front, so it writes one correct snippet
instead of iterating — one execute vs two or three is the whole win.

**Hybrid loses the chain** ($0.23 vs direct's $0.17). It routes correctly
— per-hop native calls, exactly what direct does — but pays a ToolSearch
to load the tools on top and runs heavier: ~1.9x the output tokens is the
real cost driver. (The raw tool-count gap, 7.5 vs 6, is partly an
artifact — the chain hybrid runs also issued Bash-exploration turns that
direct and toolplane did not; see the caveat below. Output tokens and
`reqs`, not tool count, carry the conclusion.) On an adaptive task with no
bulk step to amortize discovery, "direct's approach plus a discovery step"
is strictly direct plus overhead — the mild form of the "worst of both"
the issue warned about: not a routing failure, a discovery surcharge on
the one shape that cannot pay it back.

### …until you add servers: hybrid is the worst arm at M=15

The M=1 win does not survive scale. Same three arms, 15 configured servers
(**84** capabilities behind the facade — 2 orders tools + 82 distractors),
single and chain only — **loop and loop100 were not run at M=15**, so this
is a reversal on the two adaptive/lookup tasks, not on hybrid's positive
loop case (`run-20260711-002646`):

| task | direct | toolplane | hybrid |
|---|---|---|---|
| single | 3 reqs, $0.13 | 5 reqs, $0.19 | **6.5 reqs, $0.26** |
| chain | 7 reqs, $0.19 | 8 reqs, $0.23 | **10 reqs, $0.28** |

Hybrid is the **most expensive arm on both tasks measured**. The
mechanism, confirmed by tool count: the facade exposes 3 tools at any M;
the hybrid facade at M=15 exposes **87** — the 3 meta-tools plus one
re-export per capability (`--hybrid` re-exports `registry.all()`, and at
M=15 the registry holds every distractor server's 84 tools too). So hybrid
recreates exactly the flat tool surface the facade exists to avoid. Two
costs follow, both in the transcripts:

1. **Re-export does not reliably fix domain discovery — and routing
   itself becomes unreliable.** The two `single` reps diverged: rep 1
   searched `order status ORD`, then `order tracking database query shop
   ecommerce`, missed both (the client's ranking buries `orders_get_order`
   under built-ins and 83 sibling tools — the #115 ranking dilution,
   unmoved), fell back to a `toolplane` search, and used `execute_code`;
   rep 2 tried four different queries and *did* eventually reach the native
   `orders_get_order`. So at M=15 the native tool is found in 1 of 2 reps,
   after several failed searches either way — the clean M=1 routing does
   not hold.
2. **The re-exports inflate context.** When a `toolplane` search loads the
   facade tools, the 84 re-exported schemas load with them: hybrid's
   uncached input is ~43.5k–50.2k tokens across the two reps vs the
   facade's ~34.3k for the identical task. This — not the tool count — is
   the load-bearing cost driver behind the M=15 result.

So hybrid degenerates to the facade's discovery path *plus* tool-surface
bloat. The reversal tracks the capability count — the same M axis #110 and
#115 turned on — but note this is **two measured points (M=1 and M=15),
not a characterized curve**: nothing here tells you whether 5, 15, or 30
capabilities is the safe ceiling.

Caveats, disclosed: n=2, one model (Sonnet 5); **routing reliability is a
per-model behavior** — a hybrid facade's value rests on the model choosing
code-vs-tool correctly, which shifts across models, so this is a Sonnet-5
result. Bash-exploration turns are **not** arm-neutral on the losing task:
on `chain` they are hybrid-only (2/1 turns vs 0 for direct and toolplane
at both M values), so the raw chain tool-count gap overstates hybrid's
overhead — the `reqs` and output-token columns are the like-for-like
comparison and still carry the verdict. (No † in the hybrid table: every
hybrid cost range is disjoint from direct's at n=2 — single $0.141 vs
$0.128, chain $0.23 vs $0.17 — so these are small measured gaps, not
overlaps, modest reps notwithstanding.)

Verdict: **hybrid is a small- or curated-registry optimization, not a
general one.** With a handful of capabilities it wins the loops (beating
even pure code mode); the M=1 losses are single (a small ~11% cost
surcharge at request parity) and the pure adaptive chain (a discovery
surcharge). But re-exporting `registry.all()` is wrong at scale — at 84
capabilities it is the worst arm on both tasks measured, because it
rebuilds the flat surface without fixing the ranking dilution that made
the flat surface lose in the first place. The actionable design is
**selective re-export** (a curated allowlist or the hot capabilities),
filed as #125. The all-or-nothing `--hybrid` flag is therefore **not
shipped as a public feature** — it is unpublished (hidden from `--help`),
kept only so this benchmark stays reproducible; #125 supersedes it. Like
every result here it only helps deferred-loading clients — and note the
flag is not itself client-aware: it re-exports unconditionally, so on a
client without deferral (Codex) it would push all 84 schemas into context
up front. Curated re-export and client-awareness are both #125's job.

### Curated re-export does not rescue it either (2026-07-11, #125)

The obvious fix to the M=15 bloat is to re-export a *curated* subset —
only the single/adaptive tools — instead of the whole registry. #125 built
that (a `[hybrid] include = ["mcp:orders/*"]` config section, verified to
expose exactly the 2 orders tools at M=15, not 87). It did **not** restore
the win. Four arms, M=15, single and chain (`run-20260711-014100`):

| task | direct | toolplane | hybrid | curated |
|---|---|---|---|---|
| single | 3.5 reqs, $0.14 | 5 reqs, $0.19 | 6 reqs, $0.23 | 5 reqs, **$0.21** |
| chain | 7.5 reqs, $0.21 | 8.5 reqs, $0.24 | 9.5 reqs, $0.28 | 10 reqs, **$0.28** |

Curated beats hybrid-all (fewer tools, less context) but is **worse than
the plain facade** on single ($0.21 vs $0.19) and ties hybrid as the worst
arm on chain. Cutting the 82 distractors off the counter bought nothing —
because the distractors were never what buried the native tool. The
transcripts isolate two real causes, and both correct the "sibling
dilution" framing this doc used for #115:

1. **Built-in collision (confirmed, both curated reps and a direct rep).**
   The client's `ToolSearch("order status")` returns Claude Code's own
   built-in task tools — `TaskList`, `TaskCreate`, `Monitor`,
   `PushNotification` — *not* the domain tool. The competition for a
   natural domain query is the client's built-in vocabulary, not the other
   MCP servers, so curating the servers away is inert.
2. **Namespace flattening (n=1 observation, hypothesis).** In one
   transcript (`single-direct-rep1`) direct recovers from the first miss by
   searching the server name (`mcp__orders`) — the qualified name
   `mcp__orders__get_order` carries the domain, whereas every re-exported
   tool collapses to `mcp__toolplane__<leaf>` and loses that signal. This
   is a single-transcript observation, not established: the other direct
   rep found the tool on its first query, and one of curated's "extra"
   searches was a startup-timing artifact (the tool_result said the
   toolplane server was *still connecting*, not that the tool was
   unranked). So curated single took 2–3 searches vs direct's 1–2, and the
   flattening mechanism is a plausible contributor, not a proven one — it
   awaits #127's A/B. The re-export also carries the fat
   `search_capabilities`/`execute_code` domain-hint context.

So the binding constraint at scale is **client-side ranking** — a domain
tool competing against the client's built-in vocabulary — established by
the confirmed built-in collision plus the null result (curation changed
nothing), *not* by the flattening hypothesis alone. It is **not** the
number of sibling tools. Selective re-export is a correct, safe primitive,
and the config validator rejects a bare-wildcard *implicit* export-all —
but note an explicit broad glob (`mcp:*`) can still re-export most of a
catalog, so "curated" is a discipline the operator keeps, not a guarantee
the config enforces. On Claude Code it does not deliver the M=1 economics
at M=15 — and on the two adaptive tasks measured it is modestly *worse*
than the plain facade (single $0.21 vs $0.19; note chain's raw counts are
inflated by hybrid/curated-only Bash-exploration turns — 2 per rep vs 0 for
direct/toolplane — a disclosed covariate). The M=1 win was real; it does
not survive a realistic client tool population. The sharper open question
(#127) is not "curate better" but "can a re-exported tool be named so it
keeps a server-name signal and outranks the client's built-ins" — or
whether that ceiling is simply client-owned. **That A/B is the next
section; the answer is client-owned.**

### Naming the re-export does not lift the ceiling (2026-07-12, #127)

#125 left one lever untried: the re-export inherits the client-set
`mcp__toolplane__` prefix (nothing server-side changes that), but its
*leaf name* and *description* are ours. #127 pre-registered an A/B to test
whether pumping either with domain/query vocabulary lets a re-export reach
the first-search discovery a direct tool gets — or whether the ceiling is
client-owned. Three re-export arms, all curated to the same target tools,
differing only in a private `TOOLPLANE_HYBRID_SIGNAL` env knob (bench-only,
never public config):

- **control** — `orders_get_order`, description as-is;
- **name** — a query-shaped leaf carrying the domain + the description
  vocabulary, truncated at 64 chars (`orders_fetch_one_order_record_..._status`;
  on shipments the cap drops the tail, so `state` never makes it into the leaf);
- **description** — the domain word and leaf verbs front-loaded.

Primary outcome: the target tool is returned by the **first valid**
`ToolSearch` — a search that is not the "server still connecting"
cold-start artifact, which is counted separately and never scored as a
ranking miss. Because the built-in collision is nondeterministic (the same
query can hit or miss run to run), the **first-hit rate over reps** is the
unit of evidence, not any single transcript. (Two honesty notes on the
metric: it does not distinguish a keyword search from an exact-name
`select:` fetch — one orders control rep recovered by `select:`-ing a tool
whose name it had already seen, so control's 4/11 includes one non-ranking
hit, which if excluded only widens name's apparent edge; and a re-export's
name is visible in the client's deferred-tool listing without any search,
so first-search discovery is not the only channel.)

**Orders, M=15, n=11 per arm** (pooled over wheel `974e578`; the query the
agent chose varies, so a query-controlled column isolates the reps whose
first search was exactly `"order status"`):

| arm | first-hit | first-hit \| q=`order status` | searches→tool | cost |
|---|---|---|---|---|
| direct | **11/11** | **6/6** | 1.0 | **$0.15** |
| control | 4/11 | 2/6 | 2.0 | $0.19 |
| name | 6/11 | 5/6 | 1.0 | $0.19 |
| description | 4/11 | 3/5 | 2.0 | $0.19 |

On orders the name signal looks like it helps — query-controlled 5/6 vs
control's 2/6, and it reaches the tool in one search like direct. But it is
not significant overall (6/11 vs 4/11, Fisher p>0.6), and the mechanism is
suspicious: its leaf `orders_..._order_..._status` literally contains the
query word "status", which `orders_get_order` lacks. So the pre-registered
second domain deliberately breaks that coincidence.

**Shipments, M=15, n=9 per arm** — same single-lookup shape, but the tool
exposes the field as `state` while the task asks for a shipment's
`status`. "status" is a synonym **absent from the description** (so the
name-signal leaf cannot carry it) yet **still collides with the client's
built-in Task tools** (`ToolSearch("shipment status …")` →
`[TaskCreate, Monitor, PushNotification, TaskGet, TaskList]`, transcript-
confirmed), so the discovery *difficulty* is identical to orders:

| arm | first-hit | first-hit \| q=`shipment status` | searches→tool | cost |
|---|---|---|---|---|
| direct | **9/9** | **9/9** | 1.0 | **$0.15** |
| control | 2/9 | 0/2 | 2.0 | $0.18 |
| name | 3/9 | 0/2 | 2.0 | $0.20 |
| description | 3/9 | 0/5 | 2.0 | $0.17 |

The name signal's advantage **does not survive**: 3/9 vs control 2/9 vs
description 3/9 (Fisher p=1.0 — no detectable effect), searches→tool back to
2.0, query-controlled 0/2 = control 0/2. It only ever appeared on orders
because that query word happened to be in the description; where it is not,
there is nothing left. Direct, meanwhile, is robust in **both** domains
(11/11, 9/9, one search). The single strongest statistic in the dataset is
this gap, not the bump: on the exact query `"shipment status"`, direct hits
9/9 while every re-export arm hits **0/9** (Fisher p≈4e-5).

Two caveats belong on the table. First, the metric scores the agent's own
first query, and that phrasing varies within an arm (`shipment status`,
`shipment status tracking`, `shipment tracking status`, bare `shipment`) —
hit/miss tracks the *query family* more than the arm, which is why the
exact-query denominators are small (0/2, 0/5). The claim these support is
therefore the narrow one: *on Claude Code, in these two domains and this
query family, no server-side leaf name or description recovered direct's
first-search advantage.* Second, the orders "bump" was never significant to
begin with (query-controlled 5/6 vs 2/6 is Fisher p=0.24; pooled 6/11 vs
4/11 is p>0.6); the structural argument — a re-export leaf cannot carry a
query word that is not in its source description (and is bounded at 64
chars, so on shipments even `state` is truncated off) — does the real work,
not the small-sample deltas.

Conclusions, held to what the two domains isolate:

1. **The naming bump does not generalize.** On orders it was a suggestive-
   but-non-significant edge that traced to "status" being in that
   description; on shipments, where the leaf cannot carry the query word,
   there is no detectable name or description effect. Consistent with a
   lexical coincidence, not a naming rule — the overfitting risk #125
   flagged.
2. **The discovery ceiling is client-owned** — with the mechanism named as
   a hypothesis, not a proven cause. What is *established*: no server-side
   leaf name or description tested recovers a re-export's first-search
   discovery, and direct dominates every re-export on the identical exact
   query in both domains. The *leading explanation* is the client-set
   `mcp__<server>__` qualifier, which a re-export flattens to
   `mcp__toolplane__` — but this A/B varied only the leaf and description,
   never the server segment (and control's `mcp__toolplane__orders_get_order`
   already contains the domain token, so token-presence alone is not the
   differentiator). Isolating the qualifier would need the facade served
   under a domain-named server. The built-in collision (a domain tool
   losing to `TaskList`/`Monitor` for a generic query) is separately
   client-side and untouchable server-side.
3. **Discovery signal is economically inert here anyway.** Every re-export
   arm sits at ~$0.17–0.20 regardless of which search surfaced the tool;
   direct stays cheapest at $0.15. The cost gap #125 found comes from the
   facade round-trips, not from which search wins — so even a durable
   discovery win would not have closed it.

So the hybrid *optimization* thread closes: curated re-export is a correct,
safe, opt-in primitive (kept, #125), but on Claude Code no server-side name
or description tested recovers direct's first-search discovery at scale, and
the leading reason is a client-side ranking feature (the server-name
qualifier, plus the built-in collision) that toolplane does not control.

Provenance, stated precisely: the result rows carry `git_sha=dc59fe5` with
`git_dirty=true`, and — importantly — `dc59fe5` does **not** yet contain the
shipments fixtures or the `single_shipment` harness code (those land in
`14327d8`). So the shipments runs executed genuinely *uncommitted* harness
and fixture bytes; the dirty flag is real, not a result-file artifact. What
*is* anchored: the wheel hash `974e578` is byte-identical to a clean-tree
`dc59fe5` build (the `src/` code under test is unchanged, so the
interpreter/facade is the committed source), and the shipment fixtures'
recorded `fixtures_sha256` match `14327d8`'s committed bytes exactly. The
gap the harness leaves is that `bench/run.py` itself (prompts, metric,
wiring) is hashed nowhere, so for the shipments runs it is provably not the
recorded `git_sha`'s version — the numbers are independently reproducible
from the transcripts (a reviewer reparsed all 80 rows with zero
mismatches), but the byte-level provenance guard had a hole. This PR closes
it: `build_code_under_test` now stamps a `harness_sha256` on every row, so a
dirty `run.py` is provable from the row itself, not only from the coarse
`git_dirty` bit. (The rows in *this* dataset predate that field; their
provenance rests on the wheel + fixture hashes plus the transcript reparse.)

## Payload size and API granularity reverse the winner (2026-07-13, #117)

The original loop fixture exposed only `list_order_ids` + `get_order`, with
tiny records. That is a legitimate API shape, but it hides code
mode's context-isolation advantage and gives direct MCP no bulk endpoint as a
counterweight. #117 crossed two controlled axes at N=30 and M=1:

- payload padding: 0, 2 KB, or 20 KB per record;
- mutually exclusive API profiles: `fetch-one` or `bulk` (`get_orders`).

Both arms saw the same profile and bytes. Three counterbalanced reps per cell,
all 36 correct; medians below. Input is uncached input tokens.

| payload | API | direct | toolplane | cheaper arm |
|---|---|---:|---:|---|
| 0 | fetch-one | $0.17 / 23.5K input | $0.12 / 14.1K | toolplane, 32% |
| 2 KB | fetch-one | $0.30 / 60.8K | $0.12 / 15.5K | toolplane, 2.4x |
| 20 KB | fetch-one | $1.58 / 398K | $0.16 / 26.6K | **toolplane, 9.7x** |
| 0 | bulk | $0.10 / 13.4K | $0.13 / 14.5K | direct, 24% |
| 2 KB | bulk | $0.13 / 14.7K | $0.14 / 18.1K | direct, 8% |
| 20 KB | bulk | $0.18 / 26.0K | $0.23 / 39.8K | **direct, 21%** |

The interaction is the result. With fetch-one APIs, direct pulls records into
the conversation one call at a time while Toolplane performs the 30-record
aggregation inside Monty and returns the totals. Five of six high-payload
Toolplane reps later leaked one sampled record, still far less than all 30;
payload therefore turns a modest baseline win into 9.7x. Give the server a
bulk endpoint and the winner reverses at every payload: direct avoids
Toolplane's discovery and snippet overhead.

There is an important client mechanism in the bulk cells. At 2 KB and 20 KB,
Claude Code externalized the oversized direct MCP result to a local tool-result
file in all 6 reps, then the agent used Bash with `jq` or Python against that
file. The large payload therefore did *not* enter model context. This is real production-client
behavior and available to both arms, but it means the bulk result measures API
granularity together with Claude Code's oversized-result escape hatch—not a
client-independent one-call law. At zero padding the response stays inline and
direct still wins, so externalization is not the sole cause of the reversal.

Toolplane also left performance on the table: several bulk reps returned or
sampled the full payload before aggregating, producing 2–4 `execute_code`
calls. Classification records 19 staged-after-success executes across the 18
Toolplane runs. The measured comparison is agent behavior, not an ideal hand-
written snippet.

The honest envelope is therefore conditional on API shape. Code mode is a
large win when many fat per-record calls must be composed; bulk retrieval plus
Claude Code's file externalization and Bash aggregation is cheaper here.
Filter placement remains a separate axis rather than being folded into this
result. This is one Claude Code/Sonnet run date with n=3; odd reps leave the
counterbalanced first-position split uneven by one, and payload/granularity
cells ran in a fixed order. The 8% bulk/2 KB edge is therefore descriptive,
not a precise or client-independent causal estimate.

Provenance: `run-20260713-040737`, clean committed tree at `57e9165`, wheel
`faa5de87`, harness `d3ad3224`; raw JSON and full transcripts are committed.

## Sessions preserve data, but the savings do not compound (2026-07-13, #119)

Every earlier paid cell was a fresh one-shot conversation. #119 keeps one
Claude Code process and its stdio MCP server alive for six related turns over
30 orders padded by 2 KB each: load + regional totals; four filters/aggregates
over the same data; then a separately scored reset/refetch task. Bash, files,
web, and helper agents are disabled. The shared first prompt asks either
surface to retain reusable data using its native mechanism: conversation
history for direct, Monty's `orders_cache` for Toolplane.

Four counterbalanced Sonnet reps, all 48 answers correct:

| six-turn session | direct | toolplane | result |
|---|---:|---:|---|
| cumulative cost, median | $0.44 | **$0.25** | Toolplane 43% cheaper |
| observed cost range | $0.43–0.87 | **$0.20–0.37** | ranges do not overlap |
| peak request context, median | 71.2K | **34.1K** | Toolplane 52% lower |
| observed peak range | 70.6K–121.6K | **32.0K–43.7K** | ranges do not overlap |

That is a real session-level win, but **not for the compounding reason we
predicted**. The decomposition matters:

| phase | direct median | toolplane median | interpretation |
|---|---:|---:|---|
| initial load + totals | $0.286 | **$0.122** | 57% cheaper: the full 60 KB dataset stays behind Toolplane |
| four reuse turns combined | $0.094 | $0.092 | effectively tied; ranges overlap |

Both arms made **zero fixture calls** on reuse turns 2–5. Direct did not
refetch: Claude reused the order records already carried in conversation.
Toolplane genuinely reused `orders_cache` in Monty, but each small follow-up
took two model requests (tool call + answer) while direct answered from
conversation in one. Toolplane's half-sized context roughly balances that
extra request, so the cost gap stays flat instead of widening. No compaction
event fired in any run, even at direct's 121.6K-token outlier; this experiment
therefore does not establish an earlier-compaction advantage.

The reset phase proves the runtime contract, not an arm-to-arm economic race.
Every Toolplane rep executed `await reset_session()` in its own call, then made
31 fresh fixture calls and answered correctly. Direct has no equivalent reset:
three reps reused conversation without refetching; one made 30 fresh calls and
created the $0.87/121.6K outlier. Comparing those reset-turn costs as if they
were the same operation would be misleading.

Agent quality remains visible. Toolplane's first-load fixture calls ranged from
31 to 93 because some snippets fetched the dataset again after an error; the
session still won on total cost and context in every rep. The result is about
observed agent behavior, not an ideal handwritten cache setup. Some successful
snippets also returned one or three padded sample records, so “stays behind”
describes the full dataset rather than claiming that no sample bytes escaped.

### Snapshot scaling is linear and small at 10 MB

Monty snapshots the complete session before every run to make timeout rollback
safe. Seven local measurements per size, against the same frozen wheel:

| live namespace payload | snapshot bytes | median `dump()` | median no-op run including snapshot |
|---:|---:|---:|---:|
| 1 KB | 1.2 KB | 0.001 ms | 0.20 ms |
| 100 KB | 100.2 KB | 0.017 ms | 0.19 ms |
| 10 MB | 10.0 MB | 2.62 ms | 3.48 ms |

Serialized size and the Python-visible allocation are approximately linear in
live state. At 10 MB the mandatory snapshot is measurable but not a practical
bottleneck for these tasks. This is not a process-RSS measurement, and it does
not extrapolate to 100 MB+ namespaces or the future AsyncMonty subprocess path
in #88.

The sharper conclusion is conditional: persistent sessions work and halve
context here, but four reuse follow-ups are not enough to turn that context reduction
into compounding dollar savings. A longer run that actually crosses the
client's compaction threshold remains unmeasured.

Provenance: `longitudinal-20260713-095957`, clean committed tree at `871ce53`;
all rows stamp the frozen wheel, fixture set, base harness, and longitudinal
harness hashes. Raw JSON and full transcripts are committed. One disclosed
post-hoc correction replaces the per-turn token fields with the verbatim
`usage` object from each recorded result event: the first harness incorrectly
differenced those already-per-turn counters. Costs, peak context, answers, and
every published table value were unaffected; no run was re-executed.

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
*(This paragraph describes toolplane 0.4.0 as shipped. The discovery-tax
fix measured in "Cutting the discovery tax" above moved the crossover
under 30.)*

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
  into every toolplane number above. *(Since resolved: the harness now
  persists transcripts and `bench/classify.py` splits the causes — the
  dominant one was return-shape guessing, fixed and re-measured in
  "Cutting the discovery tax" above.)*
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
