"""MCP facade over a configured Toolplane runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from .config import ConfigSource, load_toolplane_config
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
            return ExecutionResult(
                backend=backend or "",
                error=ExecutionError(
                    type="BackendPolicyError",
                    message=(
                        f"Backend override '{backend}' is not allowed by "
                        "Toolplane MCP facade policy. Pass --unsafe only for "
                        "trusted local development."
                    ),
                ),
            ).model_dump(mode="json")
        result = await runtime.execute(
            code,
            backend=backend,
            inputs=inputs,
            packages=tuple(packages or ()),
        )
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


async def serve_mcp_facade(
    config: ConfigSource,
    *,
    transport: Transport = "stdio",
    host: str | None = None,
    port: int | None = None,
    allow_unsafe: bool = False,
) -> None:
    app = await build_mcp_facade_from_config(config, allow_unsafe=allow_unsafe)
    kwargs: dict[str, Any] = {}
    if host is not None:
        kwargs["host"] = host
    if port is not None:
        kwargs["port"] = port
    await app.run_async(transport=transport, show_banner=False, **kwargs)
