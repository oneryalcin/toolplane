"""MCP facade over a configured Toolplane runtime."""

from __future__ import annotations

import inspect
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

# module-level because pydantic must resolve the deferred `ctx: Context`
# annotation on execute_code; the friendly ImportError for missing fastmcp
# still fires in build_mcp_facade before anything is used
try:
    from fastmcp import Context
except ImportError:  # pragma: no cover - dependency is required
    Context = None  # type: ignore[assignment, misc]

from .capabilities import Capability
from .config import ConfigSource, ToolplaneConfig, load_toolplane_config
from .discovery import domain_hint, select_capabilities
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
    hybrid: bool = False,
    hybrid_include: Sequence[str] = (),
) -> "FastMCP":
    """Build the small MCP meta-tool surface for a Toolplane runtime.

    Multi-client caution: monty sessions, the result/artifact stores, and
    CLI escalation grants are all per-process state. Only the config-driven
    path (``build_mcp_facade_from_config`` / ``serve mcp``) disables them
    per transport; callers building the facade directly for http/sse must
    construct ``Toolplane(sessions=False)`` (and disabled stores) and pass
    ``cli_escalation=False``, or one client's variables, handles, and
    grants become every client's.

    Registration order matters: the domain hint baked into the tool
    descriptions is a snapshot of the registry AT BUILD TIME. Capabilities
    registered afterwards stay fully searchable and executable, but are
    invisible to clients' deferred-tool keyword search — register
    everything first (the config-driven path always does).

    ``hybrid`` (#114) is EXPERIMENTAL and re-exports EVERY registered
    capability as an ordinary MCP tool alongside the meta-tools. Measured
    envelope (``docs/code-mode-benchmark.md``): a win at a small registry
    (single/adaptive tasks route to a native tool, loops still use
    ``execute_code``) but the WORST arm at 15 servers, where re-exporting
    the whole registry rebuilds the flat tool surface the facade exists to
    avoid. The general form is selective re-export (#125); the all-or-
    nothing form is unpublished (the CLI flag is hidden) and kept only for
    the benchmark. On clients WITHOUT deferred loading
    every re-exported schema lands in context at once. Each re-exported
    tool is a stateless one-capability dispatch through the same audited
    ``call_tool`` path ``execute_code`` uses (no session, no stores).
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
            "The search_capabilities and execute_code tool descriptions "
            "list exact Python call shapes for the capabilities served "
            "here — when the shape you need is already shown, go straight "
            "to execute_code. Otherwise one search_capabilities call (an "
            "empty query lists everything) returns each hit's exact call "
            "shape plus the snippet rules. Escalate only when that is not "
            "enough: get_capability_schemas for full parameter docs, the "
            "toolplane://namespace resource for the complete namespace "
            "(CLI bindings, result store), and "
            "skill://driving-toolplane/SKILL.md for conventions in depth."
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

    # deferred-loading clients index tools by name+description and load
    # them via keyword search; an agent's first search names the DOMAIN,
    # not "toolplane", so the descriptions must carry the vocabulary of
    # what is behind the facade or every run pays an extra failed-search
    # model request (#115). Computed at build time: from_config registers
    # all capabilities before the facade is built.
    hint = domain_hint(
        runtime.registry.all(), reserved=runtime._reserved_binding_names()
    )

    def _described(doc: str) -> str:
        base = inspect.cleandoc(doc)
        return f"{base}\n\n{hint}" if hint else base

    _SEARCH_DOC = """Search the Toolplane capability registry by keyword.

    Results carry each hit's exact Python call shape and the snippet
    rules — for straightforward tasks, write execute_code directly
    from one search. Matching is exact-word, not fuzzy: if nothing
    matches, retry with different words, or pass an empty query to
    list every capability. The namespace surfaces search does not
    cover (CLI bindings, result store) are summarized in the result
    footer and documented fully in the toolplane://namespace resource.
    """

    @mcp.tool(description=_described(_SEARCH_DOC))
    async def search_capabilities(
        query: str,
        tags: list[str] | None = None,
        detail: SchemaDetail = "brief",
        limit: int | None = None,
    ) -> str:
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

    _EXECUTE_DOC = """Execute Python against the configured Toolplane namespace.

    Use the call shapes exactly as search_capabilities returned them —
    guessed binding names or positional arguments fail. Every binding
    is async — always `await` it — and the snippet should `return` a
    JSON-shaped value. Beyond capability functions the namespace binds
    flat CLI functions for allowed binaries and
    save_result/load_result; the toolplane://namespace resource is the
    full manifest when a shape is unclear.
    """
    if runtime._default_backend_capability("parallel_calls"):
        # the one teaching surface always read before a snippet: footer /
        # manifest / skill all sit behind a read the model can skip, and
        # with those alone fire-then-await adoption measured 0/5
        # (run-20260719-023232, #109)
        _EXECUTE_DOC += """

    Looping over ids to call the same tool? Calls dispatch when
    invoked, not when awaited — fire the batch, then await it:
    `futures = [fn(x=i) for i in ids]` then
    `rows = [await f for f in futures]`. Slow tools overlap; awaiting
    each call in the loop body pays N x its latency.
    """

    @mcp.tool(description=_described(_EXECUTE_DOC))
    async def execute_code(
        code: str,
        backend: str | None = None,
        inputs: dict[str, Any] | None = None,
        packages: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
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

            # ctx.elicit resolves its session through a request-context
            # contextvar, not through the ctx object — and the pyodide RPC
            # path dispatches from a callback thread whose coroutine context
            # has that var unset. Capture the value here, inside the
            # request, so the handler can re-seat it (empirically required:
            # without this, pyodide escalation fails closed). The owning var
            # moved in fastmcp 4 / mcp-sdk v2; see docs/fastmcp4-spike.md.
            # Modern-era connections drop ctx.elicit entirely for MRTR (#132).
            request_ctx = _request_context_var()
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

    if hybrid or hybrid_include:
        if hybrid_include:
            # #125: curated re-export — only the single/adaptive capabilities
            # the operator named, so scale does not rebuild the flat surface
            reexport = select_capabilities(
                runtime.registry.all(), hybrid_include
            )
            if not reexport:
                # a typo'd glob passes validation but selects nothing; hybrid
                # would silently degrade to the plain facade while the
                # operator believes native re-export is active
                print(
                    "toolplane: warning — hybrid.include matched no "
                    f"capabilities ({list(hybrid_include)}); no tools were "
                    "re-exported. Check the patterns against canonical "
                    "names (search_capabilities lists them).",
                    file=sys.stderr,
                )
        else:
            # #114 baseline: re-export everything (held/experimental — the
            # worst arm at scale, kept for the benchmark)
            reexport = list(runtime.registry.all())
        _register_hybrid_tools(mcp, runtime, reexport)

    return mcp


# the three facade meta-tool names a re-exported capability must never
# shadow; combined with runtime-reserved bindings at call time
_FACADE_TOOL_NAMES = frozenset(
    {"search_capabilities", "get_capability_schemas", "execute_code"}
)
_MCP_TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")
# a client-safe MCP tool name: ASCII only. _is_safe_python_name accepts
# Unicode identifiers (str.isidentifier() -> "café", "工具"), which some
# clients reject and which SEP-986 flags — so a candidate must clear this
# ASCII gate, not just be a valid Python name (gauntlet finding)
_MCP_SAFE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")


# EXPERIMENT-ONLY (#127): how a re-exported capability's name and
# description are derived, to test whether pumping the leaf name or the
# description with domain/query vocabulary lets a re-export reach
# first-search discovery parity with a direct tool. This is gated by an
# env var, NOT public config or CLI — the hybrid optimization thread may
# close (see #127). The client sets the `mcp__<server>__` prefix from the
# MCP server name ("toolplane"), which no server-side value can change, so
# these levers only reach the leaf name and the description.
_HYBRID_SIGNAL_ENV = "TOOLPLANE_HYBRID_SIGNAL"
_HYBRID_SIGNALS = ("control", "name", "description")


def _hybrid_signal() -> str:
    signal = os.environ.get(_HYBRID_SIGNAL_ENV, "control").strip().lower()
    return signal if signal in _HYBRID_SIGNALS else "control"


def _capability_domain(capability: Capability) -> str:
    """The server/domain token: ``mcp:orders/get_order`` -> ``orders``."""
    name = capability.name
    if name.startswith("mcp:") and "/" in name:
        return name[4:].split("/", 1)[0]
    base = _MCP_TOOL_NAME_RE.sub("_", name).strip("_")
    return base.split("_", 1)[0] if "_" in base else base or "capability"


def _capability_leaf_words(capability: Capability) -> str:
    """The tool part as spaced words: ``mcp:orders/get_order`` -> ``get order``."""
    name = capability.name
    leaf = name.split("/", 1)[1] if "/" in name else name
    # split underscores/hyphens too, so each token ("get", "order") stands
    # alone for a keyword-matching search — not a single "get_order" blob
    return re.sub(r"[^A-Za-z0-9]+", " ", leaf).strip()


def _hybrid_name_signal_base(capability: Capability) -> str:
    """A query-shaped leaf name: domain + description vocabulary, bounded.

    Puts the same words a domain query matches (the server token plus the
    description terms) into the leaf itself — the strongest honest name
    lever a re-export has, since the server segment is client-owned.
    """
    text = f"{_capability_domain(capability)} {capability.description or ''}"
    tokens = [t for t in _MCP_TOOL_NAME_RE.sub(" ", text).lower().split() if t]
    # drop consecutive duplicates ("order order" -> "order") and cap length
    slug_tokens: list[str] = []
    for token in tokens:
        if not slug_tokens or slug_tokens[-1] != token:
            slug_tokens.append(token)
        if len(slug_tokens) >= 12:
            break
    slug = "_".join(slug_tokens)[:64].strip("_")
    if not slug or not _MCP_SAFE_NAME_RE.match(slug):
        return ""
    return slug


def _hybrid_tool_name(
    capability: Capability, taken: set[str], signal: str = "control"
) -> str:
    """A unique, client-safe MCP tool name for a re-exported capability.

    Control prefers the flat binding the agent already sees in code-mode
    call shapes (``orders_get_order``) for one consistent name across both
    surfaces; falls back to a sanitized canonical name, then numeric
    suffixing, so distinct capabilities never collide onto one tool. The
    ``name`` signal (#127) instead builds a query-shaped leaf; it falls
    back to the control base when the slug is empty/unsafe.
    """
    candidates = [capability.name, *sorted(capability.aliases)]
    base = next(
        (c for c in candidates if _MCP_SAFE_NAME_RE.match(c)),
        _MCP_TOOL_NAME_RE.sub("_", capability.name).strip("_") or "capability",
    )
    if signal == "name":
        base = _hybrid_name_signal_base(capability) or base
    name = base
    suffix = 2
    while name in taken or name in _FACADE_TOOL_NAMES:
        name = f"{base}_{suffix}"
        suffix += 1
    taken.add(name)
    return name


def _hybrid_tool_description(capability: Capability, signal: str = "control") -> str:
    base = capability.description or capability.name
    if signal == "description":
        # front-load the server/domain word and the leaf verb tokens, so the
        # terms a domain query matches lead the description (#127).
        domain = _capability_domain(capability)
        leaf = _capability_leaf_words(capability)
        return f"{domain}: {leaf}. {base}"
    return base


def _make_hybrid_dispatch(runtime: Toolplane, canonical_name: str) -> Any:
    async def _dispatch(**params: Any) -> Any:
        # canonical_name is closure-captured, NEVER a parameter. If the
        # dispatch target were a keyword arg with a default, an input
        # property named "canonical" (re-exported schemas are third-party
        # verbatim, so this is author-reachable) would rebind it — the
        # client approves and displays `orders_get_order` while the server
        # runs something else. That is the hazard: tool-IDENTITY confusion
        # defeating per-tool approval and audit, NOT a policy bypass
        # (call_tool still audits and enforces the CLI allowlist below, and
        # execute_code can already reach hidden canonicals — hidden is a
        # discovery boundary, not a security one). One re-export = one fixed
        # capability, through the same audited call_tool path.
        return await runtime.call_tool(canonical_name, params)

    return _dispatch


def _register_hybrid_tools(
    mcp: "FastMCP", runtime: Toolplane, capabilities: Sequence[Capability]
) -> None:
    from fastmcp.tools.function_tool import FunctionTool

    signal = _hybrid_signal()
    taken: set[str] = set()
    for capability in capabilities:
        tool_name = _hybrid_tool_name(capability, taken, signal=signal)
        # no output_schema: fastmcp validates the return against it, but a
        # capability's DECLARED returns need not match its ACTUAL value
        # (routinely true for third-party MCP tools), and a mismatch would
        # turn a successful dispatch into a client-side error. The value
        # still comes back (dict -> structured, scalar -> text) and the
        # description summarizes the shape.
        mcp.add_tool(
            FunctionTool(
                name=tool_name,
                description=_hybrid_tool_description(capability, signal=signal),
                parameters=capability.parameters,
                fn=_make_hybrid_dispatch(runtime, capability.name),
            )
        )


def _request_context_var() -> Any:
    """The ContextVar carrying the active MCP request context.

    fastmcp 4 (mcp-sdk v2) removed the raw SDK var and owns a documented
    replacement; both expose the same get/set/reset API. Lazy import: the
    facade must stay importable without fastmcp installed.
    """
    try:
        from fastmcp.server.dependencies import (
            fastmcp_request_ctx as request_context_var,
        )
    except ImportError:
        from mcp.server.lowlevel.server import (
            request_ctx as request_context_var,
        )
    return request_context_var


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
    hybrid: bool = False,
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
    # curated re-export is config-driven (#125); the CLI --hybrid flag is
    # the held all-or-nothing baseline. A config include list takes
    # precedence and is the real public surface.
    hybrid_include = tuple(parsed.hybrid.include) if parsed.hybrid.enabled else ()
    # escalation grants are session-scoped state like the stores: only stdio
    # guarantees one client per process, so a human approval cannot leak to
    # a client that never saw the prompt
    return build_mcp_facade(
        runtime,
        policy=policy,
        cli_escalation=transport == "stdio",
        hybrid=hybrid,
        hybrid_include=hybrid_include,
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
    hybrid: bool = False,
) -> None:
    app = await build_mcp_facade_from_config(
        config, transport=transport, allow_unsafe=allow_unsafe, hybrid=hybrid
    )
    kwargs: dict[str, Any] = {}
    if host is not None:
        kwargs["host"] = host
    if port is not None:
        kwargs["port"] = port
    await app.run_async(transport=transport, show_banner=False, **kwargs)
