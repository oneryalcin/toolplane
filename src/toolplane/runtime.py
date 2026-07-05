"""Public Toolplane runtime."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .adapters.ambient_cli import discover_cli_names, register_ambient_cli
from .artifacts import ArtifactStore, register_artifact_capabilities
from .backends import CodeBackend, LocalUnsafeBackend, MontyBackend, PyodideDenoBackend
from .bridges.in_process import InProcessBridge
from .capabilities import Capability, JsonSchema
from .discovery import DetailLevel, render_capabilities
from .errors import BackendNotFoundError
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
    ) -> None:
        if not ambient_cli and ambient_cli_allowlist is not None:
            raise ValueError("ambient_cli_allowlist requires ambient_cli=True")
        self.registry = registry or CapabilityRegistry()
        self.result_store = result_store or ResultStore()
        self.artifact_store = artifact_store or ArtifactStore()
        register_result_capabilities(self.registry)
        register_artifact_capabilities(self.registry)
        self.ambient_cli = ambient_cli
        self._ambient_cli_allowed_binaries = (
            frozenset(ambient_cli_allowlist)
            if ambient_cli_allowlist is not None
            else None
        )
        self._ambient_cli_names: tuple[str, ...] | None = None
        if ambient_cli:
            register_ambient_cli(self.registry)
        self.bridge = InProcessBridge(
            self.registry,
            ambient_cli_allowed_binaries=self._ambient_cli_allowed_binaries,
            result_store=self.result_store,
            artifact_store=self.artifact_store,
        )
        configured = list(
            backends
            or (LocalUnsafeBackend(), MontyBackend(), PyodideDenoBackend())
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
            if self._ambient_cli_allowed_binaries is not None:
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
        else:
            lines.append("CLI access is disabled in this configuration.")
        lines.extend(["", "## Result store"])
        if self.result_store.enabled:
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

    async def get_schema(
        self,
        tools: Sequence[str],
        *,
        detail: DetailLevel = "detailed",
    ) -> str:
        capabilities, missing = self.registry.schemas(tools)
        return render_capabilities(capabilities, detail=detail, missing=missing)

    async def call_tool(self, name: str, params: dict[str, Any] | None = None) -> Any:
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
        run_kwargs: dict[str, Any] = {
            "bridge": self.bridge,
            "inputs": inputs,
            "packages": packages,
            "namespace": self.registry.callable_namespace(),
            "scoped_namespace": self.registry.scoped_namespace(),
            "ambient_cli": self.ambient_cli,
            "ambient_cli_names": self._get_ambient_cli_names(),
        }
        if _backend_accepts_run_kwarg(runner, "ambient_cli_allowed_binaries"):
            run_kwargs["ambient_cli_allowed_binaries"] = (
                tuple(sorted(self._ambient_cli_allowed_binaries))
                if self._ambient_cli_allowed_binaries is not None
                else None
            )
        before = (
            set(self.artifact_store.handles())
            if self.artifact_store.enabled
            else set()
        )
        result = await runner.run(code, **run_kwargs)
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
        return result

    def _get_ambient_cli_names(self) -> tuple[str, ...]:
        if not self.ambient_cli:
            return ()
        if self._ambient_cli_allowed_binaries is not None:
            return tuple(sorted(self._ambient_cli_allowed_binaries))
        if self._ambient_cli_names is None:
            self._ambient_cli_names = discover_cli_names()
        return self._ambient_cli_names


def _backend_accepts_run_kwarg(runner: CodeBackend, name: str) -> bool:
    signature = inspect.signature(runner.run)
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return name in signature.parameters
