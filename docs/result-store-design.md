# Result Store Design

Status: accepted, implemented (`src/toolplane/results.py`). Tracking issue:
[#43](https://github.com/oneryalcin/toolplane/issues/43). This document
consolidates the issue thread (three review rounds); where they differ, this
document wins.

## Problem

Every `execute_code` run is a fresh sandbox. Multi-step agent work therefore
routes intermediate data through the model's context twice — once returned,
once pasted back into the next snippet as a literal — or re-fetches, which is
wasteful for reads and wrong for non-idempotent tools. Routing data through
the context is the exact failure Toolplane exists to prevent.

## Decision

Persist **data, not interpreters**. Snippets can save JSON-shaped values to a
host-side, in-memory, session-scoped store and load them in later runs. The
sandbox stays provably fresh on every run; only named data survives.

"Later runs" means later `execute_code` calls **within one long-lived
Toolplane process** — a `serve mcp` process or a Python-embedded runtime.
`toolplane run` constructs a fresh runtime per invocation, so handles never
survive across separate CLI invocations; that is the in-memory contract
working as intended, not a gap.

```python
# run 1
issues = await linear_list_issues(query="label:bug")
handle = await save_result(issues, label="issues")   # full data -> host store
return {"count": len(issues), "sample": issues[:3], "issues_handle": handle}

# run 2 — fresh sandbox
issues = await load_result("res_...")            # host -> sandbox, never through the model
```

The strategic property: the model sees a summary and a handle; the full value
never transits the context window in either direction.

`save_result` is the core primitive. Auto-saving successful return values may
be added later as convenience, but it cannot deliver the summary-plus-handle
pattern and is not part of v1.

## Contract

One sentence: **if `save_result` can canonicalize a value to JSON and measure
its serialized bytes, the store can hold it; if it cannot, it belongs in
artifacts.** The test is the serialization itself, not the transport: on
`local_unsafe` the bridge is an in-process call where live objects can cross,
so a transport-based rule would silently admit them on exactly the backend
where they can sneak in. `save_result` serializes at save time on every
backend and rejects what does not round-trip.

- Values are JSON-shaped (the bridge boundary already enforces this on the
  sandboxed backends). Known fidelity limits apply: datetime becomes str,
  tuple becomes list.
- No pickle, ever: unpickling agent-influenced bytes is arbitrary code
  execution plus version fragility.
- No live Python objects: they cannot re-enter monty/pyodide sandboxes, they
  alias mutably across runs, and their memory cannot be measured for caps.
- No persistent interpreter. Monty's `MontyRepl` remains the documented
  upgrade path if real usage proves agents need live state; this design does
  not foreclose it.
- The name is **result store**, never "variables" or "session state". The
  mental model taught to agents is "save what you want to keep".

## Scope, retention, and security

- **In-memory only, never persisted to disk.** Process lifetime is the
  privacy boundary; restart is the clear operation. There is deliberately no
  `toolplane result clear` command — a CLI cannot reach a running serve
  process's memory, and disk-backing the store to enable one would create the
  retention surface it tries to manage.
- **The handle is the sole authority** (the file-descriptor lesson): handles
  are unguessable (`secrets.token_urlsafe`), prefixed `res_`. The optional
  `label` is metadata for debugging and future listing only — it is never a
  lookup key, so two snippets saving `label="issues"` cannot collide or
  overwrite each other.
- **Session scoping:** on stdio transport one process serves one client, so
  process scope equals session scope. On HTTP multi-client transports the
  store is **disabled (fail-closed)** in v1 rather than shipped global, which
  would let one client load another client's data.
- **Caps, enforced on save:** max entries (default 64), max total bytes
  (default 32 MiB), max bytes per entry (default 8 MiB), TTL (default 1
  hour). When a cap would be exceeded, `save_result` **fails with a clear
  error** telling the agent the limit and what to do (save a smaller
  projection, or re-use an existing handle). No silent LRU eviction: quietly
  losing data an agent believes is saved is worse than an explicit no.
- Config: a `[results]` table (`enabled`, `max_entries`, `max_total_bytes`,
  `max_entry_bytes`, `ttl_seconds`), `extra="forbid"` like every other table.

## High-level design

```text
  sandbox (fresh per run)              host (one process)
  ┌─────────────────────────┐          ┌──────────────────────────────┐
  │ save_result(val, label) │ bridge   │ InProcessBridge              │
  │ load_result(handle)     │ ───────► │   └─ ResultStore             │
  │                         │ ◄─────── │       {res_ab12: entry, ...} │
  │ (bindings, like cli_run)│          │       caps + TTL, in-memory  │
  └─────────────────────────┘          └──────────────────────────────┘
```

`save_result`/`load_result` dispatch through the existing bridge exactly like
every other capability, which is what keeps the security model unchanged: the
host owns the store, the sandbox only sends requests.

## Low-level design

- `src/toolplane/results.py`: `ResultStore` — a dict of
  `handle -> (label, json_bytes, saved_at)` plus cap/TTL enforcement.
  Signature: `save_result(value, label=None) -> handle`;
  `load_result(handle) -> value`. Size accounting uses the serialized JSON
  byte length (measurable, backend-independent). TTL is checked lazily on
  access and save.
- **Exposure as hidden capabilities**, mirroring ambient CLI
  (`toolplane:cli/run`): `toolplane:results/save` and
  `toolplane:results/load`, registered hidden, dispatched via
  `bridge.call_tool`. This makes the store reachable from **every backend
  with zero backend-specific plumbing** via
  `call_tool("toolplane:results/save", {...})`. Note the precise meaning of
  hidden in today's registry: **hidden from discovery** (search, list, and
  namespaces omit it) but **callable by canonical name**, and
  `get_capability_schemas(["toolplane:results/save"])` resolves it. That is
  the intended behavior — the store is infrastructure, not a discoverable
  capability, but nothing about it is secret.
- **Sugar bindings per backend**, exactly like the CLI surface: monty binds
  `save_result`/`load_result` as external functions; local and pyodide gain
  the same names through their existing namespace injection. Capability and
  input names take precedence over the bindings, mirroring existing
  reserved-name handling.
- The store hangs off the runtime (one per `Toolplane` instance), constructed
  from config. The facade passes nothing new: the bridge already reaches it.
- Errors: unknown or expired handle raises a `ResultStoreError` with the
  handle in the message; cap violations raise with the specific limit.
  Both surface in-sandbox as catchable exceptions, consistent with tool
  errors.

## Failure modes (decided)

| event                | behavior                                              |
| -------------------- | ----------------------------------------------------- |
| store full           | `save_result` fails loudly with the limit in the message |
| entry too large      | fails loudly, suggests saving a projection            |
| unknown handle       | fails loudly with the handle                          |
| expired handle (TTL) | same as unknown; expiry is lazy                       |
| non-JSON value       | fails at save with the offending type                 |
| store disabled       | `save_result`/`load_result` fail with "results store is disabled" |

## Out of scope (follow-ups)

- **Artifacts** are the second lane for files and blobs (parquet, images,
  logs): `df.to_parquet(...)` in-sandbox against a host-owned scratch dir,
  handle in the result, `load_artifact` later. Feasible on monty via
  pydantic-monty's `MountDir` (ro/rw/overlay with `write_bytes_limit`). Own
  issue when started; `ExecutionResult.artifacts` is the existing hook.
- Auto-saving successful return values (convenience only).
- Per-session stores on HTTP multi-client transports.

## Precedent

The ship-names-not-values pattern under an expensive channel, independently
derived by several lineages, each contributing one lesson to this design:

- [Cap'n Proto promise pipelining](https://capnproto.org/rpc.html) —
  composable interfaces without do-everything endpoints.
- [Ray `ObjectRef`](https://docs.ray.io/en/latest/ray-core/objects.html) —
  `save_result -> handle` is the primitive; move compute to data because the
  coordinator channel (here, the context window) is the scarce resource.
- [PostgreSQL cursors](https://www.postgresql.org/docs/current/sql-declare.html)
  and [temp tables](https://www.postgresql.org/docs/current/sql-createtable.html)
  — scope and lifetime are part of the API, not an afterthought.
- Unix file descriptors — the handle is the capability; process-scoped,
  unguessable, dies with the process; names are never the authority.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
  — keep runtime state and application memory distinct; Toolplane's version
  is deliberately narrower than both.

Rule of thumb: `save_result`/`load_result` is a Ray ObjectRef plus a Postgres
temp table plus an fd-like capability — scoped to one Toolplane execution
client, and JSON-only.

## Test plan

- save/load round-trip across two `execute_code` runs (facade-level, monty).
- Summary-plus-handle flow: full value never appears in the first response.
- Cap enforcement: entries, total bytes, entry bytes, TTL expiry — each fails
  loudly with the documented message.
- Unknown handle, disabled store, non-JSON value.
- `call_tool("toolplane:results/save", ...)` works on a backend with no sugar
  bindings (proves the zero-plumbing path).
- Handle unguessability shape (`res_` prefix, token length).
- Labels are not authority: two saves with the same label yield distinct
  handles and distinct values.
