---
name: driving-toolplane
description: How to drive a Toolplane MCP server well — discovery flow, snippet conventions, CLI call shapes, the result store, and backend selection. Read this before writing execute_code snippets.
---

# Driving Toolplane

Toolplane exposes three meta-tools — `search_capabilities`,
`get_capability_schemas`, `execute_code` — instead of one MCP tool per
capability. You write small Python snippets against a curated namespace.

## The flow

1. **Read `toolplane://namespace` first.** It is the live manifest of every
   binding in the execution namespace — capability functions, CLI bindings
   with the current allowlist, result store — with exact call shapes. It is
   generated from runtime state, so it never lies about this server's
   configuration.
2. `search_capabilities(query)` — matching is exact-word, not fuzzy or
   semantic. If nothing matches, try different words; an **empty query lists
   every capability**. The CLI and result-store surfaces are not registry
   capabilities: they appear in the manifest, not in search results.
3. `get_capability_schemas(names)` — names must be canonical, exactly as
   search returns them (`mcp:<server>/<tool>`, `toolplane:<area>/<op>`).
   Guessed or abbreviated names will not resolve.
4. `execute_code(code)` — the snippet body runs inside an async function:
   `return` works at the top level, and every Toolplane binding is a
   coroutine.

## Snippet conventions

- **Await everything.** Capability, CLI, and result-store bindings are all
  async. A call that is never awaited fails with a typed
  `UnawaitedToolCallError` before or after execution — if you see it, add
  `await`.
- `return` a JSON-shaped value (dict/list/str/number/bool/None). The result
  arrives in the tool response's `value` field; `print(...)` output arrives
  in `stdout`.
- Two equivalent capability call shapes:
  - flat/scoped binding: `await deepwiki_ask_question(repoName=..., question=...)`
    or `await deepwiki.ask_question(...)`
  - canonical, works for everything including hidden capabilities:
    `await call_tool("mcp:deepwiki/ask_question", {"repoName": ..., "question": ...})`

## CLI

Only binaries on the server's allowlist are bound (the manifest lists them).

- Flat shape: `await git('log', oneline=True, max_count=3)` — subcommand as
  the first positional argument, flags as keyword arguments.
- Awaiting returns `{'stdout', 'stderr', 'exit_code', 'ok'}` — check `ok`
  or `exit_code`, and read `stderr` on failure.
- A non-allowlisted binary has **no binding**: calling it raises a plain
  `NameError`, which looks like a typo but means policy. The generic runners
  (`cli_run(binary, subcommand, flag=value, ...)` on monty, the `cli` object
  on local/pyodide) reject the same binary with an explicit policy error
  that names the allowed binaries.

## Result store

Persist JSON-shaped data across `execute_code` runs (in-memory, this server
session only, never written to disk):

```python
handle = await save_result(rows, label="q3-latency")
return {"handle": handle, "count": len(rows)}
# later run:
rows = await load_result(handle)
```

- The handle is the only key; labels are debugging metadata.
- A saved value is also readable directly as the MCP resource
  `toolplane://results/<handle>` — no execute_code run needed.
- Values must be JSON-shaped. For files and binary data, use the artifact
  store instead; for anything else, save a projection: e.g. a pandas
  DataFrame as `df.to_dict(orient="records")`. Keep projections small —
  the store has size caps and a TTL.

## Artifact store

Persist files and binary blobs (parquet, CSV, images, logs) across runs —
the bytes sibling of the result store:

```python
data = df.to_csv(index=False).encode("utf-8")
handle = await save_artifact(data, filename="metrics.csv")
# later run:
data = await load_artifact(handle)
```

- `save_artifact` takes **bytes** (JSON-shaped values belong in
  `save_result`); the execute_code response lists each artifact saved
  during the run with its handle and resource URI.
- An artifact is also readable directly as the binary MCP resource
  `toolplane://artifacts/<handle>` — on clients that materialize binary
  resources (Claude Code), that hands you a real local file.
- Artifacts live on the host disk for this server session only and are
  deleted when the session ends.

## Backends and packages

- The server pins a default backend; overriding `backend=` is usually
  blocked by facade policy (the error names the allowed overrides).
- **monty** (safe default): no imports of third-party packages, flat
  callable namespace.
- **pyodide-deno**: supports `packages=["pandas", ...]` for
  NumPy/pandas-style work.
- Errors come back structured: `error.type` / `error.message` are the real
  Python exception; `stderr` carries the full traceback.

## When something fails

Every dead end tells you the next move: no-match searches report how many
capabilities exist and how to list them; unknown names point at canonical
naming; policy errors name what is allowed. Read the error message before
retrying — it is written for you, not for a log file.

Toolplane errors are catchable by builtin type, identically on every
backend: `except ValueError` for result-store failures (expired handle,
non-JSON value), `except PermissionError` for CLI policy rejections,
`except LookupError` for unknown capability names.

```python
try:
    rows = await load_result(handle)
except ValueError:
    rows = await recompute()  # handle expired — re-derive and re-save
```
