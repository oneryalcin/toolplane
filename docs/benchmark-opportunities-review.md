# Benchmark and Optimization Opportunities Review

*Verbatim engineering review recorded 2026-07-10 after examining PRs #105, #110, #111, and #112, their linked issues, the benchmark harness, raw results, and representative transcripts.*

I reviewed PRs **#105, #110, #111, and #112**, their linked issues, the harness, raw results, and representative transcripts. The working tree remains clean.

## What the recent work established

- **#105:** Real Claude Code A/B harness; code mode wins through flat model-visible output/context growth, not fewer API requests.
- **#110:** Claude Code’s deferred loading largely eliminates the expected many-server schema tax.
- **#111:** Transcript instrumentation found the discovery ceremony and return-envelope guessing. Inlining executable call shapes cut toolplane from roughly 10–12 client turns to 5–6 and ~$0.24–0.28 to ~$0.18.
- **#112:** Current crossover is a **20–30 parity region**, direct wins small/adaptive tasks, and Monty’s sequential awaits add approximately `N × tool latency`.

This is unusually honest engineering evidence. The raw artifacts and adversarial reviews are excellent. But it is **not paper-grade yet**: `n=3`, fixed arm order, one model/client, and a synthetic two-tool server remain material limitations.

## My strongest opinion: the biggest opportunity is a hybrid surface

PR #110 undermines one of the original architectural assumptions: on clients with deferred loading, exposing many direct tools does **not** necessarily put all schemas into context.

That suggests an optional **hybrid Toolplane facade**:

- re-export underlying capabilities as ordinary MCP tools for single/adaptive calls;
- retain search/code execution for loops, joins, and large payloads;
- let native ToolSearch defer everything until needed.

Potentially:

- single lookup gets direct-like performance;
- adaptive chain uses normal tool calling;
- bulk workflows use code mode;
- users still configure capabilities once.

I would benchmark three arms before changing architecture:

1. direct MCP;
2. current three-tool facade;
3. hybrid facade.

If the model routes reliably, hybrid could dominate the current envelope. On clients without deferred loading it may be undesirable, so it should probably be client-aware or opt-in.

## Remaining discovery overhead is visible in the transcripts

Counting unique transcript `request_id`s gives the actual observable model requests after #111:

| Task | Direct | Toolplane |
|---|---:|---:|
| single | 3 | 5 |
| N=30 | 4 | 6 |
| N=100 | 5 | 6 |

For single lookups, all three toolplane reps first made a native `ToolSearch` that failed to find the domain-hidden facade, then searched again for Toolplane. Aggregate runs often needed another `ToolSearch` to load `execute_code` after loading `search_capabilities`.

So “one facade discovery call” is true but incomplete: **client-native double discovery remains**.

Experiments worth trying:

- one MCP tool with `action=search|execute`, so it stays loaded after search;
- descriptions designed so native ToolSearch loads search and execute together;
- dynamically include capability-domain keywords/tags in the search tool description;
- if possible, mark the tiny facade surface as non-deferred.

This could remove another model request without changing the runtime.

## I would elevate `call_many` above its current priority

Issue #109 is not merely a wall-clock polish. Most real network APIs are commonly in the 100–500 ms range. At N=30, sequential Monty calls can erase the current wall advantage quickly.

The helper should probably be bounded and resilient:

```python
await call_many(
    "mcp:orders/get_order",
    [{"order_id": oid} for oid in ids],
    max_concurrency=8,
)
```

Requirements:

- stable result ordering;
- configurable concurrency;
- per-item errors rather than one opaque aggregate failure;
- one audit event per underlying call;
- identical behavior across backends;
- rate-limit-safe defaults.

Benchmark a latency grid such as 0/50/100/250/500 ms at N=30 and N=100, not only one 100 ms point.

## Important benchmark gaps

### 1. Payload size and API granularity

The current server has tiny records and intentionally lacks bulk retrieval. Those choices pull in opposite directions:

- tiny payloads understate code mode’s context-isolation benefit;
- no bulk endpoint maximally penalizes direct MCP.

Add axes for:

- record/result size: 0.2 KB, 2 KB, 20 KB;
- list IDs + fetch-one versus paginated full records versus bulk endpoint;
- filtered server-side query versus client-side filtering.

These are likely more informative than sharpening N=20–30 further.

### 2. Real composite workloads

The central product claim is composition, but the measured workload is still one MCP server. A frozen, deterministic workflow should combine:

- two MCP servers;
- an allowlisted CLI;
- pagination;
- a join/filter;
- an artifact output;
- optionally one transient failure.

A cassette-backed GitHub/git task would be much more representative while remaining reproducible.

### 3. Longitudinal sessions

All paid runs are fresh one-shot sessions. That misses Toolplane’s persistent namespace and the claimed long-conversation context advantage.

Measure 5–10 related tasks in one conversation:

- cumulative cost;
- peak/final context;
- compaction behavior;
- refetching;
- correctness after session reuse/reset.

Also benchmark Monty’s required pre-run snapshot as session state grows. The snapshot must remain for timeout rollback, but its time and memory scaling are currently unmeasured.

### 4. Retrieval quality

The distractors are semantically distant. Test near-misses such as:

- `orders`, `orders_legacy`, `invoices`, `shipments`;
- paraphrased user vocabulary absent from exact-word descriptions;
- tasks requiring tools from multiple relevant servers.

Toolplane’s exact-word registry search is likely the next correctness bottleneck at realistic catalog sizes.

## Harness improvements before more public claims

1. **Counterbalance/randomize arm order.** Direct always runs first, confounding arm with cache warmth and time drift.
2. **Run immutable code.** Build a wheel or snapshot worktree and record Git SHA/source hash per row. “Do not edit while running” already failed once.
3. **Record exact model requests.** Unique `request_id` is stronger than inferring requests from cache-read arithmetic.
4. **Report final/peak context**, not only summed uncached-input tokens.
5. **Add a restricted-built-ins lane.** Current transcripts show Bash use; latest runs are auditable, but a no-Bash/Read lane would eliminate shortcut concerns.
6. **Use ≥5–10 reps for headline cells** and bootstrap paired differences. Min/max overlap is an honest descriptive annotation, but not statistical inference.
7. Separate startup, model API time, sandbox execution, and tool dispatch. This will show where an optimization actually acts.

## Runtime optimization not covered by the benchmark

`register_mcp_config()` initializes servers sequentially. A local probe showed one-server registration dominated startup while subsequent calls reused the process and were fast. With many remote servers, sequential `list_tools()` calls and one slow/unavailable server could become a substantial cold-start problem.

Measure M-axis startup independently, then consider concurrent initialization with deterministic registry insertion, timeouts, and partial-server health reporting.

## Immediate communication issue

`README.md` still headlines the shipped **0.4.0** result:

- direct ~20% cheaper at N=30;
- toolplane ~15% cheaper at N=100;
- crossover between 30 and 100.

Main now measures a different product:

- parity around N=20–30;
- toolplane ~$0.18 at N=30/100;
- roughly 45% cheaper at N=100.

That version distinction is defensible while PyPI remains 0.4.0, but it is easy for readers to interpret as the current repository result. A 0.4.1 release and versioned “shipped vs main/current” headline should be near-term.

## Bottom line

I would **not** spend the next cycle buying more N=20–30 repetitions. The crossover is sufficiently understood for product decisions.

My priority order would be:

1. benchmark the **hybrid facade**;
2. remove the remaining native ToolSearch/double-discovery requests;
3. implement and measure bounded `call_many`;
4. add one realistic MCP+CLI composite workload with payload/granularity axes;
5. harden the harness and run cross-client;
6. measure multi-task sessions and many-server startup.

Those have a much higher chance of changing the product than another synthetic crossover sweep.
