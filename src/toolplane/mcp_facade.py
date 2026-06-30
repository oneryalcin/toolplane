"""MCP facade over a configured Toolplane runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from .config import ConfigSource, ToolplaneConfig, load_toolplane_config
from .errors import UnsafeFacadeConfigError
from .runtime import Toolplane

if TYPE_CHECKING:
    from fastmcp import FastMCP

SchemaDetail = Literal["brief", "detailed", "full"]
Transport = Literal["stdio", "http", "sse", "streamable-http"]


def build_mcp_facade(runtime: Toolplane) -> "FastMCP":
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
    if not allow_unsafe:
        _ensure_safe_facade_config(parsed)
    runtime = await Toolplane.from_config(parsed)
    return build_mcp_facade(runtime)


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


def _ensure_safe_facade_config(config: ToolplaneConfig) -> None:
    unsafe: list[str] = []
    if config.toolplane.default_backend == "local_unsafe":
        unsafe.append("toolplane.default_backend = 'local_unsafe'")
    if config.cli.mode == "ambient":
        unsafe.append("cli.mode = 'ambient'")
    if unsafe:
        joined = ", ".join(unsafe)
        raise UnsafeFacadeConfigError(
            "Refusing to serve Toolplane MCP facade with unsafe policy: "
            f"{joined}. Use an explicit safe config or pass --unsafe for trusted "
            "local development."
        )
