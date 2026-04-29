# Roadmap

`toolplane` should grow through small empirical slices. Each roadmap item should
prove a real execution path or adapter boundary.

## Dependency Order

```text
local_unsafe slice
  -> Pyodide+Deno + callback bridge
  -> cli-to-py adapter
  -> MCP adapter
  -> Docker/Modal remote backends
```

The goal is to test the core architecture before building a broad framework
around it.

## Tracks

### Execution Backends

- Pyodide+Deno backend for package-capable sandboxed snippets.
- Docker backend for real CPython, local CLI binaries, and system dependencies.
- Modal backend for remote package-heavy or longer-running jobs.
- E2B and Blaxel once the callback bridge and session model are stable.

### Capability Adapters

- Python function adapter, started in the first slice.
- `cli-to-py` adapter for exposing CLI wrappers as capabilities.
- MCP adapter for exposing MCP server tools as capabilities.
- Curated Python module/library exposure for packages such as `pandas`, `httpx`,
  and project SDKs.

### Bridge And Session Model

- Host callback RPC for sandboxed backends.
- Session lifecycle: create, execute snippets, cleanup.
- Artifact handling for files, plots, dataframes, and generated outputs.
- JSON-first serialization. Pickle should remain an explicit unsafe escape
  hatch, not the default.

### Discovery Quality

- Better search than the current token-count baseline.
- Tags/categories.
- Hardened `brief`, `detailed`, and `full` schema rendering.
- Missing-capability suggestions.
- Catalog visibility and authorization hooks.

### Developer Surface

- Strong README quickstart.
- Examples directory.
- CLI command for demos/smoke tests.
- GitHub Actions CI.
- MkDocs publishing.
- PyPI release checklist.

### Safety And Policy

- Backend capability enforcement.
- Filesystem and network policy model.
- Timeout and cancellation.
- Explicit unsafe-local warnings.
- Secret handling and redaction.

## Near-Term Issues

- [#1](https://github.com/oneryalcin/toolplane/issues/1): Spike Pyodide+Deno execution backend.
- Add host callback RPC bridge for sandboxed backends.
- Add `cli-to-py` adapter.
- Add MCP client adapter.
