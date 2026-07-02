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
            "Discover capabilities with search_capabilities (an empty query "
            "lists everything), inspect schemas with get_capability_schemas, "
            "then execute Python with execute_code. The execution namespace "
            "also has surfaces that are not registry capabilities: flat CLI "
            "bindings for allowed binaries and save_result/load_result for "
            "passing JSON-shaped data between runs. Read the "
            "toolplane://namespace resource for the full namespace with "
            "call shapes."
        ),
    )

    @mcp.resource(
        "toolplane://namespace",
        name="namespace",
        description=(
            "Live manifest of the execute_code Python namespace: capability "
            "functions, CLI bindings, result store, and their call shapes."
        ),
        mime_type="text/markdown",
    )
    def namespace_manifest() -> str:
        return runtime.describe_namespace()

    # advertised only when the store is live: resolve_serve_config disables
    # the store on multi-client transports before the facade is built, and a
    # dead template would be a signpost to nowhere
    if runtime.result_store.enabled:

        @mcp.resource(
            "toolplane://results/{handle}",
            name="results",
            description=(
                "A value saved with save_result, served as canonical JSON. "
                "Read toolplane://results/<handle> with the handle returned "
                "by save_result."
            ),
            mime_type="application/json",
        )
        def result_resource(handle: str) -> str:
            # verbatim canonical JSON; fastmcp labels str reads text/plain
            # (template listing still says application/json) — content
            # contract beats the cosmetic read-time mime label
            return runtime.result_store.payload(handle)

    @mcp.tool
    async def search_capabilities(
        query: str,
        tags: list[str] | None = None,
        detail: SchemaDetail = "brief",
        limit: int | None = None,
    ) -> str:
        """Search the Toolplane capability registry by keyword.

        Matching is exact-word, not fuzzy: if nothing matches, retry with
        different words, or pass an empty query to list every capability.
        Before writing code with execute_code, read the
        toolplane://namespace resource — it documents every binding in the
        execution namespace with call shapes and gotchas, including
        surfaces (CLI, result store) that this search does not cover.
        """
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
        """Return parameter schemas for the named Toolplane capabilities.

        Names must be canonical, exactly as returned by
        search_capabilities (e.g. "mcp:server/tool") — guessed or
        abbreviated names will not resolve.
        """
        return await runtime.get_schema(names, detail=detail)

    @mcp.tool
    async def execute_code(
        code: str,
        backend: str | None = None,
        inputs: dict[str, Any] | None = None,
        packages: list[str] | None = None,
    ) -> dict[str, Any]:
        """Execute Python against the configured Toolplane namespace.

        Read the toolplane://namespace resource BEFORE writing code — it
        documents every binding with exact call shapes; guessing shapes
        fails. The namespace binds capability functions, flat CLI
        functions for allowed binaries, and save_result/load_result for
        passing JSON-shaped data between runs. Every binding is async —
        always `await` it.
        """
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
    transport: Transport = "stdio",
    allow_unsafe: bool = False,
) -> "FastMCP":
    """Build the facade from config, applying transport-dependent policy.

    The transport decision lives here, not only in serve_mcp_facade, so an
    embedder building from config for a multi-client transport gets the
    fail-closed store (and no results resource template) without having to
    know about resolve_serve_config.
    """
    parsed = resolve_serve_config(load_toolplane_config(config), transport)
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
    app = await build_mcp_facade_from_config(
        config, transport=transport, allow_unsafe=allow_unsafe
    )
    kwargs: dict[str, Any] = {}
    if host is not None:
        kwargs["host"] = host
    if port is not None:
        kwargs["port"] = port
    await app.run_async(transport=transport, show_banner=False, **kwargs)
