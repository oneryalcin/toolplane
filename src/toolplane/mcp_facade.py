"""MCP facade over a configured Toolplane runtime."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

# module-level because pydantic must resolve the deferred `ctx: Context`
# annotation on execute_code; the friendly ImportError for missing fastmcp
# still fires in build_mcp_facade before anything is used
try:
    from fastmcp import Context
except ImportError:  # pragma: no cover - dependency is required
    Context = None  # type: ignore[assignment, misc]

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
    cli_escalation: bool = True,
) -> "FastMCP":
    """Build the small MCP meta-tool surface for a Toolplane runtime.

    Multi-client caution: monty sessions, the result/artifact stores, and
    CLI escalation grants are all per-process state. Only the config-driven
    path (``build_mcp_facade_from_config`` / ``serve mcp``) disables them
    per transport; callers building the facade directly for http/sse must
    construct ``Toolplane(sessions=False)`` (and disabled stores) and pass
    ``cli_escalation=False``, or one client's variables, handles, and
    grants become every client's.
    """
    try:
        from fastmcp import FastMCP
        from fastmcp.server.providers.skills import SkillsDirectoryProvider
    except ImportError as exc:  # pragma: no cover - dependency is required
        raise ImportError(
            "Toolplane MCP facade requires FastMCP. Install Toolplane with "
            "its dependencies or add `fastmcp` to the environment."
        ) from exc

    # Escalation is only meaningful when there is a policy to escalate past;
    # the flag is durable (the manifest advertises the affordance) while the
    # handler itself is installed per-request, because ctx.elicit needs the
    # requesting client's context — on the pyodide path the dispatch runs
    # outside the request's contextvars, so the context must travel by
    # closure, not lookup. Grants live on the shared runtime policy, so the
    # caller must pass cli_escalation=False on multi-client transports —
    # otherwise one client's approval would let another client run the
    # binary (build_mcp_facade_from_config does this per transport).
    cli_escalation = (
        cli_escalation and runtime.ambient_cli and runtime.cli_policy.restricted
    )
    if cli_escalation:
        runtime.cli_policy.escalation_available = True

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
            "call shapes, and the skill://driving-toolplane/SKILL.md "
            "resource for snippet conventions and usage guidance."
        ),
    )

    # read-only usage guidance, versioned with the code it describes;
    # always on — it is metadata, not a capability surface
    mcp.add_provider(SkillsDirectoryProvider(Path(__file__).parent / "skills"))

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

    if runtime.artifact_store.enabled:

        @mcp.resource(
            "toolplane://artifacts/{handle}",
            name="artifacts",
            description=(
                "An artifact saved with save_artifact, served as binary. "
                "Read toolplane://artifacts/<handle> with the handle from "
                "save_artifact or the execute_code response's artifacts "
                "list."
            ),
            mime_type="application/octet-stream",
        )
        def artifact_resource(handle: str) -> bytes:
            return runtime.artifact_store.load(handle)

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
        ctx: Context | None = None,
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
        if cli_escalation and ctx is not None:
            request_context = ctx

            # ctx.elicit resolves its session through the MCP SDK's
            # request_ctx contextvar, not through the ctx object — and the
            # pyodide RPC path dispatches from a callback thread whose
            # coroutine context has that var unset. Capture the value here,
            # inside the request, so the handler can re-seat it (empirically
            # required: without this, pyodide escalation fails closed).
            from mcp.server.lowlevel.server import request_ctx

            captured_request_ctx = request_ctx.get()

            async def elicit_cli_grant(binary: str) -> bool:
                token = request_ctx.set(captured_request_ctx)
                try:
                    return await _elicit_cli_grant(
                        request_context, runtime.cli_policy, binary
                    )
                finally:
                    request_ctx.reset(token)

            # shared attribute: concurrent execute_code calls can clear each
            # other's handler, which degrades to the plain refusal —
            # fail-closed, and stdio serves one client anyway
            runtime.cli_policy.escalation_handler = elicit_cli_grant
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
        finally:
            if cli_escalation:
                runtime.cli_policy.escalation_handler = None
        return result.model_dump(mode="json")

    return mcp


async def _elicit_cli_grant(ctx: Any, policy: Any, binary: str) -> bool:
    """Ask the human, via MCP elicitation, to allow a blocked CLI binary.

    Returns True only on an explicit accept-with-"allow". Decline, cancel,
    unsupported clients (ctx.elicit raises), and malformed answers all
    return False, which the policy turns into the standard refusal — the
    caller (AmbientCliPolicy.ensure_allowed) also catches exceptions, so
    this can never introduce a failure mode that did not exist before.
    The schema is a flat string enum: the most conservative shape the
    2026-07 client probes found renderable.
    """
    allowed = ", ".join(sorted(policy.effective_allowlist() or ())) or "none"
    result = await ctx.elicit(
        f"The running snippet wants to execute the CLI binary '{binary}', "
        f"which is outside the Toolplane allowlist (currently: {allowed}). "
        "Allow it for the rest of this server session?",
        response_type=["allow", "deny"],
    )
    return (
        result.action == "accept"
        and getattr(result, "data", None) == "allow"
    )


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
    # a served process has no human at a browser: an unprimed direct-OAuth
    # server would block startup for the whole OAuth callback timeout
    # (~5 min) and then crash (reviewer finding on #95) — fail fast with
    # the command that fixes it instead
    from .credentials import CredentialStorageError, has_stored_oauth_tokens

    for server_name, server_config in parsed.mcp.servers.items():
        if (
            server_config.get("url")
            and server_config.get("auth") == "oauth"
            and not await has_stored_oauth_tokens(str(server_config["url"]))
        ):
            raise CredentialStorageError(
                f"MCP server {server_name!r} requires OAuth login before "
                f"serving (a server process cannot open a browser) — run: "
                f"toolplane mcp login {server_name}"
            )
    runtime = await Toolplane.from_config(parsed)
    # escalation grants are session-scoped state like the stores: only stdio
    # guarantees one client per process, so a human approval cannot leak to
    # a client that never saw the prompt
    return build_mcp_facade(
        runtime, policy=policy, cli_escalation=transport == "stdio"
    )


def resolve_serve_config(
    config: ToolplaneConfig,
    transport: Transport,
) -> ToolplaneConfig:
    """Apply transport-dependent policy before building the runtime.

    The stores and the monty session are all session-scoped state, and only
    stdio guarantees one client per process. Multi-client transports fail
    closed: they are disabled rather than shared across clients (a shared
    session would additionally serialize every client's runs behind one
    interpreter lock).
    """
    if transport == "stdio" or not (
        config.results.enabled
        or config.artifacts.enabled
        or config.session.enabled
    ):
        return config
    updated = config.model_copy(deep=True)
    updated.results.enabled = False
    updated.artifacts.enabled = False
    updated.session.enabled = False
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
