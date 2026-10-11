# Store-teaching re-test: results (2026-10-10)

The registration is `PREREG-store-teaching-2026-10.md`, with one amendment
(A1, below). The registered analysis output is
`results/prereg-store-teaching-2026-10-analysis.md`, written by:

```bash
.venv/bin/python bench/analyze_store.py \
  --git-sha d4482d1d9ff3e4683354a939deb75570cd19932e \
  --sessions bench/results/longitudinal-20261010-204013.json
```

This doc reads those verdicts. It doesn't change any of them.

## Provenance

- **Code under test.** Every row, calibration included, is
  `git_dirty=false` at `d4482d1` (the registration's merge, which contains
  #177's description change). The analysis did not VOID.
- **Client, model, prompt.** Claude Code 2.1.296, `claude-sonnet-5-5`,
  filler `varied`, `turn1 = neutral` on every row.
- **Completeness.** 16 sessions (8 per arm). None died, every client
  exited 0, and all 16 passed turns 1–5.
- **Spend: $2.45 recorded** (K $0.30, L $2.15), under the $4 cap. After K
  the formula projected K + 8 × K = $2.71.
- **Cache warm-up.** K's last transcript event was at 19:39:01 UTC and L's
  first at 19:40:21, 80 seconds apart. L's first session paid the same
  first-request cache creation as the sessions after it (about 14.2K
  tokens created and 8.4K read on request 1, in all 16 L sessions). K's
  first session paid the true cold start (22.6K created, none read). So
  the warm-up worked and no L session paid a one-off cold start.
- **Amendment A1 (the K wiring check).** The registration said K's
  transcripts would show the neutral turn-1 wording. They can't: a
  transcript records only the client's output stream, never the prompts
  sent to it (true of every longitudinal transcript, old and new). After
  K and before L (an 80-second window), the check was done instead by
  running `run_session` at `d4482d1` against a fake client and capturing
  what the harness writes to it. With `--turn1 neutral` it sends exactly
  `TURN1_NEUTRAL`, with no session-variable sentence; the default still
  sends the original. No rule, statistic or input changed. The gap was
  checkable at registration time against #175's transcripts and was
  missed. So the check is reproducible rather than asserted, the same
  probe is now committed as `test_turn1_prompt_reaches_the_client`
  (`tests/test_bench_longitudinal.py`), which changes no harness code.
  The amendment is logged in the registration file with this results
  commit.

## Verdicts

| id | claim | verdict | point (95% CI) |
|---|---|---|---|
| T1 | once told, the sessions-off model uses the store on most reuse turns | **REVERSED** | 0.00 [0.00, 0.00]: 0 of 32 reuse turns |
| T2 | a store-backed sessions-off arm reuses at about the session's cost (±10%) | **REVERSED**, flagged "T1 did not hold: not a store comparison" | −$0.0037 [−0.0039, −0.0033]; $0.0317 vs $0.0354 |

## Readings

### The model stopped assuming persistence and still didn't use the store

The prediction failed: no sessions-off reuse turn loaded from the store,
and no snippet in either arm called `save_result` or `load_result`. The
sessions-off arm's code did change between the two runs.

Counts over every `execute_code` snippet:

| | #175 (old description, old prompt) | this run (new description, neutral prompt) |
|---|---|---|
| sessions-off snippets | 64 | 65 |
| …mention `orders_cache` | 47 | 0 |
| …catch `NameError` | 32 | 0 |
| …call `save_result` / `load_result` | 0 / 0 | 0 / 0 |
| session-arm snippets mentioning `orders_cache` | 52 of 64 | 0 of 64 |

In #175 the sessions-off model assumed its variables persisted: it tried
`orders_cache`, caught the `NameError` and refetched. Here each reuse turn
opens with a plain refetch (`ids = await orders_list_order_ids()`, then
the 30 fetches).

**Which change did this can't be separated, and part of it is plainly
the prompt.** The deleted turn-1 sentence named the variable
`orders_cache`. Its disappearance from the *session* arm too (52 → 0),
whose description says variables do persist, shows that row measures the
prompt, not the description. The `NameError` drop (32 → 0) is consistent
with the model having read "variables do NOT persist", but it is equally
consistent with the prompt no longer suggesting a variable to try. Both
changes shipped together by design, and this run does not attribute the
change to either.

What the run does show: with sessions off, the description naming the
store and its use, and nothing steering toward a session variable, the
model still refetched on every reuse turn rather than save. On this
fixture that is a reasonable choice: the refetch runs inside the sandbox
against a fast local server, and its records never enter the context. The
run doesn't show the store would go unused where refetching is slow,
rate-limited or billed.

### T2: a live session still beats refetching, by about 10%

With the store unused, T2 again compares the live session against
in-sandbox refetching, as the analysis flags. The session's reuse turns
cost 10% less ($0.0317 against $0.0354). The interval excludes zero and
reaches just past the ±10% margin, so the registered verdict is REVERSED.
#175 measured the same comparison at −13%. Over the five comparable
turns the arms differ by $0.003 (0.1185 against 0.1215). Within turn 1,
the session arm fetched once and reused the list for its second snippet
(31 fixture calls median); sessions-off fetched twice (62). One
sessions-off session (rep 4) spent an extra model request in turn 1 (5
requests, 63 fixture calls); it doesn't touch the reuse turns T2 reads.

### The session arm under the new description

Reported, not tested. All 32 session reuse turns answered from the live
session (`retained`). Every session reset in its own call and then
refetched; the fixed detector credits 8 of 8.

## What changes

- **Product.** The description fix (#177) stays: it states the
  configuration's real behaviour, and it stops advertising a store that
  is disabled. This run doesn't show it changed the model's behaviour on
  its own. It did not make the store a default. (No round trips were
  saved either way: the old `NameError` handling lived inside one
  snippet, and reuse turns took two requests in both runs.) If the store
  matters for slow or billed sources, the next step is a fixture where
  refetching is actually slow, with a hint on that cost (for example
  "saving avoids re-calling slow tools").
- **Paper.** The sessions section's "may explain the store going unused"
  paragraph can now say: with the description corrected and the prompt's
  steer removed together, the model stopped trying a persisted variable
  but still refetched instead of using the store (registered, 0/32).

## Files

- `results/longitudinal-20261010-204013.json` (L) with transcripts.
- `results/longitudinal-20261010-203804.json` (K, calibration, in no
  cell) with transcripts.
- `results/prereg-store-teaching-2026-10-analysis.md`, the registered
  output.
