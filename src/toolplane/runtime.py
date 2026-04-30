"""Public Toolplane runtime."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .adapters.ambient_cli import discover_cli_names, register_ambient_cli
from .backends import CodeBackend, LocalUnsafeBackend, PyodideDenoBackend
from .bridges.in_process import InProcessBridge
from .capabilities import Capability, JsonSchema
from .discovery import DetailLevel, render_capabilities
from .errors import BackendNotFoundError
from .execution import ExecutionResult
from .registry import CapabilityRegistry

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
    ) -> None:
        if not ambient_cli and ambient_cli_allowlist is not None:
            raise ValueError("ambient_cli_allowlist requires ambient_cli=True")
        self.registry = registry or CapabilityRegistry()
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
        )
        configured = list(backends or (LocalUnsafeBackend(), PyodideDenoBackend()))
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
        return render_capabilities(capabilities, detail=detail)

    async def list_tools(self, *, detail: DetailLevel = "brief") -> str:
        return render_capabilities(self.registry.all(), detail=detail)

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
        return await runner.run(code, **run_kwargs)

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
