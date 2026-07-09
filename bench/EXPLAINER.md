# The benchmark, explained at a whiteboard

*A plain-language companion to [the results page](https://oneryalcin.github.io/toolplane/code-mode-benchmark/).
Numbers below are medians from the committed raw JSON in `results/`.*

## The question

Your agent needs to answer: *"what's the total order amount per region?"* — and the order store only lets you fetch **one order at a time**.

There are two ways an agent can do this:

**Way 1 (classic MCP, "direct"):** the agent calls the tool over and over.

```
Agent: get_order("ORD-001")  →  {amer, $520}
Agent: get_order("ORD-002")  →  {apac, $610}
Agent: get_order("ORD-003")  →  {emea, $130}
        ... 97 more times ...
Agent: "okay, the totals are..."
```

**Way 2 (toolplane, "code mode"):** the agent writes one small program.

```
Agent: execute_code("""
    total = {}
    for oid in await list_order_ids():
        o = await get_order(oid)
        total[o['region']] += o['amount']
    return total
""")  →  done, one shot
```

Everyone (including us) *believed* Way 2 is better because "each tool call is a slow, expensive round-trip to the model." But belief isn't measurement. So we ran both ways, for real — real Claude Code, real money, same model, same prompt, same data — and counted everything: dollars, seconds, tokens. Three repeats of each. All raw data committed.

## What we found

```
COST ($ per task, median of 3 runs)

single lookup    direct     ██████ 0.14        ← direct wins big
(1 order)        toolplane  ███████████ 0.26

30 orders        direct     █████████ 0.20     ← direct still wins
                 toolplane  ██████████▌ 0.24

100 orders       direct     ██████████████▌ 0.33
                 toolplane  ████████████ 0.28  ← toolplane wins

WALL CLOCK (seconds)

single           direct     ███ 7s             ← direct 3x faster
                 toolplane  █████████ 22s

30 orders        direct     ██████████ 23s     ← basically a tie
                 toolplane  ███████████▌ 27s

100 orders       direct     ███████████████████████ 54s
                 toolplane  ████████████████ 37s   ← toolplane 32% faster
```

So the picture is a crossover:

```
cost
 │
 │                                    ● direct
 │                          ●
 │      direct cheaper     ╱   ← crossover somewhere
 │  ●━━━━━━━━━━━━━━●━━━━━━╳       in here (unmeasured!)
 │  ○──────────────○──────╲───────○ toolplane (nearly flat)
 │                          toolplane cheaper
 └──┬──────────────┬──────────────┬────── N (orders per task)
    1              30            100
```

**Code mode loses on small tasks, ties around 30, wins clearly at 100.** For a single lookup it's not even close — code mode pays a "discovery tax" (find the tools, read the schemas, write a program) that one tool call never earns back.

## The surprise (the best part)

Here's where it gets Feynman-fun. We assumed direct would lose at N=100 because "103 tool calls = 103 slow round-trips to the model." Then a reviewer did something beautiful: instead of trusting our story, he **checked it against our own data**.

Every trip to the model re-reads the conversation from cache, and cache reads get counted. So: if direct really made ~104 separate trips, its cache-read count should be huge — about 4.5 *million* tokens. The actual number? ~159 thousand. That's only **3–4 trips**.

Claude Code was *batching* — asking for dozens of `get_order` calls **in parallel, inside one trip**:

```
what we imagined:              what actually happened:

trip 1: get ORD-001            trip 1: get ORD-001..050   (parallel!)
trip 2: get ORD-002            trip 2: get ORD-051..100   (parallel!)
trip 3: get ORD-003            trip 3: "totals are..."
...104 trips                   ~3-4 trips total
```

Funnier still: toolplane's calls (search → schemas → run code → retry) can't be parallelized, so **toolplane made 2–3x MORE round-trips than direct... and still won at 100 orders.**

So the folklore mechanism is dead. Why does direct actually lose at scale? Two things that grow with N no matter how clever the batching is:

1. **The agent must *write out* every tool call.** Each `get_order("ORD-042")` is ~70 tokens the model generates. At 100 orders that's 7,500 output tokens — the expensive kind — versus toolplane's 1,760 (the program is the same size no matter how many orders it loops over).
2. **Every result piles into the conversation.** After the direct run, the chat is carrying 100 order records as baggage for everything that comes after. The code-mode version carries a 10-line program and one answer.

That's the honest law: **code mode doesn't make conversations faster — it makes them O(1) instead of O(N).** You write the loop once; N lives inside the sandbox instead of inside the chat.

## What we're careful about

- n=3 per cell, one model, one client, one day — a data point with error bars, not physics. One toolplane run at N=100 hit a bad snippet-retry streak and cost more than every direct run. The direction is consistent; the margins aren't tight.
- The crossover is *somewhere between 30 and 100*. We measured the endpoints, not the middle, and we say so instead of drawing a fake curve.
- Our first version of the harness had a bug that made *correct* answers look wrong (agents wrote `4520.50`, our checker demanded `4520.5`) — fixed, disclosed, rescored from recorded answers.
- And we publish the losing cases on purpose: below ~30 tool interactions, just use plain MCP. Toolplane supports both, so that's not a concession — it's usage guidance.

That's the whole thing: we asked a question everyone thought was already answered, the answer was yes-but-for-a-completely-different-reason, and the different reason ("output tokens and context, not round-trips") is more useful than the slogan we started with.

---

## Follow-up: where exactly does the time go at 30 orders?

A fair intuition says code mode should already win at 30 orders — one
snippet versus 30 fetches! Here is a real 30-order run from the raw data,
laid side by side (times from `api_duration_ms`; the call sequences are
verbatim `tool_call_names`):

```
DIRECT (23s wall, ~3 API trips)          TOOLPLANE (27s wall, ~10 API trips)

trip 1  list_order_ids                   trip 1  Bash (peek around)
trip 2  get_order x30, IN PARALLEL       trip 2  ToolSearch (load tool schemas)
trip 3  write the answer                 trip 3  search_capabilities
                                         trip 4  search_capabilities (again)
        the 30 fetches cost ~zero        trip 5  get_capability_schemas
        turns — the client fires         trip 6  ToolSearch
        them all at once; the           trip 7  read namespace manifest
        server answers in ~ms            trip 8  ToolSearch
                                         trip 9  execute_code  (attempt 1)
                                         trip 10 execute_code  (attempt 2) ✓
```

The loop is **not** where toolplane spends time. Inside `execute_code`,
the snippet's 30 `get_order` bridge calls hit a local process and finish
in milliseconds — that part works exactly as advertised.

The time goes into **sequential model turns that exist regardless of N**:

```
toolplane's ~27s, decomposed:

startup (uv + facade + monty boot)      ██ ~2s        (direct pays ~1.5s too)
discovery: search + schemas + manifest  ██████ ~6-8s  ← 3-5 turns, fixed tax
snippet writing + attempt 1             ████ ~4s
retry / staged attempt 2                █████ ~5s     ← the dialect tax
useful work (30 bridged calls + answer) ███ ~3s
turn overhead (each trip re-reads ~30k
  context and pays time-to-first-token) woven through all of the above
```

Meanwhile direct's 23 seconds are almost all *output generation*: it must
write ~2,400 tokens of tool-call text (30 × ~70 tokens each), but it pays
almost nothing for turns because the client batches the calls in parallel.

So at N=30 the race is: toolplane's **fixed ~7 sequential turns** versus
direct's **~2,400 generated tokens**. Those happen to nearly tie in time,
and direct wins on money (toolplane's ten trips each re-read a large
cached prefix — 2.6x more cache reads — and cache reads aren't free).

At N=100 the same fixed tax is unchanged (~11 trips) while direct's
output grows to ~7,600 tokens and ~50s of generation. That's the
crossover: **fixed turns vs linear output**.

Is the overhead "only the loop task"? No — it's the same fixed tax on
every task shape (you can see the identical discovery sequence in the
single-lookup runs, which is why they lose 3x). The task shape only
decides whether there is enough N to amortize it.

The actionable product insight: the tax is 3–5 discovery turns plus a
retry. Cut the average discovery to one turn (manifest-first instead of
search→schemas→retry) and the crossover moves meaningfully left. That is
now tracked as a product goal, born from this measurement.

## Follow-up: does code mode use less context window?

At 30 orders — **no, and that surprised us too.** Total fresh context
written per task ("uncached in"):

```
30 orders   direct    ████████████████▉ 34.7k tokens
            toolplane ████████████████ 32.8k tokens    ← ~tie

100 orders  direct    ██████████████████████▌ 46.0k    ← +160 tokens/record
            toolplane ████████████████▉ 34.6k          ← flat
```

Two effects cancel at small N: code mode saves the per-record baggage
(~160 tokens per order that lands in direct's conversation) but pays a
fixed context cost of its own — the facade's tool schemas, the usage
skill, and the namespace manifest the agent reads (~2–4k tokens).

At 30 records: saves ~4.8k, pays ~3k. A wash.
At 100 records: saves ~16k, pays the same ~3k. Clear win — and direct's
line keeps climbing with N while toolplane's stays flat.

So the context-window claim has the same shape as everything else in this
benchmark: **it's not "code mode uses less context" — it's "code mode's
context is constant in N."** Below the crossover the facade's own
overhead eats the savings; past it, the savings compound. And in long
multi-task sessions the flat line is worth more than these single-task
numbers show (each direct task *permanently* adds its records to the
conversation) — but that's a hypothesis we haven't measured, and we label
it as one.
