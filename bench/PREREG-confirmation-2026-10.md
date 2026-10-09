# Pre-registered confirmation matrix (October 2026)

**Status: registered, not yet run.** The commit that adds this file is
the registration. The hypotheses, the cells they read, the decision rule
and the analysis (`bench/analyze_prereg.py`, committed with this file)
must not change after it. Any later change is logged under
[Amendments](#amendments) with its date and reason, and the write-up
reports it as a deviation.

Purpose: the paper draft's evidence was gathered in stages over July, at
2–4 runs per cell, on Monty 0.0.18/0.0.19 and Sonnet 5. This matrix
retests its claims in one frozen design on today's stack, with enough
runs per cell for confidence intervals.

## Frozen conditions

- **Model.** `claude-haiku-5-5` for every block except S, which uses
  `claude-sonnet-5-5`.
- **Client.** Claude Code, current version, recorded per row.
- **Code under test.** Toolplane at one commit on `main` that contains
  this file, served from the harness's frozen wheel. Every row must
  record `git_dirty=false`; the analysis refuses (VOID) otherwise.
- **Defaults.** One MCP server (M=1) unless the block says otherwise.
  Default client built-ins (the production setting, with Bash use
  recorded per row), except where a block restricts them.
- **Runs.** 8 per arm per cell, unless the budget rule below reduces a
  cell. Arm order is counterbalanced by run parity.
- **Failures count.** A timeout, wrong answer or abstention (an answer
  given with zero tool calls) is a failed run, priced in through cost
  per correct answer.
  - A timeout's cost is unknown, because the client never reports it.
    It is imputed as the highest observed cost in its cell, so a
    timeout can only make its arm look worse, and the report lists
    every imputation.
  - Rows are matched to cells by `requested_model`, so a timeout row
    (`model=None`) stays in its cell.
- **No edits while a block runs.** The working tree stays untouched for
  the duration.

## Decision rule

The analysis script implements this rule exactly.

- **Statistic.** The 95% percentile bootstrap CI: 4,000 resamples,
  seed 0, each arm resampled independently. For cost hypotheses the
  statistic is the difference in cost per correct answer.
- **Directional predictions (<0 or >0).**
  - **HOLDS** if the CI excludes 0 in the predicted direction.
  - **REVERSED** if it excludes 0 in the other direction.
  - **UNRESOLVED** otherwise.
- **Undefined draws.** A resample in which an arm has no passing run
  prices that arm at infinity. A draw where both arms are infinite is
  undefined and is discarded. If more than 5% of draws are undefined,
  or the point estimate is, the verdict is **UNRESOLVED**.
- **H5-walk** holds when at least 7/8 of an arm's runs walk one order
  per call. That means:
  - direct: at least 5 `get_order` calls;
  - toolplane: at least 5 `execute_code` calls.

  Otherwise it **FAILS**, and H5's cost verdict for that fixture is
  reported as describing a shortcut, not a stepwise walk.
- **Similarity predictions (~0)** are equivalence tests.
  - **HOLDS** only if the whole CI lies within ±10% of the reference
    arm's value.
  - **REVERSED** if the CI excludes 0.
  - **UNRESOLVED** otherwise, so a noisy cell cannot pass by being
    wide.

## Hypotheses

"tp" is the toolplane arm. All differences are tp − direct unless stated.

| id | cell(s) | statistic | prediction | paper claim it tests |
|---|---|---|---|---|
| H1a | `single` | cost/pass diff | >0 | small tasks favor direct |
| H1b | `loop5` | cost/pass diff | >0 | small tasks favor direct |
| H2a | `loop100` | cost/pass diff | <0 | large fine-grained tasks favor tp |
| H2b | `loop5`, `loop20`, `loop` (N=30 from block B's 0-byte fetch-one cell), `loop100` | slope of cost/pass on N, tp − direct | <0 | tp's cost is flatter than direct's |
| H2c | as H2b, direct only | slope | >0 | direct's cost grows with N |
| H3 | `loop` fetch-one at 0, 2,000, 20,000 bytes | cost/pass diff | <0 at each | fetch-one favors tp |
| H3-growth | fetch-one, 20,000 vs 0 bytes | (diff at 20 KB) − (diff at 0) | <0 | tp's advantage grows with payload |
| H4 | `loop` bulk at 0, 2,000, 20,000 bytes | cost/pass diff | >0 at each | a bulk endpoint favors direct |
| H5 | `chain`, `chain_prose` | cost/pass diff | >0 | adaptive chains favor direct |
| H5-walk | same | runs walking one order per call | ≥7/8 per arm | neither arm shortcuts |
| H6a | `loop_lat100` | median wall diff (s) | <0 | taught fan-out beats native batching on wall time |
| H6b | `loop_lat100` | cost/pass diff | not >0 (holds unless the CI lies wholly above 0) | …without costing more |
| H7a | `refunds`: `toolplane_cli` (no shell) vs direct | cost/pass diff | <0 | CLI + MCP join without a shell favors tp |
| H7b | `refunds`: tp (shell available) vs direct | cost/pass diff | >0 | with a shell, agents bypass the binding |
| H8 | `loop` direct at M=15 vs M=1 | cost/pass diff | ~0 | deferred loading removes the schema tax |
| H9a | longitudinal, turns 1–5 | median peak-context diff | <0 | sessions keep context smaller |
| H9b | longitudinal, turns 2–5 | median reuse-cost diff | ~0 | reuse turns cost about the same |
| O2a | longitudinal, turns 2–5: tp vs `toolplane_nosession` | median reuse-cost diff | <0 | persistence itself saves money on reuse |
| O2b | longitudinal, turns 1–5: tp vs `toolplane_nosession` | median cost diff | <0 | …over the whole session |
| O3 | `loop` fetch-one 20,000 bytes, `--restrict-builtins` | cost/pass diff | <0 | the payload win does not depend on Bash |
| S1 | Sonnet 5.5 `single` | cost/pass diff | >0 | model-independence of H1a |
| S2 | Sonnet 5.5 `chain_prose` | cost/pass diff | >0 | model-independence of H5 |

### Reported but not tested

- toolplane's own cost slope.
- For O2, the data source of each reuse turn (`reuse_mechanism`):
  - refetch through the fixture;
  - the result store (`load_result`);
  - data retained in context or in the live session.

  O2 has no prediction for which source the sessions-off arm uses.
- Per-cell token classes: output, uncached input, cache read, peak
  context. These are the cost model's terms, reported for every cell.
- Abstention counts per cell.

## Blocks and commands

Prefix every run.py command with
`env -u ANTHROPIC_API_KEY UV_NO_CONFIG=1 .venv/bin/python bench/run.py`.

| block | arguments | runs |
|---|---|---|
| A | `--model claude-haiku-5-5 --tasks single,loop5,loop20,loop100 --reps 8` | 64 |
| B1 | `--model claude-haiku-5-5 --tasks loop --record-bytes 0,2000 --granularity fetch-one,bulk --reps 8` | 64 |
| B2 | `--model claude-haiku-5-5 --tasks loop --record-bytes 20000 --granularity fetch-one,bulk --reps 8` | 32 |
| C | `--model claude-haiku-5-5 --tasks chain,chain_prose --reps 8` | 32 |
| D | `--model claude-haiku-5-5 --tasks loop_lat100 --reps 8` | 16 |
| E | `--model claude-haiku-5-5 --tasks refunds --arms direct,toolplane,toolplane_cli --reps 8` | 24 |
| F | `--model claude-haiku-5-5 --tasks loop --servers 15 --reps 8` | 16 |
| O3 | `--model claude-haiku-5-5 --tasks loop --record-bytes 20000 --restrict-builtins --reps 8` | 16 |
| G | `env -u ANTHROPIC_API_KEY UV_NO_CONFIG=1 .venv/bin/python bench/longitudinal.py --model claude-haiku-5-5 --arms direct,toolplane,toolplane_nosession --reps 8` | 24 sessions |
| S | `--model claude-sonnet-5-5 --tasks single,chain_prose --reps 4` | 16 |

Analysis:

```
.venv/bin/python bench/analyze_prereg.py <every run-*.json from A–F, O3, S> --longitudinal <G's longitudinal-*.json>
```

## Budget rule (fixed now)

The target is ≤ $5 in total, with a hard cap of $8.

1. **Calibration.** Before the matrix, run one calibration pass. It
   belongs to no cell, its rows are committed, and it is disclosed.
   - It covers only cells whose Haiku cost cannot be estimated from
     existing data:
     - B2's four 20 KB cells, at 1 run per arm;
     - `loop100` direct, at 1 run;
     - one longitudinal session per G arm.
   - No other cell is pre-run. Existing tasks already have Haiku
     evidence (#150), and the sessions-off arm's first real use is G
     itself.
   - Calibration may change only run counts, never hypotheses or cells.
2. **Projection.** Project the total as: (calibrated or known per-run
   cost) × planned runs. Known per-run costs:
   - Haiku light cells, about $0.006 (#150);
   - Sonnet 5.5 light cells, about $0.10 (this week's runs).
3. **Reductions, in this fixed order**, until the projection is ≤ $5.
   Each step is logged as an amendment.
   1. B2 and O3 drop to 4 runs per arm.
   2. S drops to 2 runs per arm.
   3. G drops to 4 sessions per arm.
4. **If the projection still exceeds $8**, stop and ask before
   spending.
5. **During the run**, record the cumulative spend after each block. If
   the next block would cross $8, stop and ask.

## Exclusions

- Rows with `git_dirty=true` are void, in run files and longitudinal
  files alike.
- No other exclusion is allowed. Every run in a block counts,
  including failures, timeouts and abstentions.

## Amendments

*(none yet)*
