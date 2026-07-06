# Monty Session-Persistent Namespaces

Spike record for #77. All findings verified empirically against
`pydantic-monty` 0.0.18 on macOS arm64, before any Toolplane code was
written. Verdict up front: **feasible** — monty ships a stateful REPL with
snapshot support, and every spike question resolved in its favor.

## Problem

Every `execute_code` run today gets a fresh interpreter. Variables,
functions, and intermediate data die with the run, so multi-step agent
workflows pay a save/load tax on every step (`save_result` → handle →
`load_result`), and a meaningful share of the discovery pedagogy
(SKILL.md, error signposts) exists to teach that tax. Agents expect a
notebook; we hand them a calculator.

`docs/architecture.md` names `Session` as a core boundary; `sessions.py`
was never built because we did not know whether monty could hold state.

## Spike questions and answers

### 1. Can monty keep a live VM across calls? — Yes, first-class

`pydantic_monty.MontyRepl` is exactly this: "Stateful no-replay REPL
session … execute snippets incrementally against persistent heap and
namespace state."

```python
repl = MontyRepl()
repl.feed_run("data = {'rows': [1, 2, 3]}\ndef double(x): return x * 2")
repl.feed_run("double(sum(data['rows']))")   # -> 12
```

Verified working:

- Variables, functions, and closures persist across `feed_run` /
  `feed_run_async` calls.
- A failed run (`ZeroDivisionError`) does **not** wipe the namespace; the
  session stays usable.
- Async external functions work per-call
  (`feed_run_async(code, external_functions={"call_tool": fn})` — note:
  a **dict**, unlike `Monty`'s list) and their results persist into later
  runs that never declared the function.
- `dump()` / `MontyRepl.load(blob)` serialize the full session — heap,
  functions, closures — to bytes. A restored session resumes correctly,
  including calling a closure over restored state.
- The type checker sees names from prior runs (run 2 referencing run 1's
  `data` type-checks clean).

### 2. Is namespace re-hydration needed as a fallback? — No

The fallback (replaying JSON-shaped globals into the next run) is strictly
worse than what exists: `MontyRepl` persists the *live heap*, so functions,
closures, and non-JSON values survive — the exact things re-hydration
would lose. Question retired.

### 3. Result/artifact stores: complement or replace? — Complement

Within one session, persistent namespaces subsume most of what
`save_result`/`load_result` is taught for (carrying data between runs).
The stores keep two jobs sessions cannot do:

- **Artifacts** are bytes exposed as MCP resources
  (`toolplane://artifacts/{handle}`) — a wire concern, orthogonal to
  interpreter state.
- **Results** remain the seam that works identically on *all* backends
  (pyodide has no session story) and across sessions in one process.

So: sessions retire pedagogy, not machinery.

### 4. Memory/lifetime policy — enforceable today

- `MontyRepl(limits={"max_memory": 10_000_000})` enforces the cap
  **cumulatively** across the session's accumulated state. The violation
  surfaces as a catchable `MemoryError` and the session survives it —
  the agent can delete state and continue.
- Snapshot cost makes per-run checkpoints affordable: a 100k-row
  list-of-dicts session dumps to 8.8 MB in 14 ms and loads in 28 ms;
  a typical small session is ~200 bytes in ~0.01 ms.
- Footgun (upstream): `ResourceLimits` is a TypedDict and **silently
  ignores unknown keys** — a misspelled `max_memory` disables the cap
  with no error (`extract_limits` in `crates/monty-python/src/limits.rs`
  never checks for unrecognized keys; filed as
  [pydantic/monty#534](https://github.com/pydantic/monty/issues/534)).
  Toolplane must validate limit keys itself until that lands.
- Related upstream: [pydantic/monty#483](https://github.com/pydantic/monty/issues/483)
  — REPL `max_duration_secs` counts from construction, not per feed, so
  duration caps on a long-lived session are effectively unusable;
  per-run timeouts must stay host-side (`asyncio.wait_for`).

## The sharp edge: cancellation

Toolplane enforces a per-run timeout with `asyncio.wait_for`. Cancelling a
`feed_run_async` mid-flight has two failure modes, both observed:

1. **Partial mutations persist.** A cancelled run's completed statements
   stay in the heap (`n = 99` before the blocking await survives the
   timeout). Without mitigation, a timed-out run leaves half-applied
   state — the same class of lie as the #71 abandoned-escalation bug.
2. **Deterministic session poison.** If the cancelled run stored a *new
   string literal of 2+ characters* into surviving state, the session is
   permanently bricked: the next feed panics on monty's tokio worker
   (`intern.rs:916 index out of bounds`) and raises
   `RuntimeError: Async REPL transition was cancelled before completion`,
   as does every feed after it. 25/25 reproductions; 1-char strings and
   ints (inline values, not interned) recover — which is why a naive
   stress loop using `append('x')` reported 20/20 clean and initially
   misread this as a rare race. Filed upstream as
   [pydantic/monty#533](https://github.com/pydantic/monty/issues/533);
   apparent mechanism: cancellation rolls back the intern table but heap
   objects mutated before the cancel still reference the new entry.

**Mitigation, verified (including under the panic scenario):**
snapshot-per-run. `dump()` before each run; on timeout, discard the
poisoned instance and `MontyRepl.load(snapshot)` a fresh one:

```python
snap = repl.dump()
try:
    await asyncio.wait_for(repl.feed_run_async(code, ...), timeout)
except TimeoutError:
    repl = MontyRepl.load(snap)   # VM state as if the run never happened
```

This makes every run atomic **with respect to VM state** — the namespace
either reflects a completed run or the run never happened — and the
poison is moot because the cancelled instance is dropped. At 14 ms per
8.8 MB of state, the checkpoint is affordable on every run.

**What rollback cannot undo: host-side effects.** Toolplane runs expose
host bridge functions (capabilities, CLI, `save_result`/`save_artifact`,
escalation grants). A snippet can commit any of those and *then* time
out; reloading the VM snapshot does not unsave a result, delete an
artifact, or revoke a grant. So the implementation must not advertise
whole-run transactionality: rollback scope is the namespace, and the
timeout signpost has to say so (state rolled back; tool calls and saves
made before the timeout stand). Escalations already have run-scoped
cancellation from #71; result/artifact handles created by a timed-out
run are visible in the audit log (`run_end.ok=false` + the run's
`dispatch` events) rather than silently orphaned.

## Other constraints found

- One run at a time per session: `MontyRepl` holds an internal mutex;
  concurrent `feed_run` calls serialize. Fine for the stdio/single-agent
  case; a shared-runtime transport must key sessions per client or
  disable them (same shape as the #71 stdio-only escalation gate).
- `pydantic-monty` is pre-1.0 (0.0.18); `MontyRepl` is newer surface than
  `Monty` and the API may shift. Pin and re-verify on upgrade.

## Decision

Build sessions on `MontyRepl` with snapshot-per-run rollback, scoped
honestly: VM-state atomicity, host-side effects explicitly excluded and
signposted. Follow-up implementation issue covers: session lifetime keyed
to the served MCP session (stdio first, mirroring escalation), explicit
`reset` affordance, limit-key validation, timeout signpost wording, and
which signposts/SKILL.md passages retire.

Not in scope for the follow-up: pyodide sessions (backend is
feature-frozen per #78) and cross-process persistence (`dump()` makes it
*possible*, but nothing demands it yet — the result store already scopes
to process lifetime).
