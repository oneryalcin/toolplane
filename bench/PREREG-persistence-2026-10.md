# Pre-registered persistence follow-up (October 2026)

**Status: registered, not yet run.** The commit that adds this file is
the registration. The hypotheses, the cells and sessions they read, the
decision rule and the analysis (`bench/analyze_persistence.py`, committed
with this file) must not change after that commit. **Every** later change
is logged under [Amendments](#amendments) with its date, commit and
reason, including review fixes made before any data. The write-up reports
each one as a deviation. (The confirmation matrix's review commits were
never logged; this rule closes that gap.)

Purpose: this picks up what the confirmation matrix
(`PREREG-confirmation-2026-10.md`, results in
`RESULTS-confirmation-2026-10.md`) left out or could not decide:

1. **Persistence.** Its sessions block (H9, O2) was dropped in amendment
   A1 after Haiku 5.5 collapsed on the longitudinal fixture.
2. **The Sonnet 5.5 slice.** S1/S2 ran at 2 runs per arm. That is too
   few for a real bootstrap interval, so the results doc reports them as
   descriptive only.

## Frozen conditions

- **Model.** `claude-sonnet-5-5` for everything. Rows record
  `requested_model`; the analysis is VOID on any other model.
- **Client.** Claude Code at whatever version is current when the run
  starts. Every `claude` subprocess runs with `DISABLE_AUTOUPDATER=1`, and
  every row records its own `client_version`. The analysis is VOID if the
  rows span more than one version. A missing version (a died session or
  a timeout) does not count as a second version.
- **Code under test.** Toolplane at one commit on `main` that contains
  this file, served from the harness's frozen wheel. Every row must
  record `git_dirty=false`. Every input row must also carry the same
  `git_sha`, `wheel_sha256`, `fixtures_sha256` and `harness_sha256`, and
  every session the same `longitudinal_harness_sha256`. The analysis is
  VOID otherwise.
- **Fixture.** The longitudinal harness (`bench/longitudinal.py`):
  - six turns over 30 orders of 2,000 bytes each;
  - turn 1 loads and totals the orders, turns 2–5 reuse them, and turn 6
    is the reset;
  - built-ins restricted as the harness already does: no Bash, files,
    web or subagents;
  - **filler `varied`**: seeded word text instead of the repeated
    `ordNNNN-` token. Haiku degenerated on the repeated token, and real
    payloads aren't one repeated token. The analysis is VOID on any
    session with another filler.
- **Runs.** Exactly 8 per arm (sessions, or slice runs per task) for
  every hypothesis that gets a verdict. Any other count reads
  **UNRESOLVED (incomplete)**. Arm order is counterbalanced by rep
  parity.
- **No edits while a block runs.** The working tree stays untouched for
  the duration.

## Arms

| arm | what it is |
|---|---|
| `direct` | the orders MCP server, called natively |
| `toolplane` | the Toolplane facade, Monty session on (the default) |
| `toolplane_nosession` | the same facade with `[session] enabled = false`. The **result store stays on**: `save_result`/`load_result` is advertised and works across turns, as in the shipped sessions-off setup |

## Decision rule

The bootstrap is the confirmation matrix's (`analyze_prereg.bootstrap`):
95% percentile CI, 4,000 resamples, seed 0, each arm resampled
independently. Undefined draws are handled as there: if more than 5% of
draws are undefined, the verdict is UNRESOLVED.

- **Directional predictions (<0, >0).**
  - **HOLDS** if the CI excludes 0 in the predicted direction.
  - **REVERSED** if it excludes 0 in the other direction.
  - **UNRESOLVED** otherwise.
- **Equivalence predictions (~0): the margin is checked first.**
  - **HOLDS** if the whole CI lies within ±10% of the reference arm's
    median. This holds even when the CI also excludes 0 (two one-sided
    tests).
  - Otherwise **REVERSED** if the CI excludes 0.
  - Otherwise **UNRESOLVED**.

  This is the one rule that differs from the confirmation matrix, which
  checked "excludes 0" first and so reported a +3% effect inside its
  margin as REVERSED (its H8).
- **Session pass gate.** A session passes if:
  - it did not die (no harness error, all 6 turns recorded);
  - the client exited 0;
  - it answered turns 1–5 correctly.

  A hypothesis that reads an arm with fewer than 7/8 passing sessions is
  **UNRESOLVED (gate)**. Without the gate, an arm could look cheap by
  failing.
- **Died sessions.** A session that died (a turn timeout or client exit)
  has no turn data. It counts against the gate. Its statistic is imputed
  twice, each time at an extreme of its arm's observed values:
  - once pushing the difference down (the first arm's deaths at its
    minimum, the second arm's at its maximum);
  - once pushing it up.

  The verdict stands only if both imputations agree. Otherwise it is
  **UNRESOLVED (depends on died sessions)**, so a death can never decide
  a verdict. Every session that didn't die, correct or not, enters the
  statistics as observed.
- **Slice runs** (S1, S2) use the confirmation matrix's cost per correct
  answer and its timeout imputation (a timeout is priced at its cell's
  highest observed cost).

## Hypotheses

The session statistics are per-arm medians, differenced. The July
column is the Sonnet 5 result from `longitudinal-20260713-095957` (4
runs per arm, no CI). It shows what motivated each prediction; it is not
part of this test.

| id | statistic | predicted | July (Sonnet 5) | claim it backs |
|---|---|---|---|---|
| P1 | peak request context over turns 1–5, tp − direct | <0 | 33.5K vs 70.4K tokens | sessions keep context smaller |
| P2 | cost of turns 1–5, tp − direct | <0 | $0.21 vs $0.38 | a session is cheaper overall, front-loaded |
| P3 | cost of reuse turns 2–5, tp − direct | ~0 | $0.092 vs $0.094 | reuse turns cost about the same |
| P4 | cost of reuse turns 2–5, tp − nosession | <0 | not measured | a live session reuses data more cheaply than the result store |
| P5 | cost of turns 1–5, tp − nosession | <0 | not measured | …over the whole session |

**What P4/P5 compare.** A live Monty session against Toolplane with
sessions off but the result store on. That is the shipped alternative,
not "persistence against no memory". A tie would mean the session adds
little *over the result store*, not that persistence is worthless. The
sessions-off arm can also refetch, or keep data in conversation. Each
turn's `reuse_mechanism` (below) shows which it did, and is reported, not
tested. A no-memory arm (result store off too) is out of scope.
| S1 | `single`, cost per pass, tp − direct (8 runs per arm) | >0 | — | the small-task loss holds on Sonnet 5.5 |
| S2 | `chain_prose`, cost per pass, tp − direct (8 runs per arm) | >0 | — | the stepwise-chain loss holds on Sonnet 5.5 |

### Reported but not tested

- Per arm:
  - sessions passing turns 1–5, and errors;
  - median turns 1–5 cost and peak context;
  - reset verified, i.e. toolplane ran `reset_session()` in its own call
    and then refetched;
  - compaction events.
- Each reuse turn's data source (`reuse_mechanism`): refetch, result
  store, or retained in context or the live session. No prediction is
  made for the sessions-off arm.
- Turn 6 (reset) is not compared across arms. It is a different
  operation in each.

## Blocks

Every command runs from the registration's merge commit, with
`env -u ANTHROPIC_API_KEY UV_NO_CONFIG=1`.

| block | command | runs |
|---|---|---|
| K (calibration, in no cell) | `.venv/bin/python bench/longitudinal.py --model claude-sonnet-5-5 --arms direct,toolplane,toolplane_nosession --reps 1 --filler varied` | 3 sessions |
| L | `.venv/bin/python bench/longitudinal.py --model claude-sonnet-5-5 --arms direct,toolplane,toolplane_nosession --reps 8 --filler varied` | 24 sessions |
| S | `.venv/bin/python bench/run.py --model claude-sonnet-5-5 --tasks single,chain_prose --reps 8` | 32 runs |

`longitudinal.py` exits 1 when any session failed or died. It still
writes the complete results file first, and that file is analyzed as is.
A died session's row records its error and its partial transcript.

**Analysis** (K's file is not an input):

```
.venv/bin/python bench/analyze_persistence.py --sessions <L's longitudinal-*.json> --runs <S's run-*.json>
```

## Budget

- **Cap: $10**, including calibration.
- **Projection: about $8.** The persistence arms have never run on
  Sonnet 5.5, so this is scaled from July's Sonnet 5 sessions by the
  Sonnet 5.5 / Sonnet 5 cost ratio on `single` (about 0.5). Expect
  sessions near $0.22 (direct), $0.14 (toolplane) and $0.18 (nosession).
  - Calibration K: about $0.55.
  - L: about $4.3.
  - S: about $2.9.
- **Rules.**
  1. Run K first. If any K session fails the pass gate, stop: the
     fixture doesn't work on this model, and any change is an amendment.
  2. Re-project with fixed formulas:
     - L = 8 × (the sum of K's three session costs);
     - S = 32 × $0.0825, the mean Sonnet 5.5 cost per run of the
       confirmation matrix's S block ($0.66 over 8 runs of the same two
       tasks).
  3. If K + L + S exceeds $10, drop S, which this file names as the first
     reduction. Never cut L below 8 per arm. If K + L alone exceeds $10,
     stop and amend.
  4. After each block, record the cumulative spend. Stop before any block
     that would cross the cap.

## Exclusions

- Rows with `git_dirty=true` are void.
- No other exclusion is allowed. Every session and run counts, including
  failures, timeouts and died sessions (see the pass gate).

## Amendments

None yet.
