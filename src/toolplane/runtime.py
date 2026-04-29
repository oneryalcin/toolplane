"""Public Toolplane runtime."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .backends import CodeBackend, LocalUnsafeBackend
from .discovery import DetailLevel, render_capabilities
from .errors import BackendNotFoundError
from .execution import ExecutionResult
from .registry import CapabilityRegistry


class Toolplane:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry | None = None,
        backends: Sequence[CodeBackend] | None = None,
        default_backend: str = "local_unsafe",
    ) -> None:
        self.registry = registry or CapabilityRegistry()
        configured = list(backends or (LocalUnsafeBackend(),))
        self.backends = {backend.name: backend for backend in configured}
        self.default_backend = default_backend

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
        return await self.registry.call(name, params)

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
        return await runner.run(
            code,
            namespace={"call_tool": self.call_tool},
            inputs=inputs,
            packages=packages,
        )
