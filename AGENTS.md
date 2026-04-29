# toolplane Agent Guidance

This repo exists to build a controlled Python code-mode runtime where CLIs, MCP
tools, and libraries are normalized into one programmable tool surface.

## Product Direction

- Keep `toolplane` focused on the programmable tool surface, not a full agent
  framework.
- Preserve the stable flow:

```text
discover capabilities -> inspect schemas -> execute Python against a curated namespace
```

- Keep discovery and execution separate. Backends execute code; registries
  describe and dispatch capabilities.
- Treat MCP tools, CLI wrappers, Python functions, and host helpers as
  capability adapters, not execution backends.

## Implementation Bias

- Build small vertical slices and validate them empirically.
- Avoid speculative plugin frameworks or broad abstractions before concrete
  backends force them.
- Let each piece of code earn its place through behavior, tests, or an imminent
  integration need.
- Keep tests focused on contracts and empirical behavior. No ceremonial testing.

## Pydantic vs Dataclass

Dataclasses are fine for simple internal value carriers.

Prefer Pydantic earlier when a model is likely to cross a boundary soon, not only
in a distant future. Boundary-crossing models include:

- execution results and structured errors
- backend capability/config objects
- capability manifests loaded from config or remote registries
- data sent over sandbox RPC
- adapter inputs/outputs that need validation or JSON schema export

If a dataclass is starting to need manual validation, serialization,
deserialization, or schema generation, move it to Pydantic instead of layering
more custom code around it.

## Backend Direction

- `local_unsafe` is only for development and shape validation.
- Monty is useful for safe small-tool orchestration, but it is not the default
  answer for package-heavy code.
- Pyodide+Deno is the next likely default sandbox for package-capable snippets,
  especially pandas/NumPy-style workflows.
- Docker, Modal, E2B, and Blaxel are needed for arbitrary CPython packages,
  native system dependencies, subprocesses, GPUs, remote isolation, or
  long-running jobs.

## Verification

- Prefer empirical smoke tests alongside unit tests.
- For backend work, prove the real execution path works, not just config
  construction.
- Keep artifacts out of the repo unless they are intentional source files.
