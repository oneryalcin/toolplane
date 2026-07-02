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

## Empirical capability envelope (pydantic-monty 0.0.18)

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
  also `max_memory`, `max_recursion_depth`, `max_allocations`.
- True concurrency: 5 parallel `run_async` calls with async externals
  completed in one 0.1s sleep window (VM resume offloaded to threads).
- Structured errors: `MontyRuntimeError.exception()` returns the real Python
  exception; `.traceback()` returns frames.

Verified NOT working (each probed directly):

- Class definitions: `NotImplementedError` from the parser. This kills the
  `__getattr__` shim used by the other backends for scoped namespaces.
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
flat names are discoverable through `get_capability_schemas`. Revisit if Monty
gains class support (it is under active development).

## Scope decisions

- Config defaults flip (`default_backend = "monty"`, `cli.mode = "disabled"`);
  the `Toolplane()` constructor defaults are unchanged — the constructor is a
  local dev convenience, config files describe deployments.
- `EffectivePolicy` derivation is untouched: the override allowlist stays
  `(default_backend,)`, so `execute_code(backend="local_unsafe")` stays
  blocked (fail-closed, #24/#25). Allowing `pyodide-deno` as a safe override
  under default policy is a possible follow-up, deliberately not this slice.
- Version pin `pydantic-monty>=0.0.18,<0.1`: pre-0.1 API churn must not be
  able to brick the default backend via an unconstrained resolve.
- Timeout enforcement is belt-and-braces: `ResourceLimits.max_duration_secs`
  bounds VM time, an outer `asyncio.wait_for` bounds wall clock including
  time spent inside external tool calls.
