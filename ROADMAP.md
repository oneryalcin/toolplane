# Roadmap

`toolplane` should grow through small empirical slices. Each roadmap item should
prove a real execution path or adapter boundary.

## Dependency Order

```text
local_unsafe slice
  -> Pyodide+Deno + callback bridge
  -> cli-to-py adapter
  -> MCP adapter
  -> config-driven runtime setup
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
- MCP adapter for exposing MCP server tools as first-class Python callables.
- Curated Python module/library exposure for packages such as `pandas`, `httpx`,
  and project SDKs.

### Bridge And Session Model

- Host callback RPC for sandboxed backends.
- Session lifecycle: create, execute snippets, cleanup.
- Artifact handling for files, plots, dataframes, and generated outputs.
- JSON is the default wire format, but not the programming model. Agent-written
  code should receive Python primitives and structured containers, not JSON
  strings.
- Pickle should remain an explicit unsafe escape hatch, not the default.

### Code Namespace

- Canonical qualified capability ids, such as `mcp:arch/list_entities`,
  `cli:gh/issue_list`, and `py:finance/calculate_nav`.
- Friendly Python aliases only when unique and valid.
- Scoped namespaces such as `mcp.arch.list_entities(...)` and
  `cli.gh.issue_list(...)`.
- Loud failures for alias collisions and reserved-name shadowing.
- `call_tool(name, params)` as the universal escape hatch.

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

- [x] [#1](https://github.com/oneryalcin/toolplane/issues/1): Spike Pyodide+Deno execution backend.
- [x] [#3](https://github.com/oneryalcin/toolplane/issues/3): Add host callback RPC bridge for sandboxed backends.
- [x] [#2](https://github.com/oneryalcin/toolplane/issues/2): Add `cli-to-py` adapter.
- [ ] [#4](https://github.com/oneryalcin/toolplane/issues/4): Add MCP capability adapter.
- [ ] [#7](https://github.com/oneryalcin/toolplane/issues/7): Add config-driven runtime setup.

## MCP Adapter Acceptance

The MCP adapter should confirm the core idea, not just wrap one protocol call:

- Register MCP tools and expose them through canonical ids plus safe Python
  aliases.
- Execute code that paginates an MCP result in a normal Python loop, aggregates
  the returned values, and returns a Python dict.
- Mix MCP, CLI, and regular Python capabilities in one snippet.
- Prove structured MCP output arrives as Python primitives, not a JSON string.
- Prove alias collisions fail loudly while canonical/scoped access still works.
- Preserve source, canonical id, tool name, and original error detail on
  failures.
