# Confirmation matrix: results (2026-10-10)

The registration is in `PREREG-confirmation-2026-10.md`, as amended by A1.
The registered analysis output is
`results/prereg-confirmation-2026-10-analysis.md`, written by:

```bash
.venv/bin/python bench/analyze_prereg.py \
  bench/results/run-20261010-115818.json bench/results/run-20261010-121022.json \
  bench/results/run-20261010-135716.json bench/results/run-20261010-140115.json \
  bench/results/run-20261010-140904.json bench/results/run-20261010-141230.json \
  bench/results/run-20261010-141956.json bench/results/run-20261010-142344.json
```

This doc reads those verdicts. It doesn't change any of them.

## Provenance

- **Rows.** 240 runs across 8 blocks: A, B1, B2, C, D, E, F and S. All
  are `git_dirty=false`, with code under test at `d38c04c` (the A1 merge)
  and wheel `c62640e59c9f`.
- **Client.** Claude Code 2.1.296 on every row, with auto-update off. The
  3 rows with no version are timeouts, which carry no version by A1
  design. The analysis did not VOID the run.
- **Spend.** The matrix rows record $4.81, against the $4.6 projection.
  That excludes the 3 timeout runs, whose cost the client never reported.
  The 5 runs in that cell that finished cost $0.09–$0.66 each. Per block:
  A $0.38, B1 $1.49, B2 $1.70, C $0.21, D $0.10, E $0.17, F $0.10,
  S $0.66. B1 ran over because the 2 KB direct cell collapsed (see H3).
  Calibration added $0.73 that was recorded ($0.42 in runs, $0.30 in the
  longitudinal session), plus two calibration timeouts of unknown cost.
  Total recorded spend is $5.53. If each timeout cost as much as the
  costliest finished run in its cell ($0.66), the total would be about
  $7.52, still under the $8 cap.
- **Models.** Haiku 5.5 for A–F; Sonnet 5.5 (`claude-sonnet-5-5`) for S.
- **Analysis changes after registration.** The registering commit was
  `629d7ec`. Four review commits followed before the merge (`a8538a9`,
  `5541a25`, `b98731a`, `d864390`) and were never logged as amendments.
  They tightened the rules: a timeout is priced at the cell maximum
  instead of free; more than 5% undefined draws means UNRESOLVED; a scale
  cell with no passing run leaves the slope undefined; wall time counts
  every run. All four landed before any data (calibration ran at the
  merge, `c0b98b6`). The independent recompute checked each against this
  dataset, and none flips a verdict. After the data, one display-only
  change: `_fmt` printed infinities with no sign and now prints `-inf` or
  `+inf`. No verdict changes.

## Verdicts

| id | claim | verdict | note |
|---|---|---|---|
| H1a | single favors direct | HOLDS | +$0.0005/pass (+11%) |
| H1b | N=5 favors direct | HOLDS | +$0.0006 (+13%) |
| H2a | N=100 favors toolplane | HOLDS | $0.0058 vs $0.0116, half the cost |
| H2b | toolplane flatter in N | HOLDS | slope diff −6.8e-5 $/record |
| H2c | direct grows with N | HOLDS | +7.3e-5 $/record |
| H3 0 B | fetch-one favors toolplane | HOLDS | −$0.0012 |
| H3 2 KB | fetch-one favors toolplane | HOLDS, **by a correctness collapse** | direct 0/8 (see below) |
| H3 20 KB | fetch-one favors toolplane | HOLDS | $0.0079 vs $0.3998, 50x cheaper; n=4 |
| H3 growth | advantage grows with payload | HOLDS | 20 KB vs 0 B only; 2 KB doesn't fit a monotonic story |
| H4 0/2 KB/20 KB | bulk favors direct | HOLDS ×3 | the 20 KB lower bound rounds to +0.0000 (n=4) |
| H5 | adaptive chains favor direct | HOLDS ×2 | +12% on both fixtures |
| H5-walk | no shortcut | HOLDS 8/8 ×4 | every arm walked hop by hop |
| H6a | fan-out beats native on wall time | UNRESOLVED | −1.45 s median, CI [−3.6, +1.35] |
| H6b | …without costing more | HOLDS | toolplane is in fact cheaper, −$0.0006 |
| H7a | `toolplane_cli` beats direct on refunds | UNRESOLVED, **point against** | +$0.0008 (+12%); CI [−0.00003, +0.0016] |
| H7b | with a shell, toolplane is costlier | HOLDS, **by a correctness collapse** | toolplane 2/8 (see below) |
| H8 | no schema tax (direct M=15 vs M=1) | REVERSED, **inside the margin** | +3% (see below) |
| S1 | Sonnet 5.5 single favors direct | HOLDS, **descriptive only** | n=2 (see below) |
| S2 | Sonnet 5.5 chain_prose favors direct | HOLDS, **descriptive only** | n=2 (see below) |
| O3, H9a/b, O2a/b | — | DROPPED (A1) | — |

Summary: of 22 tested rows (H5-walk counted once), 19 HOLD, 2 are UNRESOLVED and 1 is
REVERSED. Three of the HOLDs and the one REVERSED need the readings below
before anyone cites them.

## Readings the table can't carry

### H3 at 2 KB: Haiku degeneration, cause not isolated

All 8 direct runs:

- fetched all 30 orders;
- then broke into gibberish at the step where they had to combine the
  results. The 5 runs that finished produced 60K–259K output tokens
  (median 131K); the timeouts report none;
- ended in one of four ways: 3 timeouts at 900 s, 3 with no answer, 1
  (rep 4) with a gibberish answer, and 1 (rep 3) with a Haiku 5.5
  usage-policy refusal (`[bio]`, `req_011CftWazCmZ4cgcxRMaRztA`).

The 20 KB direct runs saw the same filler, ten times larger, made the
same 30 fetches, and passed 4/4. That rules out a simple "more payload,
more failure" story. Beyond that, the cause is not isolated. One
hypothesis is the filler itself, one token repeated (`ord0002-ord0002-…`),
but the 20 KB runs survived that same filler, so the hypothesis doesn't
explain why 2 KB fails and 20 KB doesn't. The degeneration looks like the
one in the Haiku longitudinal calibration that led A1 to drop G.

The verdict HOLDS under the registered rule, because failures are priced
into cost-of-pass. But it says nothing about token economics at 2 KB.
**For the follow-up:** use varied filler text, since real payloads aren't
one repeated token. That removes one candidate cause, though it may not
be the right one.

### H7b: with a shell, toolplane runs mostly never searched

Both arms opened shell-first: every run, direct and toolplane, started
with 1–5 `git log` calls through Bash. They differ in what came next:

- **Direct:** all 8 runs then called ToolSearch, found `get_order`, and
  passed.
- **Toolplane, 2 passing runs:** called ToolSearch, then `execute_code`.
- **Toolplane, 6 failing runs:** never called ToolSearch. They concluded
  the amounts had to come from some source they couldn't reach, and
  answered with no number.

So H7b's mechanism is a skipped search (direct searched 8/8, toolplane
2/8), not a higher token cost. A failed toolplane run costs about what a
direct run does ($0.0068 against $0.0064 mean). The client-side ranking
finding from #125/#127 doesn't apply, because ranking only acts once a
search happens. Why the toolplane runs stopped short of searching is not
established.

### H7a: the Sonnet result didn't replicate on Haiku

With no shell, `toolplane_cli` passed 8/8. But it cost 12% more than
direct. The Sonnet 5.5 #113 ratio was 0.94, which favored toolplane; on
Haiku it is 1.12. The CI crosses 0 (lower bound −$0.00003), so the
verdict is UNRESOLVED. Don't read it as neutral: the point estimate leans
against the prediction.

One cost isn't from the join itself. Each of the 8 runs tried Bash once,
and the client denied it ("No such tool available"). The restriction
held, but those attempts are in the measured cost, and they weren't
separated out.

### H8: REVERSED by a rule that overlaps itself

- **The result.** Direct at M=15 costs $0.0068 per pass, against $0.0066
  at M=1. That is +$0.00020, 95% CI [+0.000025, +0.00034], about +3%.
- **The margin.** ±10% of the M=1 reference is ±$0.00066. The whole CI
  lies inside it, which meets the HOLDS clause. It also excludes 0, which
  meets the REVERSED clause.
- **The order of checks.** The registered code checks REVERSED first, so
  the verdict is REVERSED.

The text never said which clause wins. The plain reading: there is a
detectable schema tax at M=15, and it is small (+3%), well inside the
band the registration called negligible.

This doc doesn't reinterpret the verdict: it stays REVERSED. **As a rule
for future registrations only:** check the margin first, as standard
equivalence testing (TOST) does, so that a precise, small effect isn't
reported as a reversal.

Toolplane's own M=15 effect is visible in the cells table ($0.0054 →
$0.0062, +14%). H8 doesn't test it.

### S1/S2: two runs per arm prove little

A1 cut the Sonnet 5.5 slice to 2 runs per arm. With 2 runs, the
bootstrap can only resample the two observed values, so its "95% CI"
spans the observed runs and is not a real interval. "HOLDS" then means
only that both toolplane runs cost more than both direct runs, which is
easy to get by chance. Read S1/S2 as a quick look in the same direction
as Haiku, not as evidence that the findings hold across models. Topping
the slice up now would be an unregistered change made after seeing the
data, so it waits for the Sonnet 5.5 follow-up, with at least 8 runs per
arm.

### H2: "flat" was too strong

Toolplane's slope is 4.8e-6 $/record, with CI [4e-7, 9.6e-6]. That is
15x shallower than direct's, but the CI doesn't include 0. The amended
registration reports this slope rather than testing it, so it gets no
verdict. Even so, "toolplane is flat in N" overstates it; "about 15x
flatter" is what the data shows.

## What this means for the paper

These claims now rest on registered, clean, n=8 cells:

- **The small-task loss** (H1).
- **The large-N win** (H2a): it is real by N=100.
- **Payload under fetch-one** (H3 at 0 B and 20 KB), and **the bulk
  reversal** (H4).
- **The stepwise-chain loss** (H5).

All of these are on Haiku 5.5. Sonnet 5.5 (S1/S2) points the same way,
but at 2 runs per arm it is not evidence across models.

Treat the following as open or reframed:

- **Wall-time concurrency** (H6a) is unresolved.
- **The CLI + MCP join** does not show a cost win on Haiku (H7a).
- **"No schema tax"** should become "a small (+3%) schema tax at M=15"
  (H8).
- **The 2 KB point** is a Haiku collapse with an unisolated cause, not a
  cost measurement (H3).

Persistence (H9) is still untested. It waits on the Sonnet 5.5 follow-up
registration.
