# The Discovery Tax, Explained (verbatim)

*Saved verbatim from the working session, 2026-07-09 — the plain-words
explanation of PR #111 (#106 discovery-tax fix + #104 transcript
instrument). Companion to [EXPLAINER.md](EXPLAINER.md), which covers the
original #72/#105 envelope measurement.*

---

Pull up a chair. Here's the whole thing, no jargon.

## The mystery we started with

Yesterday we published an honest measurement: our clever system (write one
Python snippet instead of making 100 tool calls) **loses money on small
jobs**. For a job touching 30 records, the dumb way cost $0.20 and our way
cost $0.24. That bothered us. The snippet itself runs in milliseconds — so
where do the four cents go?

When something costs more than physics says it should, you don't theorize.
You **watch it actually happen**. So the first thing PR #111 built was a
camera: every benchmark run now saves its complete transcript — every turn
the AI took, every result it got back, byte for byte. And a little program
that reads those transcripts and counts things.

## What the camera saw

Imagine you hire a new clerk and say "go total up the orders." Before
touching a single order, the clerk:

```
turn 1:  asks the directory     "what tools exist here?"      (search)
turn 2:  asks the records office "what arguments do they take?" (schemas)
turn 3:  reads the office manual  "what are these called
                                   in Python, exactly?"        (manifest, 2,700 chars)
turn 4:  writes the code... gets it slightly wrong
turn 5:  writes it again                                       ← the actual work
```

Why three trips before any work? Because we — the people who built this —
had **scattered the three facts the clerk needs across three different
offices**. The tool's official name lived in search. Its arguments lived in
the schema office. Its *Python name* — the thing you actually type — lived
only in the manual. No single trip gave you all three. And our own
instruction sheet proudly told the clerk: visit all the offices first!

That's the discovery tax. It isn't physics. It's bureaucracy **we built**.

## The fix: put it all on one card

Now, when the clerk asks "what tools exist?", the answer looks like this:

```
- `await orders_get_order(order_id=<string>)` — Fetch one order record. [mcp:orders/get_order]

Rules: everything is async — always await; keywords only;
results come back plain — no wrapper to unwrap.
```

One card. The name, the exact way to call it, the house rules. Nothing to
look up anywhere else for a simple job.

And there's a lovely detail in that last rule. The transcripts caught the
clerk doing something we'd never have guessed: after getting a result, it
*assumed* the answer must be gift-wrapped — it wrote `ids["result"]` and
`order["value"]`, unwrapping boxes that don't exist, got a TypeError, then
**burned a whole turn just printing the type of the thing** to see what it
was holding. A probe! We only knew because we filmed it. One sentence on
the card — "results come back plain" — and that entire behavior vanished.

## The result

```
TURNS PER JOB (toolplane arm)          COST PER JOB
before:  ██████████ 10-12              before:  $0.24 - $0.28
after:   █████ 5-6                     after:   $0.18   flat
```

And here's the chart that matters — cost against job size:

```
 cost
$0.33 |                                    ● direct
      |                          the dumb way climbs:
$0.26 |            ●             every record = more
      |     ●                    generated tokens
$0.20 | ●●..........●
$0.18 | ○-----○------------○     ← our way: FLAT
$0.14 |
      +----+-----+----------+----
           1     30        100   records touched
```

That flat line is the whole thesis made visible. **$0.18 whether the job
touches 1 record, 30, or 100** — because the loop lives inside one snippet,
and one snippet costs the same no matter how many times it loops. The dumb
way pays per record, forever.

Yesterday the crossover — the point where we start winning — sat somewhere
between 30 and 100 records. Today:

```
before:  lose ............ lose | ??? | win
                          30         100

after:   lose | win  win  win  win  win
              ↑
        somewhere under 30 (we haven't found the exact spot)
```

At 30 records we now win outright on speed (21s vs 30s) and win the median
on cost. At 100 we're 45% cheaper and nearly 3× faster. At **one** record
we still lose, four cents' worth — the floor is one search turn plus one
execute turn, and no cleverness removes it. We say so in print.

## Then we tried to break it

Three independent reviewers were told: recompute everything from the raw
film, trust nothing. Every published number reproduced. But they caught
real things:

- Our shiny "exact call shapes" produce **broken Python** for tools with
  parameter names like `from` (a reserved word). We'd told agents "use this
  verbatim" — verbatim SyntaxError. Fixed: those fall back to a form that's
  always valid.
- On a server with sessions, a tool that happens to be named
  `reset_session` gets shadowed by the actual session-reset button — our
  card would advertise a call that **wipes the agent's workspace** instead
  of running the tool. Fixed.
- And one sentence in our own write-up said the TypeError probes appeared
  in the "pre-fix" runs. Our own footage says they appeared in the
  *half-fixed* runs. In a document whose whole brand is "check our film,"
  that sentence had to die. It died.

## The one-sentence version

**The tax on being clever wasn't nature — it was our own paperwork. We
filmed the clerk, saw exactly which corridors we'd made it walk, tore down
the corridors, and now the clever way costs $0.18 no matter how big the
job is.**

The parts we still owe: where exactly under 30 the crossover sits, and
whether the one-card trick holds up on messier tool collections than our
tidy two-tool test server. The camera's built in now, so finding out is
cheap.
