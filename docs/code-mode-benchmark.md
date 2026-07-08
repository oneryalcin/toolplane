# The Code-Mode Envelope, Measured

*2026-07-08 · toolplane 0.4.0 · Claude Code 2.1.204 · claude-sonnet-5 ·
harness and raw results in [`bench/`](https://github.com/oneryalcin/toolplane/tree/main/bench)*

The code-mode thesis — one Python snippet looping over tools beats N
individual tool-call round-trips — is repeated in vendor blog posts and was
the founding premise of this project. We had never measured it ourselves,
and the industry numbers predate aggressive prompt caching in production
clients. So we measured it, and we are publishing where code mode **loses**
along with where it wins, because a benchmark you would only publish if it
wins is marketing, not evidence.

## Setup

Two arms, same model, byte-identical prompts, same deterministic MCP server
(an order store: `list_order_ids`, `get_order(id)` — fetch-one-record
shape, so aggregate questions force round-trips):

- **direct** — the server registered straight into Claude Code. Classic
  MCP usage: one tool call per record.
- **toolplane** — the same server behind the toolplane facade. The agent
  discovers capabilities and writes Python snippets against them.

Each cell is the median of 3 headless `claude -p` runs from a fresh empty
directory with `--strict-mcp-config` (no other servers, no CLAUDE.md).
Correctness is validated programmatically against the shared dataset.
"Uncached in" counts input plus cache-creation tokens; cache reads are
reported by the client but priced ~10x lower, and both arms benefit from
them equally.

## Results

**Aggregate over 30 records** (compute per-region totals):

| arm | ok | tool calls | output tokens | uncached in | cost | wall |
|---|---|---|---|---|---|---|
| direct | 3/3 | 32 | 2394 | 34,672 | $0.20 | 23.2s |
| toolplane | 3/3 | 10 | 1297 | 32,793 | **$0.24** | **26.8s** |

**Aggregate over 100 records** (same task, bigger store):

| arm | ok | tool calls | output tokens | uncached in | cost | wall |
|---|---|---|---|---|---|---|
| direct | 3/3 | 103 | 7569 | 45,969 | $0.33 | 53.6s |
| toolplane | 3/3 | **11** | **1760** | **34,646** | **$0.28** | **36.6s** |

**Filtered count over 30 records** (EMEA orders over 500):

| arm | ok | tool calls | output tokens | uncached in | cost | wall |
|---|---|---|---|---|---|---|
| direct | 3/3 | 32 | 1984 | 34,491 | $0.19 | 20.6s |
| toolplane | 3/3 | 7 | 1016 | 32,224 | $0.22 | 22.5s |

**Single record lookup** (one `get_order` call):

| arm | ok | tool calls | output tokens | uncached in | cost | wall |
|---|---|---|---|---|---|---|
| direct | 3/3 | **2** | **171** | **31,122** | **$0.14** | **6.9s** |
| toolplane | 3/3 | 9 | 888 | 35,445 | $0.26 | 21.7s |

## The envelope

**Code mode loses below roughly N≈30–50 tool interactions per task, and
wins increasingly above it.**

- **Single lookups: direct wins decisively** — half the cost, a third of
  the wall-clock. Code mode pays a fixed discovery tax (capability search,
  schema fetch, snippet writing, and occasionally a failed first snippet
  recovered via the namespace manifest) that one tool call never amortizes.
- **At 30 records: near parity, direct slightly ahead** on cost and
  wall-clock, even though toolplane already uses 3x fewer round-trips and
  half the output tokens. This is the finding that dates the 2023-era
  framing: **round-trips are no longer where the money is.** Prompt caching
  makes each additional tool-call turn cheap on input; 30 cheap round-trips
  cost less than code mode's fixed overhead.
- **At 100 records: code mode wins everything** — 15% cheaper, 32% faster,
  4.3x fewer output tokens, 9x fewer round-trips. Direct's cost and
  latency scale with N (every record fetched becomes conversation context,
  and 100+ sequential turns add up even when cached); toolplane's stay
  flat, because N lives inside one snippet.

So the honest one-liner is not "code mode is faster." It is: **code mode
turns O(N) conversations into O(1) conversations, and the fixed cost of
that transformation pays for itself somewhere between 30 and 100 tool
interactions in today's Claude Code.** Below that, plain MCP is the right
tool, which is why toolplane serves both: any server behind the facade can
also be registered directly.

There is a second effect the tables understate: at N=100, direct pushed
7.5k output tokens and 104 turns for a three-line answer. Context growth
compounds in long agent sessions — a real workday session doing five such
tasks accumulates the transcripts of 500 tool calls in the direct shape,
versus ~50 in code mode. The benchmark measures single tasks; the
compounding favors code mode more than these numbers show.

## Limitations, honestly

- One client (Claude Code), one model (claude-sonnet-5), medians of 3 runs
  on one machine, one day. The harness is checked in and cheap to rerun —
  treat these as one data point with error bars, not a law.
- The server is a deterministic toy. Real servers have slower tools
  (network-bound), which shifts the envelope **toward** code mode (each
  direct round-trip eats a full tool latency; a snippet still pays them
  but without per-call model turns).
- Every toolplane run used multiple `execute_code` calls (median 2, up to
  8 in one N=100 run) plus one namespace-manifest read. Some of that is
  deliberate staged execution — with persistent sessions, "explore first,
  compute second" is legitimate code-mode style — and some is
  failed-snippet retries (the monty dialect tax). The harness does not yet
  persist transcripts, so we cannot split the two; whichever mix it is,
  it is fully priced into every toolplane number above.
- Both arms inherit Claude Code's system prompt and caching; we measure
  the production experience, not an idealized API loop.

## Reproduce it

```bash
git clone https://github.com/oneryalcin/toolplane && cd toolplane
uv run python bench/run.py --reps 3
uv run python bench/run.py --reps 3 --tasks loop100
```

Raw per-run JSON for the tables above is committed under
[`bench/results/`](https://github.com/oneryalcin/toolplane/tree/main/bench/results).
