# Pre-registered store-teaching re-test (October 2026)

**Status: registered, not yet run.** The commit that adds this file is
the registration. The hypotheses, the sessions they read, the decision
rule and the analysis (`bench/analyze_store.py`, committed with this
file) must not change after that commit. Every later change is logged
under [Amendments](#amendments) with its date, commit and reason,
including review fixes made before any data.

## Why

In the persistence follow-up (`RESULTS-persistence-2026-10.md`), the
sessions-off arm advertised `save_result`/`load_result` and never used
them. On all 32 reuse turns it wrote `try: orders_cache` /
`except NameError:` and refetched inside the sandbox. Two things may
explain this:

1. **The tool description.** The `execute_code` description was
   byte-identical with sessions on or off. It never said whether
   variables persist, and it named the store without saying what it is
   for.
2. **The turn-1 prompt.** It asked the model to retain the records in a
   variable named `orders_cache` "if your tool surface supports
   persistent Python session state", and did not mention a store.

#177 changes (1). With sessions off and the store on, the description
now ends (sessions off, store on): "Variables do NOT persist between execute_code calls. To reuse
data later, `handle = await save_result(value)` and return the handle; a
later call gets it back with `await load_result(handle)`." This re-test
also replaces (2) with a neutral turn-1 prompt (`--turn1 neutral`, below)
and asks:

- **Does the model use the store once the description tells it to?**
- **Does a store-backed sessions-off arm then cost about the same on
  reuse turns as a live session?**

The neutral prompt only removes. It is the original turn-1 prompt with
the session-variable sentence deleted and nothing added: no "keep it for
later", no mention of a store. Its first sentence ("This is the first of
several related questions about the same order store") is unchanged from
#175. So the only text in the session that points at the store is the
tool description, which is the only one of the two that ships to real
users. A positive T1 still can't separate "the description taught the
store" from "removing the steer let the model look for one"; this run
doesn't claim that split.

## Frozen conditions

As in `PREREG-persistence-2026-10.md`, with these differences:

- **Code under test.** Toolplane at one commit on `main` that contains
  this file and #177's description change.
- **Turn-1 prompt `neutral`** (`longitudinal.TURN1_NEUTRAL`): the
  original turn-1 prompt minus the sentence "If your tool surface
  supports persistent Python session state, retain the complete fetched
  order list in a variable named orders_cache for later questions."
  Nothing is added. Turns 2–6 are unchanged; they still say "Reuse
  already-available data when possible", as in #175. Rows record `turn1`; the analysis
  is VOID on any other value.
- **Arms.** `toolplane` (session on) and `toolplane_nosession` (session
  off, store on). Direct is not run; nothing here compares against it.
- **Runs.** Exactly 8 sessions per arm. Arm order is counterbalanced by
  rep parity.
- **Unchanged.** `claude-sonnet-5-5`, the varied filler, the single
  client version, the uniform provenance, and no edits while a block
  runs.

## Decision rule

Imported unchanged from the persistence follow-up
(`analyze_persistence.py`):
- the bootstrap (4,000 resamples, seed 0, each arm independently);
- margin-first equivalence (±10% of the reference median);
- the session pass gate (7/8 sessions with no death, client exit 0 and
  turns 1–5 correct, else UNRESOLVED);
- the VOID checks, plus two of this study's own: every row has
  `turn1 == "neutral"`, and every row's `git_sha` equals the registration's
  merge commit (passed as `--git-sha`). Uniform rows from an older or
  reverted description would otherwise pass.

**T1 is a share, not a difference.** For each sessions-off session, the
share of its four reuse turns (2–5) whose `reuse_mechanism` is
`result_store`. That label needs three things (`longitudinal.
_store_load_succeeded`): the turn made no fixture calls; an
`execute_code` snippet calls `load_result` as a real call (the word in a
comment or a string doesn't count); and that call's result carries no
error. A handle can only load if an earlier turn saved it. The statistic is the mean share over 8 sessions, with a bootstrap
over sessions.
- **HOLDS** if the CI lies wholly above 0.5.
- **REVERSED** if it lies wholly below 0.5.
- **UNRESOLVED** otherwise.

A died session's share is unknown. It is imputed at 0 and at 1, and the
verdict stands only if both agree.

## Hypotheses

| id | statistic | predicted | #175 value (old description and prompt) |
|---|---|---|---|
| T1 | sessions-off reuse turns served by the store (mean share) | >0.5 | 0/32 |
| T2 | reuse cost turns 2–5, tp − nosession, equivalence ±10% of nosession | ~0 | −$0.0051 (−13%) |

T2's prediction assumes T1 holds. A sessions-off arm that loads from the
store makes the same two requests per reuse turn as a live session, and
neither puts the records in context. If T1 doesn't hold, T2's verdict is
printed with "(T1 did not hold: not a store comparison)", since it then
compares the session against refetching again.

### Reported but not tested

- Per arm:
  - sessions passing turns 1–5, and errors;
  - median turns 1–5 cost, reuse cost and peak context;
  - each reuse turn's `reuse_mechanism`.
- Whether turn 1 of the sessions-off arm called `save_result`, and
  whether it returned the handle (read from transcripts).
- The session arm's reuse mechanism under the new description. It is
  expected to stay `retained`.

## Blocks

Every command runs from the registration's merge commit with
`env -u ANTHROPIC_API_KEY UV_NO_CONFIG=1`.

| block | command | sessions |
|---|---|---|
| K (calibration and warm-up, in no cell) | `.venv/bin/python bench/longitudinal.py --model claude-sonnet-5-5 --arms toolplane,toolplane_nosession --reps 1 --filler varied --turn1 neutral` | 2 |
| L | `.venv/bin/python bench/longitudinal.py --model claude-sonnet-5-5 --arms toolplane,toolplane_nosession --reps 8 --filler varied --turn1 neutral` | 16 |

**K is also the cache warm-up.** In the persistence follow-up, the first
run of a block whose prompt prefix had no cached copy paid cache
creation, and the cost spike decided S1. K runs the same configuration
as L, so L must start within 30 minutes of K finishing, well inside the
1-hour cache lifetime. K's results file and transcripts are committed with
L's. The write-up reports the gap between K's last transcript event and
L's first, and whether L's first session paid cache creation on its
first request (its `cache_creation_input_tokens` against the others'). A
gap over 30 minutes is a deviation, reported as such.

**K's gate.** L runs only if:
- both K sessions pass the session gate; and
- K's transcripts show the neutral turn-1 wording, a check that the
  prompt is wired through.

K's store use is **not** a gate: stopping on it would make the run
depend on the outcome.

**Analysis** (K's file is not an input):

```
.venv/bin/python bench/analyze_store.py --git-sha <merge commit> --sessions <L's longitudinal-*.json>
```

## Budget

- **Cap: $4**, including K.
- **Projection: about $2.5.** The persistence follow-up's sessions cost
  $0.13 (session) and $0.14 (sessions off). K is about $0.27 and L about
  $2.2.
- **Rule.** After K, project L as 8 × (the sum of K's two session costs).
  If K + L exceeds $4, stop and amend. Never cut L below 8 per arm.

## Exclusions

- Rows with `git_dirty=true` are void.
- No other exclusion. Every session counts, including failures and died
  sessions (see the gate).

## Amendments

- **A1 (2026-10-10, logged with the results commit; decided after K,
  before L).** K's wiring check could not be done from transcripts as
  written: transcripts record only the client's output stream, never the
  prompts sent to it. It was done instead by running `run_session` at
  `d4482d1` against a fake client and capturing the harness's stdin:
  `--turn1 neutral` sends exactly `TURN1_NEUTRAL`. No rule, statistic,
  input or command changed. The probe is committed as
  `test_turn1_prompt_reaches_the_client`. The transcripts' blind spot was
  checkable against #175's transcripts at registration and was missed.
