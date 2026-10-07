# Monty As The Safe Default Backend

Spike record for #37. All findings below were verified empirically against
`pydantic-monty` 0.0.18 on macOS arm64 before any Toolplane code was written.

## Problem

A fresh default config could not serve safely: `default_backend` was
`local_unsafe` and `cli.mode` was `ambient`, so `toolplane serve mcp` always
required `--unsafe`. No safe backend was guaranteed to exist because
`pyodide-deno` needs Deno on PATH and re-fetches `npm:pyodide` from the CDN on
every run (high latency, and the #21 flake surface).

## Decision

Make Monty (`pydantic-monty`) the default backend, and make the default CLI
policy `disabled`.

Monty is the only candidate that satisfies "always available + safe":

| requirement            | monty                | pyodide-deno            | local_unsafe |
| ---------------------- | -------------------- | ----------------------- | ------------ |
| pure pip install       | yes (native wheel)   | no (needs Deno)         | yes          |
| safe by construction   | yes (no fs/network)  | yes (Deno permissions)  | no           |
| startup latency        | ~ms                  | seconds + CDN fetch     | ~ms          |
| third-party packages   | no                   | yes                     | import-only  |
| scoped `ns.member` sugar | no (see below)     | yes                     | yes          |

`pyodide-deno` remains the opt-in answer for package-capable snippets;
`local_unsafe` remains dev-only and keeps requiring `--unsafe` everywhere.

## 1.1 envelope delta (2026-10-08, #145)

Probed through Toolplane's real `monty` backend, same snippets on both
versions:

| Feature | 0.0.19 | 1.1.0 |
|---|---|---|
| decorators, `@dataclass` | no | yes |
| `collections`, `itertools`, `functools` | no | yes |
| `str.format`, `%` formatting | no | yes |
| `random`, `copy`, `base64` | no | yes |
| `eval` / `exec`, `complex` | no | yes |
| classes, f-strings, `datetime`, `json`, `re`, try/except | yes | yes |
| `del`, `match`, generators (`yield`) | no | no |
| third-party imports (`pandas`), `open()` on host files | denied | denied |

Filesystem, environment, network, and subprocess access did not move: a
31-probe sweep (`open`, `pathlib`, `os.listdir`/`stat`/`mkdir`, `os.environ`,
`os.getenv`, `os.urandom`, `subprocess`, `socket`, `__import__`, …) is denied
or unsupported on both versions, in one-shot and session mode. What 1.x newly
exposes by default (Toolplane passes no `os_policy`): the worker's wall clock
(`time.time()`, `datetime.now()`, `date.today()`, UTC), worker OS entropy for
`random`, and real `time.sleep` / `asyncio.sleep` (pool-capped at 10s per
call, not counted by monty's duration limits — the host timeout cuts them
off; regression-tested). `time.process_time()` stays `0.0`. Two 1.x behaviors
the backend now owns:
`max_duration_secs` became `max_feed_duration_secs` (its clock runs only
while sandbox code executes, so the host `asyncio.wait_for` stays the
wall-clock bound), and `max_suspensions` caps host round trips at 1000 per
checkout by default — every capability call counts, cumulatively across a
session's runs — so the backend sets it out of reach.

## Empirical capability envelope (pydantic-monty 0.0.18 baseline)

This section preserves the original 0.0.18 decision evidence. Stable 0.0.19
subsequently added class definitions and removed `max_allocations` from
`ResourceLimits`; Toolplane still uses flat capability bindings by design.

Verified working:

- `async def` / `await`, including Toolplane's exact `wrap_async_main` shape
  and top-level `await`.
- Async external functions via `Monty.run_async(external_functions=...)` —
  the exact hook `bridge.call_tool` dispatch needs. External function
  exceptions are catchable inside the sandbox with `try/except`.
- Flat callable namespace: every registry capability already carries a flat
  alias (`{server}_{tool}`, e.g. `math_multiply`), so external functions cover
  the full capability surface.
- Stdout capture via `CollectStreams`; f-strings, comprehensions; a stdlib
  shim subset (`json`, `math`, `re`, `datetime` — NOT `types`/`collections`).
- `ResourceLimits`: `max_duration_secs=0.5` killed a hot loop at 0.51s;
  also `max_memory`, `max_recursion_depth`, and (in 0.0.18 only)
  `max_allocations`.
- True concurrency: 5 parallel `run_async` calls with async externals
  completed in one 0.1s sleep window (VM resume offloaded to threads).
- Structured errors: `MontyRuntimeError.exception()` returns the real Python
  exception; `.traceback()` returns frames.

Verified NOT working (each probed directly):

- Class definitions: `NotImplementedError` in the 0.0.18 parser snapshot. Class
  definitions are supported in stable 0.0.19, but that does not itself supply
  scoped host-call bindings.
- Scoped namespace emulation attempts, all dead ends:
  dotted external names don't bind a root name; `SimpleNamespace` inputs are
  not convertible; dataclass inputs preserve attribute access but callable
  fields are not callable in-VM; no `types`/`collections` modules to build a
  namespace object in-sandbox.
- Third-party packages (no install, no import).
- `print(file=sys.stderr)` (`'file' argument is not supported`).

## Consequence: namespace contract on monty

On the monty backend, agent code uses the flat aliases and `call_tool`:

```python
r = await math_multiply(x=6, y=7)          # flat alias
r2 = await call_tool("mcp.math.multiply", {"x": 6, "y": 7})
```

Scoped sugar (`await math.multiply(...)`) remains available on `local_unsafe`
and `pyodide-deno`. Capability schemas already expose `aliases` as data, so
flat names are discoverable through `get_capability_schemas`. Stable 0.0.19's
class syntax does not change that host-binding contract; scoped Monty bindings
would require a separate design and compatibility decision.

## Scope decisions

- Config defaults flip (`default_backend = "monty"`, `cli.mode = "disabled"`);
  the `Toolplane()` constructor defaults are unchanged — the constructor is a
  local dev convenience, config files describe deployments.
- `EffectivePolicy` derivation is untouched: the override allowlist stays
  `(default_backend,)`, so `execute_code(backend="local_unsafe")` stays
  blocked (fail-closed, #24/#25). Allowing `pyodide-deno` as a safe override
  under default policy is a possible follow-up, deliberately not this slice.
- Version pin `pydantic-monty>=1.1,<2`: API churn must not be able to brick
  the default backend via an unconstrained resolve.
- Timeout enforcement is belt-and-braces for one-shot runs:
  `ResourceLimits.max_feed_duration_secs` bounds VM time, an outer
  `asyncio.wait_for` bounds wall clock including time spent inside external
  tool calls and sleeps. Sessions use only the outer `asyncio.wait_for` (see
  `docs/monty-session-spike.md` for why).
