"""MCP facade over a configured Toolplane runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from .config import ConfigSource, ToolplaneConfig, load_toolplane_config
from .errors import BackendNotFoundError
from .execution import ExecutionError, ExecutionResult
from .policy import EffectivePolicy, ensure_safe_facade_policy
from .runtime import Toolplane

if TYPE_CHECKING:
    from fastmcp import FastMCP

SchemaDetail = Literal["brief", "detailed", "full"]
Transport = Literal["stdio", "http", "sse", "streamable-http"]


def build_mcp_facade(
    runtime: Toolplane,
    *,
    policy: EffectivePolicy | None = None,
) -> "FastMCP":
    """Build the small MCP meta-tool surface for a Toolplane runtime."""
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - dependency is required
        raise ImportError(
            "Toolplane MCP facade requires FastMCP. Install Toolplane with "
            "its dependencies or add `fastmcp` to the environment."
        ) from exc

    mcp = FastMCP(
        "Toolplane",
        instructions=(
            "Discover capabilities, inspect schemas, then execute Python "
            "against the configured Toolplane namespace."
        ),
    )

    @mcp.tool
    async def search_capabilities(
        query: str,
        tags: list[str] | None = None,
        detail: SchemaDetail = "brief",
        limit: int | None = None,
    ) -> str:
        """Search the configured Toolplane capability registry."""
        return await runtime.search(
            query,
            tags=frozenset(tags or ()),
            detail=detail,
            limit=limit,
        )

    @mcp.tool
    async def get_capability_schemas(
        names: list[str],
        detail: SchemaDetail = "detailed",
    ) -> str:
        """Return schemas for selected Toolplane capabilities."""
        return await runtime.get_schema(names, detail=detail)

    @mcp.tool
    async def execute_code(
        code: str,
        backend: str | None = None,
        inputs: dict[str, Any] | None = None,
        packages: list[str] | None = None,
    ) -> dict[str, Any]:
        """Execute Python against the configured Toolplane namespace."""
        if (
            policy is not None
            and backend is not None
            and policy.allowed_backend_overrides is not None
            and backend not in policy.allowed_backend_overrides
        ):
            if backend in runtime.backends:
                allowed = ", ".join(sorted(policy.allowed_backend_overrides))
                error = ExecutionError(
                    type="BackendPolicyError",
                    message=(
                        f"Backend '{backend}' exists but is blocked by "
                        "Toolplane MCP facade policy. Allowed backend "
                        f"overrides: {allowed}. Pass --unsafe only for "
                        "trusted local development."
                    ),
                )
            else:
                valid = ", ".join(sorted(runtime.backends))
                error = ExecutionError(
                    type="BackendNotFoundError",
                    message=(
                        f"Unknown backend '{backend}'. "
                        f"Valid backends: {valid}."
                    ),
                )
            return ExecutionResult(
                backend=backend or "",
                error=error,
            ).model_dump(mode="json")
        try:
            result = await runtime.execute(
                code,
                backend=backend,
                inputs=inputs,
                packages=tuple(packages or ()),
            )
        except BackendNotFoundError:
            # reachable when the configured default backend is unknown, or an
            # unknown override slips past a permissive (--unsafe) policy
            valid = ", ".join(sorted(runtime.backends))
            requested = backend or runtime.default_backend
            return ExecutionResult(
                backend=requested,
                error=ExecutionError(
                    type="BackendNotFoundError",
                    message=(
                        f"Unknown backend '{requested}'. "
                        f"Valid backends: {valid}."
                    ),
                ),
            ).model_dump(mode="json")
        return result.model_dump(mode="json")

    return mcp


async def build_mcp_facade_from_config(
    config: ConfigSource,
    *,
    allow_unsafe: bool = False,
) -> "FastMCP":
    parsed = load_toolplane_config(config)
    policy = EffectivePolicy.from_config(parsed, allow_unsafe=allow_unsafe)
    ensure_safe_facade_policy(policy)
    runtime = await Toolplane.from_config(parsed)
    return build_mcp_facade(runtime, policy=policy)


def resolve_serve_config(
    config: ToolplaneConfig,
    transport: Transport,
) -> ToolplaneConfig:
    """Apply transport-dependent policy before building the runtime.

    The result store is session-scoped, and only stdio guarantees one client
    per process. Multi-client transports fail closed: the store is disabled
    rather than shared across clients.
    """
    if transport == "stdio" or not config.results.enabled:
        return config
    updated = config.model_copy(deep=True)
    updated.results.enabled = False
    return updated


async def serve_mcp_facade(
    config: ConfigSource,
    *,
    transport: Transport = "stdio",
    host: str | None = None,
    port: int | None = None,
    allow_unsafe: bool = False,
) -> None:
    parsed = resolve_serve_config(load_toolplane_config(config), transport)
    app = await build_mcp_facade_from_config(parsed, allow_unsafe=allow_unsafe)
    kwargs: dict[str, Any] = {}
    if host is not None:
        kwargs["host"] = host
    if port is not None:
        kwargs["port"] = port
    await app.run_async(transport=transport, show_banner=False, **kwargs)
