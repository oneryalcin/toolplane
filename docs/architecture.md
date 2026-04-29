# Architecture

`toolplane` should be built with a small working core and explicit boundaries.
The goal is not to create a general agent framework. The goal is to create the
programmable tool surface that an agent framework can use.

## Design Posture

Use implementation discipline first: build a narrow vertical slice that can be
read from top to bottom. Avoid speculative plugin frameworks, lifecycle systems,
or abstract factories until real backends force them.

Name the real boundaries early so the code does not collapse into glue:

- `Capability`: something code can call.
- `Registry`: searchable catalog of capabilities.
- `Schema`: how the agent learns to call something.
- `Backend`: where code runs.
- `Bridge`: how sandboxed code calls host capabilities.
- `Session`: execution state across snippets.

The main rule:

```text
Discovery and execution are separate. Backends execute code; registries describe and dispatch capabilities.
```

## Core Flow

```text
Capability sources
  -> registry
  -> discovery tools
  -> code execution backend
  -> bridge back to registry
  -> result/artifacts
```

Capability sources include:

- Python functions.
- Python modules and libraries.
- MCP tools.
- `cli-to-py` wrappers.
- Host application helpers.

Execution backends include:

- local unsafe.
- Monty.
- Pyodide+Deno.
- Docker.
- Modal, E2B, or Blaxel later.

MCP tools, CLI wrappers, and Python functions are capability adapters, not
execution backends. Backends should not know whether a callable came from MCP,
a CLI, or a normal Python function.

## Proposed Layout

```text
src/toolplane/
  __init__.py

  capabilities.py      # Capability, CapabilitySchema, tags, metadata
  registry.py          # register/search/get_schema/call
  discovery.py         # search, get_schema, list_tools renderers
  execution.py         # ExecutionResult, errors, limits, artifacts

  backends/
    __init__.py
    base.py            # CodeBackend protocol + backend capabilities
    local.py           # development-only unsafe local backend
    monty.py           # Monty backend
    pyodide_deno.py    # default package-capable sandbox
    docker.py          # later
    modal.py           # later

  bridges/
    __init__.py
    base.py            # bridge protocol
    in_process.py      # direct callable injection
    rpc.py             # sandbox-to-host callback bridge

  adapters/
    __init__.py
    python.py          # normal function/module registration
    mcp.py             # MCP tool adapter
    cli_to_py.py       # cli-to-py adapter

  schemas/
    __init__.py
    render.py          # brief/detailed/full schema rendering
    json_schema.py     # introspection helpers

  sessions.py          # execution sessions and state
  errors.py
```

## First Vertical Slice

Start with one working path:

1. Register normal Python functions.
2. Search the registry.
3. Get a callable schema.
4. Execute code in `local_unsafe`.
5. Let code call registered functions through `await call_tool(...)`.
6. Return an `ExecutionResult`.

Then add Pyodide+Deno. That forces the real bridge problem early without
pulling Docker, Modal, E2B, or Blaxel complexity into the first version.

## Backend Boundary

Backends know how to run code. They do not know how to search MCP tools, parse
CLI help, or inspect Python function signatures.

The target API should stay source-agnostic:

```python
result = await runtime.execute(
    code,
    backend="pyodide-deno",
    packages=["pandas"],
)
```

That should work whether `code` calls an MCP-backed capability, a `cli-to-py`
wrapper, or a regular Python helper. The registry and bridge own that dispatch.

## Avoid

- A giant agent runtime abstraction.
- Backend-specific capability models such as "MCP backend" or "CLI backend".
- A plugin framework before concrete implementations need it.
- Raw text-only results when structured values, logs, errors, and artifacts are
  available.
- Pretending Monty, Pyodide, Docker, and Modal have the same capability surface.

Keep protocols small, implementations concrete, and tests centered on behavior.
