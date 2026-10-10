# Persistence follow-up: results (2026-10-10)

The registration is in `PREREG-persistence-2026-10.md` (no amendments).
The registered analysis output is
`results/prereg-persistence-2026-10-analysis.md`, written by:

```bash
.venv/bin/python bench/analyze_persistence.py \
  --sessions bench/results/longitudinal-20261010-191310.json \
  --runs bench/results/run-20261010-192444.json
```

This doc reads those verdicts. It doesn't change any of them.

## Provenance

- **Code under test.** Every row, calibration included, is
  `git_dirty=false` at `4e8a107` (the registration's merge), wheel
  `c62640e59c9f`. Fixtures, harness and longitudinal-harness hashes are
  uniform; the analysis did not VOID.
- **Client.** Claude Code 2.1.296 on every row, auto-update off.
- **Model.** `claude-sonnet-5-5` on every row. Filler `varied` on every
  session.
- **Completeness.** 24 sessions (8 per arm) and 32 slice runs (8 per
  cell). No session died, none timed out, every client exited 0.
- **Spend: $8.00 recorded**, under the $10 cap and near the $8
  projection. Calibration K $0.61, L $4.64, S $2.74. Nothing is
  unpriced: there were no timeouts.
- **Budget rule.** After K the fixed formula projected
  K + L + S = 0.61 + 8 × 0.613 + 32 × 0.0825 = $8.15, so S ran.
- **Deviations.** None to the registered rules or code. Two notes:
  - `longitudinal.py` exited 1 on block L. Every session passed; the exit
    came from the reset detector (next item). The registration says the
    file is analyzed as is, and it was.
  - **The reset detector undercounts.** Toolplane shows reset verified on
    6/8 sessions. In the other two (`toolplane-rep6`, `toolplane-rep8`)
    the transcripts show `return await reset_session()` in its own
    `execute_code` call, then a full refetch, and turn 6 was answered
    correctly. The harness's `_is_dedicated_reset_code` only accepts the
    bare statement `await reset_session()`, so the `return` form reads as
    no reset. By hand it is 8/8. This figure is reported, not tested, and
    no verdict reads it.

## Verdicts

| id | claim | verdict | point (95% CI) |
|---|---|---|---|
| P1 | sessions keep context smaller (peak, turns 1–5) | HOLDS | −16.7K tokens [−17.5K, −16.3K]; 27.5K vs 44.2K |
| P2 | a session is cheaper overall (turns 1–5) | HOLDS | −$0.078 [−0.082, −0.073]; $0.117 vs $0.195 |
| P3 | reuse turns cost about the same as direct (±10%) | **REVERSED** | +$0.0086 [+0.0077, +0.0102]; $0.033 vs $0.0245, +35% |
| P4 | a live session reuses more cheaply than sessions-off | HOLDS | −$0.0051 [−0.0062, −0.0034]; $0.033 vs $0.038 |
| P5 | …over the whole session (turns 1–5) | HOLDS | −$0.0084 [−0.0119, −0.0032]; $0.117 vs $0.125 |
| S1 | `single` costs more on toolplane (Sonnet 5.5) | UNRESOLVED | −$0.0026 [−0.0133, +0.0030] |
| S2 | `chain_prose` costs more on toolplane (Sonnet 5.5) | HOLDS | +$0.0030 [+0.0013, +0.0049]; about +3% |

Every arm passed 8/8 sessions, so no verdict is gated. The per-arm table
is in the analysis output.

## Readings

### P2 holds; the saving is all in turn 1

Toolplane's session is 40% cheaper over turns 1–5, but the whole gap is
the load turn: turn 1 median $0.085 against direct's $0.170. Direct's
turn 1 brings all 30 orders (60 KB of filler) into context; toolplane
keeps them in the sandbox and returns totals. On turns 2–5 direct is the
cheaper arm (P3). The claim "a session is cheaper overall" holds for this
shape, five turns over one load; it is front-loaded, as the registration
said.

### P3 is reversed: a reuse turn costs one extra request

On a reuse turn direct answers from the orders already in its context:
one model request, no tool call, about $0.0056. Toolplane answers from
the live session: an `execute_code` call and then the answer, two
requests, about $0.0080. Toolplane's context is smaller (27K against
44K), but the second request re-reads it, and that costs more than the
extra 17K tokens direct carries once. Over turns 2–5 that is $0.033
against $0.0245, +35%, far outside the ±10% margin. All 32 reuse turns
in each arm were correct.

July's Sonnet 5 sessions had the two arms level ($0.092 against $0.094).
Direct's reuse turns are about 4× cheaper here than in July; this run
can't say why (model, client version and filler all changed).

What this does not show: the break-even. Direct's per-turn reuse cost
grows with what sits in its context, and toolplane's doesn't. At 60 KB
of loaded data and four reuse turns, the live session's one-time saving
on the load ($0.085) dwarfs its per-turn surcharge ($0.0024); it would
take about 35 reuse turns to give it back, and this fixture can't test
that.

### P4 and P5 hold, narrowly; the result store was never used

The sessions-off arm advertises `save_result`/`load_result`, and Sonnet
5.5 never used them: 32 of 32 reuse turns refetched all 30 orders inside
`execute_code` (31 fixture calls each). The refetch stays in the sandbox,
so it costs little: $0.0013 a turn more than the live session. So P4/P5
compare a live session against in-sandbox refetching, not against the
result store. The live session saves $0.008 per five-turn session (7%).
It would save more where the refetch is slow or costly, which this
fixture's local server isn't.

The session also pays off inside turn 1. Both toolplane arms typically
fetch all 30 orders in one `execute_code` call to inspect their shape,
then compute in a second. With the session on, the second call reuses
the fetched list; with sessions off it is gone, and the arm fetches all
30 again (62 fixture calls median, against 31.5).

### S1 is unresolved on one cold-cache run

The registered statistic is mean cost per pass. Seven of direct's eight
`single` runs cost $0.0711 and all eight of toolplane's cost $0.0732 or
$0.0768, so in every comparable pair toolplane is about 3% dearer. The
eighth direct run, the first run of block S, cost $0.1123: it paid
cache creation that every later run read. That one run pulls direct's
mean above toolplane's, and the interval crosses 0. The registered
verdict is UNRESOLVED and stays so. Read with the confirmation matrix's
Haiku H1a (+11%, HOLDS), the small-task loss looks real but small on
Sonnet 5.5; this run alone doesn't establish it.

### S2 holds at about 3%

Toolplane costs $0.0981 per pass on `chain_prose` against $0.0950, with
every run correct. The stepwise-chain loss holds on Sonnet 5.5, and is
smaller than Haiku 5.5's +12% (the confirmation matrix's H5).

## What changes in the claims

- "Sessions keep context smaller" and "a session is cheaper overall":
  confirmed on Sonnet 5.5 with intervals, for a load-then-reuse
  workload.
- "Reuse turns cost about the same": **wrong** on this setup. Each reuse
  turn costs one extra request, about a third more than answering from
  context. The overall saving comes from the load, not from reuse.
- "A live session beats sessions-off": true but small (7%), and against
  in-sandbox refetching, because the model never reached for the result
  store.
- The small-task and chain losses: chain holds at about 3%; single is
  unresolved by one cold-cache run.

## Files

- `results/longitudinal-20261010-191310.json` (L) and
  `results/run-20261010-192444.json` (S), with transcripts.
- `results/longitudinal-20261010-191117.json` (K, calibration, in no
  cell), with transcripts.
- `results/prereg-persistence-2026-10-analysis.md`, the registered
  output.
