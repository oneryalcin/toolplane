# Code Mode Backends

`toolplane` should keep one stable agent-facing pattern:

```text
discover capabilities -> inspect schemas -> execute Python against a curated namespace
```

!!! note "Backend choice follows workload shape"

    Monty is useful for small safe orchestration. Pyodide+Deno is the likely
    default for pandas/NumPy-style snippets. Docker, Modal, E2B, and Blaxel are
    for arbitrary CPython packages, subprocesses, native dependencies, GPUs, or
    stronger remote isolation.

The execution backend should be swappable. Different workloads need different
tradeoffs between safety, package support, startup latency, local development
ergonomics, and production isolation.

## FastMCP Code Mode Lessons

FastMCP Code Mode is the closest reference design:

- It hides the original tool catalog behind discovery meta-tools and one
  execution tool.
- Discovery is progressive: search first, inspect schemas second, execute third.
- Tool catalogs are request-scoped, so auth and visibility can change per user.
- The sandbox receives explicitly injected async functions rather than ambient
  access to the server.
- The sandbox boundary is abstracted behind a provider protocol.

The provider abstraction is the important part to preserve. FastMCP's current
provider protocol is intentionally small:

```python
async def run(
    code: str,
    *,
    inputs: dict[str, Any] | None = None,
    external_functions: dict[str, Callable[..., Any]] | None = None,
) -> Any: ...
```

`toolplane` should keep that spirit, but its backends need to advertise
capabilities because some support third-party packages and files while others
do not.

## OpenAI Agents SDK Sandbox Lessons

[OpenAI's Agents SDK sandbox design](https://developers.openai.com/api/docs/guides/agents/sandboxes)
reinforces the same separation of concerns:

- The sandbox session/client owns execution location, filesystem, lifecycle,
  resume state, snapshots, and provider options.
- Sandbox capabilities bind to the live session and expose tools that call the
  session API.
- Provider-specific code implements the session operations. Docker uses
  container exec; Modal creates a `modal.Sandbox` and runs commands through
  `sandbox.exec`.
- Normal Python function tools remain host-side SDK tools. They are not
  magically imported into the remote sandbox.

The implication for `toolplane` is direct: a backend executes code, while the
bridge decides how sandboxed code reaches host capabilities. For local unsafe
execution, that can be a direct Python call. For Pyodide+Deno, Docker, Modal,
E2B, or Blaxel, it should usually be a host callback or provider-appropriate
proxy unless the capability is explicitly safe to ship into the sandbox.

## smolagents Lessons

[smolagents](https://huggingface.co/docs/smolagents/main/tutorials/secure_code_execution)
is another important reference. It starts from the same premise: agents should
write code because code is a better action language than repeated JSON tool
calls.

Its sandbox menu is useful for `toolplane`:

- A local interpreter with import allowlists and operation limits.
- Blaxel, E2B, Modal, and Docker executors for isolated code execution.
- A WebAssembly executor using Pyodide and Deno.

In the implementation, remote executors keep a remote namespace alive: tools and
variables are shipped into that namespace, required packages are installed or
loaded there, and later code snippets execute against that state. The
Pyodide+Deno executor runs a local Deno HTTP server, grants only scoped Deno
permissions, loads Pyodide, and uses `micropip` to load requested Python
packages.

The Pyodide+Deno path is especially relevant. Pyodide is CPython compiled to
WebAssembly, and the official Pyodide distribution supports packages including
NumPy, pandas, SciPy, Matplotlib, and scikit-learn. That makes it a much better
default than Monty for data-analysis code that needs common scientific Python
packages.

The caveat is that Pyodide is not arbitrary Linux CPython. Pure Python wheels
are generally supported, and many native-extension packages have been ported,
but packages that need unsupported native extensions, system libraries,
subprocesses, or real OS services still need Docker, Modal, E2B, Blaxel, or
another backend.

## Backend Matrix

| Backend | Use | Strengths | Limits |
| --- | --- | --- | --- |
| Local unsafe | Development only | Full local Python, imports, files, fastest to debug | Not a sandbox; never production for untrusted code |
| Monty | Safe small-tool orchestration | Very low latency, resource limits, explicit external functions | Limited Python/runtime surface; not for pandas or arbitrary packages |
| Pyodide+Deno | Default package-capable sandbox | WebAssembly isolation, pandas/numpy-style package support, good local/edge fit | Not arbitrary Linux CPython; limited native/system/subprocess support |
| Local subprocess/venv | Trusted or semi-trusted local work | Real CPython, third-party packages, easy package resolution | Weak isolation unless wrapped with OS controls |
| Docker | Default production-ish local backend | Real CPython, third-party packages, filesystem/network controls | Higher startup latency; requires Docker/runtime management |
| E2B | Hosted code interpreter | Purpose-built remote sandbox, package installation, persistent session patterns | External service, credentials/cost, artifact transfer |
| Blaxel | Hosted fast-start sandbox | Remote isolation, fast warm starts, scale-to-zero model | External service, credentials/cost, platform-specific lifecycle |
| Remote sandbox | Hosted isolation | Central policy, scalable isolation, easier production operations | Network latency, credentials, cost, artifact transfer |
| Modal | Remote Python execution | Strong fit for dependency-rich Python workloads and scalable jobs | Requires Modal account/config; async job lifecycle must be modeled |

Monty remains valuable, but it should not be the only backend. It is a good
answer for "safe code that calls host-provided functions." It is not the right
answer for "let the agent import pandas and transform a dataframe."

Pyodide+Deno is the likely default for package-capable sandboxed snippets.
Docker and Modal remain necessary when the code needs arbitrary CPython wheels,
system packages, subprocesses, local CLI binaries, GPUs, or long-running jobs.

## Backend Contract

The execution API should return structured metadata, not just the Python return
value:

```python
class CodeBackend(Protocol):
    async def run(
        self,
        code: str,
        *,
        bridge: HostBridge,
        inputs: Mapping[str, Any] | None = None,
        limits: ResourceLimits | None = None,
        packages: Sequence[str] = (),
        files: FilePolicy | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecutionResult: ...
```

`ExecutionResult` should include:

- `value`: the returned Python value.
- `stdout` and `stderr`: captured output.
- `artifacts`: files or generated objects the backend chooses to expose.
- `duration_ms`: execution time.
- `backend`: backend identifier and version/config summary.
- `error`: structured exception details when execution fails.

Backends can reject unsupported options early. For example, the Monty backend
should reject `packages=["pandas"]` with a clear capability error instead of
letting the script fail later.

The backend receives a bridge, not a registry and not adapter-specific tool
objects. That keeps the execution boundary source-agnostic: Pyodide, Docker, or
Modal code can call host capabilities without knowing whether they came from an
MCP server, a CLI wrapper, or a normal Python function.

## Capability Model

Each backend should describe what it supports:

```python
class BackendCapabilities(TypedDict):
    imports: bool
    third_party_packages: bool
    filesystem: Literal["none", "read", "read_write", "mounted"]
    network: Literal["none", "restricted", "full"]
    resource_limits: set[str]
    persistence: Literal["none", "session", "artifact"]
    startup_latency: Literal["low", "medium", "high"]
```

The planner can use this before code execution:

- Use Monty when code only coordinates registered functions.
- Use Pyodide+Deno when the code needs common data/science packages such as
  pandas, NumPy, or plotting and does not need real OS subprocesses.
- Use Docker, E2B, Blaxel, or Modal when the code needs arbitrary packages,
  native system dependencies, subprocesses, GPUs, or stronger remote isolation.
- Use local unsafe only for developer-controlled experiments.

## Namespace Model

The namespace exposed to agent-written code should be source-agnostic:

- Python functions and libraries.
- MCP tools wrapped as async Python functions.
- CLI tools wrapped by `cli-to-py`.
- Host-provided domain helpers.

The code should not need to know where a function came from. Naming, schemas,
auth, and return normalization belong in the registry layer before execution.
Ambient CLI access should be lazy: the runtime may expose safe names such as
`git.diff(...)`, but it should parse and dispatch a binary only when code uses
it. Host configuration should control policy and overrides rather than require
registration ceremony for basic local CLI availability.

The code should also not need to manipulate JSON strings. JSON is the default
wire format across sandbox, remote, MCP, and CLI boundaries, but the programming
model is Python values:

- registered tools return `dict`, `list`, `str`, numbers, booleans, or `None`
  when their outputs are structured;
- rich objects are created inside the execution environment or represented by
  explicit artifact/file handles;
- adapter errors preserve source, canonical capability id, tool name, and
  original detail.

Friendly Python names are aliases, not identity. Scoped namespaces such as
`repo.read_text(...)` and `context7.query_docs(...)` are the preferred authoring
surface when several related capabilities come from the same source. Every
capability needs a
canonical qualified id such as `mcp:arch/list_entities`, `cli:gh/issue_list`, or
`py:finance/calculate_nav`. Generated aliases are exposed only when unique and
valid. Collisions must fail loudly or require scoped/canonical access.

## Tool Bridge Modes

Different backends need different ways to expose registered capabilities:

| Bridge | Fits | Shape |
| --- | --- | --- |
| In-process callables | Local unsafe, Monty | Inject async Python callables directly into the execution scope |
| Host callback RPC | Pyodide+Deno, Docker, remote sandboxes | Sandbox calls back to the host/toolplane gateway to invoke MCP tools, CLI wrappers, or host functions |
| Code shipping | Docker, E2B, Modal, Blaxel | Serialize tool definitions or helper modules into the sandbox namespace when they can safely run there |

`toolplane` should support host callback RPC as the general mechanism. It keeps
credentials and local resources in the host while allowing package-capable
sandboxes to orchestrate tools. Code shipping is an optimization for tools that
are safe and useful to move into the sandbox.

The first bridge implementation keeps the payload deliberately small and
JSON-first:

- `ToolCallRequest`: capability name plus params.
- `ToolCallResponse`: either a value or a structured `ToolCallError`.
- `InProcessBridge`: direct registry dispatch for local execution.
- `HttpCallbackBridge`: localhost bearer-token RPC for Pyodide+Deno and later
  sandboxed backends.

## Initial Milestone

Build the smallest useful runtime:

1. A capability registry with names, descriptions, schemas, tags, and callables.
2. Discovery tools: `search`, `get_schema`, and optionally `list_tools`.
3. A backend protocol with a development-only `local_unsafe` implementation.
4. Clear backend capability errors.
5. A Pyodide+Deno backend as the first real sandbox for package-capable Python,
   including pandas-style workflows.
6. A host callback bridge so sandboxed code can call host capabilities.
7. A `cli-to-py` adapter that registers explicit CLI commands as capabilities.
8. An MCP adapter that exposes MCP tools as first-class Python callables.
9. A Docker backend next for real CPython, local CLI binaries, and arbitrary
   system dependencies.
10. Modal, E2B, or Blaxel as remote backends once the local contract is stable.
