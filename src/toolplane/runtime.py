"""Public Toolplane runtime."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .adapters.ambient_cli import (
    AmbientCliPolicy,
    discover_cli_names,
    register_ambient_cli,
)
from .artifacts import ArtifactStore, register_artifact_capabilities
from .audit import AuditLog
from .backends import CodeBackend, LocalUnsafeBackend, MontyBackend, PyodideDenoBackend
from .bridges.in_process import AuditedRunBridge, InProcessBridge
from .capabilities import Capability, JsonSchema
from .discovery import DetailLevel, render_capabilities
from .errors import BackendCapabilityError, BackendNotFoundError
from .execution import ExecutionResult
from .registry import CapabilityRegistry
from .results import ResultStore, register_result_capabilities

if TYPE_CHECKING:
    from .config import ConfigSource


class Toolplane:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry | None = None,
        backends: Sequence[CodeBackend] | None = None,
        default_backend: str = "local_unsafe",
        ambient_cli: bool = True,
        ambient_cli_allowlist: Sequence[str] | None = None,
        result_store: ResultStore | None = None,
        artifact_store: ArtifactStore | None = None,
        audit_log: AuditLog | None = None,
        sessions: bool = True,
        session_max_memory_bytes: int | None = 512 * 1024 * 1024,
    ) -> None:
        if not ambient_cli and ambient_cli_allowlist is not None:
            raise ValueError("ambient_cli_allowlist requires ambient_cli=True")
        self.registry = registry or CapabilityRegistry()
        self.result_store = result_store or ResultStore()
        self.artifact_store = artifact_store or ArtifactStore()
        self.audit_log = audit_log or AuditLog()
        register_result_capabilities(self.registry)
        register_artifact_capabilities(self.registry)
        self.ambient_cli = ambient_cli
        self.cli_policy = AmbientCliPolicy(
            ambient_cli_allowlist, audit_log=self.audit_log
        )
        self._ambient_cli_names: tuple[str, ...] | None = None
        if ambient_cli:
            register_ambient_cli(self.registry)
        self.bridge = InProcessBridge(
            self.registry,
            cli_policy=self.cli_policy,
            result_store=self.result_store,
            artifact_store=self.artifact_store,
        )
        # sessions only apply to the default backend set: a caller passing
        # explicit backends owns their construction (including session mode)
        configured = list(
            backends
            or (
                LocalUnsafeBackend(),
                MontyBackend(
                    session=sessions,
                    session_max_memory_bytes=session_max_memory_bytes,
                ),
                PyodideDenoBackend(),
            )
        )
        self.backends = {backend.name: backend for backend in configured}
        self.default_backend = default_backend

    @classmethod
    async def from_config(
        cls,
        config: ConfigSource,
        *,
        registry: CapabilityRegistry | None = None,
        backends: Sequence[CodeBackend] | None = None,
    ) -> "Toolplane":
        """Build a Toolplane runtime from a validated config or TOML path."""
        from .config import ToolplaneConfig, load_toolplane_config

        parsed = (
            config
            if isinstance(config, ToolplaneConfig)
            else load_toolplane_config(config)
        )
        runtime = cls(
            registry=registry,
            backends=backends,
            default_backend=parsed.toolplane.default_backend,
            ambient_cli=parsed.cli.enabled,
            ambient_cli_allowlist=(
                tuple(parsed.cli.allowed_binaries)
                if parsed.cli.allowed_binaries is not None
                else None
            ),
            result_store=ResultStore.from_settings(parsed.results),
            artifact_store=ArtifactStore.from_settings(parsed.artifacts),
            audit_log=AuditLog.from_settings(parsed.audit),
            sessions=parsed.session.enabled,
            session_max_memory_bytes=parsed.session.max_memory_mb * 1024 * 1024,
        )
        if parsed.mcp.servers:
            await runtime.register_mcp_config(parsed.mcp.to_fastmcp_config())
        return runtime

    def tool(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | frozenset[str] | None = None,
    ) -> Callable[..., Any]:
        def decorator(inner: Callable[..., Any]) -> Callable[..., Any]:
            self.registry.register(
                inner,
                name=name,
                description=description,
                tags=tags,
            )
            return inner

        if fn is not None:
            return decorator(fn)
        return decorator

    def register(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        tags: set[str] | frozenset[str] | None = None,
    ) -> None:
        self.registry.register(fn, name=name, description=description, tags=tags)

    def register_python_namespace(
        self,
        name: str,
        tools: Mapping[str, Callable[..., Any]],
        *,
        tags: set[str] | frozenset[str] | None = None,
    ) -> list[Capability]:
        """Register host Python helpers under a scoped code-mode namespace."""
        from .adapters.python import register_python_namespace

        return register_python_namespace(
            self.registry,
            name,
            tools,
            tags=tags,
        )

    def register_cli(
        self,
        name: str,
        command: Any,
        *,
        subcommand: str | None = None,
        description: str | None = None,
        parameters: JsonSchema | None = None,
        tags: set[str] | frozenset[str] | None = None,
    ) -> Capability:
        """Register an explicit cli-to-py command as a capability."""
        from .adapters.cli_to_py import register_cli

        return register_cli(
            self.registry,
            name,
            command,
            subcommand=subcommand,
            description=description,
            parameters=parameters,
            tags=tags,
        )

    async def register_mcp(
        self,
        name: str,
        server: Any,
        *,
        tags: set[str] | frozenset[str] | None = None,
    ) -> list[Capability]:
        """Register tools from a FastMCP-compatible server/client transport."""
        from .adapters.mcp import register_mcp_server

        return await register_mcp_server(
            self.registry,
            name,
            server,
            tags=tags,
        )

    async def register_mcp_config(
        self,
        config: Any,
        *,
        tags: set[str] | frozenset[str] | None = None,
    ) -> list[Capability]:
        """Register all tools from an `mcpServers` config dictionary."""
        from .adapters.mcp import register_mcp_config

        return await register_mcp_config(
            self.registry,
            config,
            tags=tags,
        )

    async def search(
        self,
        query: str,
        *,
        tags: set[str] | frozenset[str] | None = None,
        detail: DetailLevel = "brief",
        limit: int | None = None,
    ) -> str:
        capabilities = self.registry.search(query, tags=tags, limit=limit)
        if not capabilities and detail != "full":
            # a no-match must be a signpost, not a dead end: agents that
            # miss on vocabulary need a browse path, not silence
            total = len(self.registry.all())
            if not total:
                return "No capabilities are registered."
            noun = "capability is" if total == 1 else "capabilities are"
            return (
                f"No capabilities matched the query. {total} {noun} "
                "registered — search again with an empty query to list "
                "them all, or read the toolplane://namespace resource "
                "for the full execution namespace."
            )
        return render_capabilities(capabilities, detail=detail)

    async def list_tools(self, *, detail: DetailLevel = "brief") -> str:
        return render_capabilities(self.registry.all(), detail=detail)

    def describe_namespace(self) -> str:
        """Render a live manifest of the execute_code Python namespace.

        This is the browse path for surfaces that have no searchable
        registry entry — flat CLI bindings and the result store exist only
        in-sandbox, so without this manifest an agent can discover them
        only by guessing names (which a live cold-discovery test showed
        does not work).
        """
        lines = [
            "# Toolplane execution namespace",
            "",
            "Python passed to `execute_code` runs against these bindings.",
            "Every binding is async — always `await` it.",
            "",
            "## Capability functions",
        ]
        flat: dict[str, list[str]] = {}
        for callable_name, canonical in self.registry.callable_namespace().items():
            flat.setdefault(canonical, []).append(callable_name)
        if flat:
            for canonical in sorted(flat):
                names = " / ".join(
                    f"`await {name}(...)`" for name in sorted(flat[canonical])
                )
                lines.append(f"- {names} — {canonical}")
        else:
            lines.append("*(no flat capability bindings)*")
        scoped = self.registry.scoped_namespace()
        if scoped:
            lines.append("")
            lines.append("Scoped namespaces:")
            for namespace, members in scoped.items():
                for member, canonical in members.items():
                    lines.append(
                        f"- `await {namespace}.{member}(...)` — {canonical}"
                    )
        lines.extend(
            [
                "",
                "Every capability, including ones without a binding above, is "
                'callable as `await call_tool("canonical:name", {...params})`. '
                "Use `search_capabilities` / `get_capability_schemas` for "
                "parameter schemas.",
                "",
                "## CLI",
            ]
        )
        if self.ambient_cli:
            names = self._get_ambient_cli_names()
            if self.cli_policy.restricted:
                lines.append(f"Allowed binaries: {', '.join(names)}")
            else:
                lines.append(
                    f"{len(names)} binaries discovered on PATH are bound by name."
                )
            lines.extend(
                [
                    "",
                    "- Each allowed binary is bound as a flat async function: "
                    "subcommand as the first positional argument, flags as "
                    "keyword arguments — e.g. "
                    "`await git('log', oneline=True, max_count=3)`.",
                    "- Awaiting returns `{'stdout', 'stderr', 'exit_code', 'ok'}`.",
                    "- For a binary whose name is not a valid Python "
                    "identifier, use `await cli_run(binary, subcommand, "
                    "flag=value, ...)` (monty backend; a positional options "
                    "dict also works) or the `cli` namespace object "
                    "(local/pyodide backends).",
                    "- Binaries outside the allowlist have no binding — "
                    "calling one raises NameError — and `cli_run` rejects "
                    "them by policy.",
                ]
            )
            if self.cli_policy.escalation_available and self.cli_policy.restricted:
                lines.append(
                    "- If you need a binary outside the allowlist, call it "
                    "through `cli_run` (or the `cli` object): the server "
                    "pauses and asks the human operator to allow it for "
                    "this session, once per binary. If granted, it also "
                    "gains a flat binding in later runs; if refused, you "
                    "get the policy error — use an allowed binary instead "
                    "of retrying."
                )
        else:
            lines.append("CLI access is disabled in this configuration.")
        if self._session_default():
            lines.extend(
                [
                    "",
                    "## Session",
                    "Variables persist across execute_code runs on the "
                    "default backend: assignments and function definitions "
                    "from one run are simply available in the next — no "
                    "save/load step needed.",
                    "",
                    "- A failed run keeps the session: prior variables (and "
                    "statements completed before the error) persist.",
                    "- A timed-out run is rolled back: the namespace returns "
                    "to its pre-run state, but capability calls, CLI "
                    "commands, and saves the run already made stand.",
                    "- `await reset_session()` clears all session variables "
                    "after the current run (saved results and artifacts are "
                    "unaffected).",
                    "- The session has a memory cap; if a run fails with "
                    "MemoryError, reassign large variables (`big = None` — "
                    "monty has no `del`) or reset.",
                    "- Assigning a variable named after a Toolplane binding "
                    "(e.g. `save_result = ...`) is rejected: in a session "
                    "the assignment would mask the binding until reset.",
                ]
            )
        lines.extend(["", "## Result store"])
        if self.result_store.enabled:
            if self._session_default():
                lines.append(
                    "Session variables already persist between runs; use "
                    "the store when a value must survive a session reset, "
                    "cross a backend override, or be read directly as an "
                    "MCP resource."
                )
            lines.extend(
                [
                    "- `handle = await save_result(value)` — JSON-shaped "
                    "values only",
                    "- `value = await load_result(handle)`",
                    "- A saved value is also readable directly as the MCP "
                    "resource `toolplane://results/<handle>` (canonical "
                    "JSON, no execute_code run needed).",
                    "",
                    "Handles persist across execute_code calls within this "
                    "server session; nothing persists to disk.",
                ]
            )
        else:
            lines.append("The result store is disabled in this configuration.")
        lines.extend(["", "## Artifact store"])
        if self.artifact_store.enabled:
            lines.extend(
                [
                    "- `handle = await save_artifact(data, "
                    'filename="report.parquet")` — bytes only (files, '
                    "images, parquet); JSON-shaped values belong in the "
                    "result store",
                    "- `data = await load_artifact(handle)`",
                    "- A saved artifact is also readable directly as the "
                    "binary MCP resource `toolplane://artifacts/<handle>`; "
                    "the execute_code response lists each artifact saved "
                    "during the run with its URI.",
                    "",
                    "Artifacts live on host disk for this server session "
                    "only and are deleted when the session ends.",
                ]
            )
        else:
            lines.append(
                "The artifact store is disabled in this configuration."
            )
        return "\n".join(lines)

    def as_tool(
        self,
        *,
        name: str = "run_code",
        description: str | None = None,
        backend: str | None = None,
        packages: Sequence[str] = (),
    ) -> Callable[[str], Any]:
        """Return a single async ``run_code`` tool for embedding in agent frameworks.

        The returned object is a plain async function with a docstring, which
        is the shape pydantic-ai (``Agent(tools=[...])``), the OpenAI Agents
        SDK (``function_tool(...)``), and LangChain/LangGraph (``tool(...)``)
        all accept directly. Call this AFTER registering capabilities: the
        docstring is the agent's only discovery channel in embedded mode, and
        it is generated from the namespace at call time.

        The generated description is deliberately compact — OpenAI's API
        rejects tool descriptions over ~1024 characters, and the full
        manifest does not fit. Pass ``description=`` to override it (e.g.
        with ``describe_namespace()`` for clients without that limit).

        Backend resolution is safe by default: the tool never inherits
        ``local_unsafe`` implicitly, because here the code author is a
        model, not the developer. ``backend=None`` uses the runtime default
        unless that default is ``local_unsafe``, in which case monty is
        used; pass ``backend="local_unsafe"`` explicitly to opt in.
        """
        resolved_backend = backend or self.default_backend
        if backend is None and resolved_backend == "local_unsafe":
            resolved_backend = "monty"
        runner = self.backends.get(resolved_backend)
        if runner is None:
            # misconfiguration fails at construction, not on the agent's
            # first tool call
            raise BackendNotFoundError(f"Unknown backend: {resolved_backend}")
        fixed_packages = tuple(packages)
        if fixed_packages and not runner.capabilities.third_party_packages:
            raise BackendCapabilityError(
                f"backend '{resolved_backend}' cannot install packages; "
                "use pyodide-deno for package workflows"
            )
        doc = description or self._compact_tool_description(
            session=bool(getattr(runner, "session", False))
        )

        async def run_code(code: str) -> dict[str, Any]:
            result = await self.execute(
                code, backend=resolved_backend, packages=fixed_packages
            )
            return result.model_dump(mode="json")

        run_code.__name__ = name
        run_code.__qualname__ = name
        run_code.__doc__ = doc
        return run_code

    def _compact_tool_description(
        self, *, limit: int = 1000, session: bool | None = None
    ) -> str:
        """Namespace summary that fits inside strict tool-description caps."""
        if session is None:
            session = self._session_default()
        lines = [
            "Execute Python against a curated tool namespace. The snippet "
            "body runs inside an async function: await every namespace "
            "call, `return` a JSON-shaped value. Returns "
            "{value, stdout, stderr, error, artifacts}.",
        ]
        namespace_map = self.registry.callable_namespace()
        flat = sorted(namespace_map)
        if flat:
            shown = ", ".join(flat[:12])
            more = f", ... ({len(flat)} total)" if len(flat) > 12 else ""
            lines.append(f"Capabilities: await {flat[0]}(...) etc — {shown}{more}.")
            # the call_tool example must be a REAL canonical id: a live #80
            # run showed a model reading a "canonical:name" placeholder as
            # a literal scheme and inventing canonical:git
            lines.append(
                f'Any capability: await call_tool("{namespace_map[flat[0]]}", '
                "{...params})."
            )
        else:
            lines.append(
                'Any capability: await call_tool("<canonical id>", {...params}).'
            )
        if self.ambient_cli:
            names = self._get_ambient_cli_names()
            if names:
                shown = ", ".join(names[:8]) + (
                    f", ... ({len(names)} total)" if len(names) > 8 else ""
                )
                # the example verb must be a binary that actually works
                # here — a hardcoded one misdirects every non-git allowlist
                lines.append(
                    f"CLI (subcommand first, flags as kwargs): await "
                    f"{names[0]}(...) -> {{stdout, stderr, exit_code, ok}}. "
                    f"Allowed: {shown}."
                )
            else:
                lines.append("CLI: no binaries are allowed.")
        if session:
            lines.append(
                "Variables persist across calls (session); await "
                "reset_session() clears them. A timed-out run rolls the "
                "namespace back."
            )
        if self.result_store.enabled and not session:
            lines.append(
                "State between runs: handle = await save_result(value); "
                "await load_result(handle)."
            )
        if self.artifact_store.enabled:
            lines.append(
                "Bytes between runs: await save_artifact(data, "
                'filename="x.csv"); await load_artifact(handle).'
            )
        lines.append(
            "Failures raise real Python exceptions: store errors are "
            "ValueError, CLI policy is PermissionError, unknown "
            "capabilities are LookupError — catch by type to retry."
        )
        text = "\n".join(lines)
        if len(text) > limit:
            text = text[: limit - 3] + "..."
        return text

    async def get_schema(
        self,
        tools: Sequence[str],
        *,
        detail: DetailLevel = "detailed",
    ) -> str:
        capabilities, missing = self.registry.schemas(tools)
        return render_capabilities(capabilities, detail=detail, missing=missing)

    async def call_tool(self, name: str, params: dict[str, Any] | None = None) -> Any:
        if self.audit_log.enabled:
            # direct host calls are audited too, just without a run_id
            return await AuditedRunBridge(
                self.bridge, self.audit_log, None
            ).call_tool(name, params)
        return await self.bridge.call_tool(name, params)

    async def execute(
        self,
        code: str,
        *,
        backend: str | None = None,
        inputs: dict[str, Any] | None = None,
        packages: Sequence[str] = (),
    ) -> ExecutionResult:
        backend_name = backend or self.default_backend
        runner = self.backends.get(backend_name)
        if runner is None:
            raise BackendNotFoundError(f"Unknown backend: {backend_name}")
        # correlation is explicit and per-run: the run_id rides a bridge
        # wrapper into every dispatch path (a shared current-run slot was
        # tried first and misattributed events across overlapping runs —
        # reproduced independently by two #82 reviewers)
        run_id = AuditLog.new_run_id() if self.audit_log.enabled else None
        run_bridge: Any = self.bridge
        if self.audit_log.enabled:
            run_bridge = AuditedRunBridge(self.bridge, self.audit_log, run_id)
        run_kwargs: dict[str, Any] = {
            "bridge": run_bridge,
            "inputs": inputs,
            "packages": packages,
            "namespace": self.registry.callable_namespace(),
            "scoped_namespace": self.registry.scoped_namespace(),
            "ambient_cli": self.ambient_cli,
            "ambient_cli_names": self._get_ambient_cli_names(),
        }
        if _backend_accepts_run_kwarg(runner, "ambient_cli_allowed_binaries"):
            if self.cli_policy.escalation_handler is not None:
                # binding-layer pre-checks (local constructors, the pyodide
                # in-sandbox check) would refuse before the bridge can ask
                # the human; when escalation is live the bridge is the sole
                # policy authority
                run_kwargs["ambient_cli_allowed_binaries"] = None
            else:
                effective = self.cli_policy.effective_allowlist()
                run_kwargs["ambient_cli_allowed_binaries"] = (
                    tuple(sorted(effective)) if effective is not None else None
                )
        before = (
            set(self.artifact_store.handles())
            if self.artifact_store.enabled
            else set()
        )
        self.audit_log.emit(
            "run_start",
            run_id=run_id,
            backend=backend_name,
            code_sha256=hashlib.sha256(code.encode()).hexdigest()[:12],
            code_chars=len(code),
        )
        run_started = self.audit_log.timer()
        raised: BaseException | None = None
        try:
            result = await runner.run(code, **run_kwargs)
        except BaseException as exc:
            raised = exc
        # escalations must not outlive the run that asked: a backend
        # timeout leaves the dispatch coroutine (and its open human
        # prompt) running detached, and a late answer would mutate
        # session policy invisibly (found live in #71 certification)
        interrupted = self.cli_policy.cancel_pending_escalations()
        if raised is not None:
            # same run_end shape as the success path: jq consumers key on
            # these fields (reviewer finding on #82)
            self.audit_log.emit(
                "run_end",
                run_id=run_id,
                backend=backend_name,
                duration_ms=self.audit_log.elapsed_ms(run_started),
                ok=False,
                error_type=type(raised).__name__,
                artifacts_saved=0,
                escalations_cancelled=list(interrupted),
            )
            raise raised
        if interrupted and result.error is not None:
            binaries = ", ".join(interrupted)
            base = result.error.message.rstrip()
            if base and not base.endswith((".", "!", "?")):
                base += "."
            result = result.model_copy(
                update={
                    "error": result.error.model_copy(
                        update={
                            "message": (
                                f"{base} The run was still waiting for a "
                                f"human decision on: {binaries}. That "
                                "request was cancelled with the run — "
                                "execute again to re-prompt."
                            )
                        }
                    )
                }
            )
        if self.artifact_store.enabled:
            # agents never enumerate resources unaided — the handle and URI
            # must arrive in the response they are already reading
            saved = [
                self.artifact_store.describe(handle)
                | {"uri": f"toolplane://artifacts/{handle}"}
                for handle in self.artifact_store.handles()
                if handle not in before
            ]
            if saved:
                result = result.model_copy(update={"artifacts": tuple(saved)})
        self.audit_log.emit(
            "run_end",
            run_id=run_id,
            backend=result.backend or backend_name,
            duration_ms=self.audit_log.elapsed_ms(run_started),
            ok=result.error is None,
            error_type=result.error.type if result.error else None,
            artifacts_saved=len(result.artifacts),
            escalations_cancelled=list(interrupted),
        )
        return result

    def _session_default(self) -> bool:
        """True when default execute_code runs land in a persistent session.

        Read from the live backend instance, not construction args, so the
        manifest cannot lie when a caller supplied their own backends.
        """
        backend = self.backends.get(self.default_backend)
        return bool(getattr(backend, "session", False))

    def _get_ambient_cli_names(self) -> tuple[str, ...]:
        if not self.ambient_cli:
            return ()
        effective = self.cli_policy.effective_allowlist()
        if effective is not None:
            # includes session escalation grants, so a granted binary gains
            # a flat binding (and a manifest entry) in later runs
            return tuple(sorted(effective))
        if self._ambient_cli_names is None:
            self._ambient_cli_names = discover_cli_names()
        return self._ambient_cli_names


def _backend_accepts_run_kwarg(runner: CodeBackend, name: str) -> bool:
    signature = inspect.signature(runner.run)
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return name in signature.parameters
