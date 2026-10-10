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
- **Spend.** $4.81 against the $8 cap and the $4.6 projection. B1 came in
  over its projection ($1.49 against $0.95) because the 2 KB direct cell
  collapsed (see H3).
- **Models.** Haiku 5.5 for A–F; Sonnet 5.5 (`claude-sonnet-5-5`) for S.
- **Analysis change.** One change after registration, display only:
  `_fmt` used to print `inf` with no sign. It now prints `-inf` or
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
| H5 | adaptive chains favor direct | HOLDS ×2 | +11–12% on both fixtures |
| H5-walk | no shortcut | HOLDS 8/8 ×4 | every arm walked hop by hop |
| H6a | fan-out beats native on wall time | UNRESOLVED | −1.45 s median, CI [−3.6, +1.35] |
| H6b | …without costing more | HOLDS | toolplane is in fact cheaper, −$0.0006 |
| H7a | `toolplane_cli` beats direct on refunds | UNRESOLVED, **point against** | +$0.0008 (+12%); CI [−0.0000, +0.0016] |
| H7b | with a shell, toolplane is costlier | HOLDS, **by a correctness collapse** | toolplane 2/8 (see below) |
| H8 | no schema tax (direct M=15 vs M=1) | REVERSED, **inside the margin** | +3% (see below) |
| S1 | Sonnet 5.5 single favors direct | HOLDS | n=2 |
| S2 | Sonnet 5.5 chain_prose favors direct | HOLDS | n=2 |
| O3, H9a/b, O2a/b | — | DROPPED (A1) | — |

Summary: of 22 tested rows, 19 HOLD, 2 are UNRESOLVED and 1 is
REVERSED. Three of the HOLDs and the one REVERSED need the readings below
before anyone cites them.

## Readings the table can't carry

### H3 at 2 KB: Haiku degeneration, not a payload cost

All 8 direct runs:

- fetched all 30 orders;
- then broke into gibberish at the step where they had to combine the
  results (about 130K output tokens per run);
- ended in one of three ways: 3 timeouts at 900 s, 4 with no answer, and
  1 (rep 3) ending in a Haiku 5.5 usage-policy refusal (`[bio]`,
  `req_011CftWazCmZ4cgcxRMaRztA`).

The 20 KB direct runs saw the same filler, ten times larger, made the
same 30 fetches, and passed 4/4. So what tripped Haiku is not the payload
size. The likely trigger is the fixture: the filler is one token repeated
(`ord0002-ord0002-…`). The cause is not isolated. This is the same
failure seen in the Haiku longitudinal calibration that led A1 to drop G.

The verdict HOLDS under the registered rule, because failures are priced
into cost-of-pass. But it says nothing about token economics at 2 KB.
**Fixture weakness for the follow-up:** real payloads aren't one repeated
token. The filler should be varied text.

### H7b: the shell hid the MCP tools

In all 6 failed toolplane runs, the agent went straight to
`git log` through Bash. It never called ToolSearch, and concluded that
"no refund amounts exist anywhere". The 2 passing runs took the path
ToolSearch → `execute_code`. Direct passed 8/8, because its `get_order`
tool is visible without a search.

So H7b's mechanism is a skipped discovery, not a higher token cost. A
failed toolplane run costs about the same as a direct one ($0.0065). This
fits the client-side ranking finding from #125/#127.

### H7a: the Sonnet result didn't replicate on Haiku

With no shell, `toolplane_cli` passed 8/8. But it cost 12% more than
direct. The Sonnet 5.5 #113 ratio was 0.94, which favored toolplane; on
Haiku it is 1.12. The CI touches 0, so the verdict is UNRESOLVED. Don't
read it as neutral: the point estimate leans against the prediction.

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

The registration didn't fix the ±10% equivalence band any tighter. Future
registrations should check the margin first, as standard equivalence
testing (TOST) does. That way a precise, small effect isn't reported as a
reversal.

Toolplane's own M=15 effect is visible in the cells table ($0.0054 →
$0.0062, +15%). H8 doesn't test it.

### H2: "flat" was too strong

Toolplane's slope is 4.8e-6 $/record, with CI [4e-7, 9.6e-6]. That is
15x shallower than direct's, but the CI doesn't include 0. The amended
registration reports this slope rather than testing it, so it gets no
verdict. Even so, "toolplane is flat in N" overstates it; "about 15x
flatter" is what the data shows.

## What this means for the paper

These claims now rest on registered, clean, n=8 cells:

- **The small-task loss** (H1, S1).
- **The large-N win** (H2a): it is real by N=100.
- **Payload under fetch-one** (H3 at 0 B and 20 KB), and **the bulk
  reversal** (H4).
- **The stepwise-chain loss** (H5, S2).

Treat the following as open or reframed:

- **Wall-time concurrency** (H6a) is unresolved.
- **The CLI + MCP join** does not show a cost win on Haiku (H7a).
- **"No schema tax"** should become "a small (+3%) schema tax at M=15"
  (H8).
- **The 2 KB point** is a fixture artifact (H3).

Persistence (H9) is still untested. It waits on the Sonnet 5.5 follow-up
registration.
