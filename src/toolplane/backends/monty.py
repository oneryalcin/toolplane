"""Monty sandboxed Python backend."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic_monty import (
    CollectStreams,
    Monty,
    MontyError,
    MontyRepl,
    MontyRuntimeError,
    ResourceLimits,
)

from ..adapters.ambient_cli import (
    AMBIENT_CLI_CAPABILITY,
    CLI_SHAPE_GUIDANCE,
    is_safe_cli_name,
)
from ..bridges.base import HostBridge
from ..errors import BackendCapabilityError, NamespaceCollisionError
from ..execution import BackendCapabilities, ExecutionError, ExecutionResult
from ..artifacts import build_artifact_bindings
from ..results import build_result_bindings
from ._python import (
    UNAWAITED_CALL_ERROR_TYPE,
    UNAWAITED_CALL_MESSAGE,
    find_reserved_rebindings,
    find_unawaited_calls,
    wrap_async_main,
)

# Monty stringifies an un-awaited external-function future to this exact repr
# before it reaches the host, so the string is the only detectable trace.
# Anchored on purpose: a future embedded in a larger string (e.g. via
# f-string) is NOT detected, and a user string exactly matching the repr is a
# false positive — both accepted until pydantic-monty exposes the future
# out-of-band. Anchoring keeps the false-positive window to a string that is
# meaningless as data.
_UNAWAITED_FUTURE = re.compile(r"^<coroutine external_future\(\d+\)>$")


class MontyBackend:
    """Run code in the Monty sandboxed Python interpreter.

    Monty is a pure-wheel dependency with no filesystem or network access, so
    it is safe to serve by default. Its Python subset has no class definitions,
    so capabilities are exposed as flat callables (e.g. ``math_multiply``) and
    ``call_tool`` rather than scoped ``math.multiply`` namespaces. Ambient CLI
    binaries follow the same shape: ``await git("status", short=True)`` per
    allowed binary, plus ``cli_run(binary, subcommand, options)`` for names
    that are not Python identifiers. Allowlist policy is enforced host-side by
    the bridge, not by these sandbox bindings.
    """

    name = "monty"
    capabilities = BackendCapabilities(
        imports=True,
        third_party_packages=False,
        package_install=False,
        filesystem="none",
        network="none",
        resource_limits=frozenset({"timeout", "memory", "recursion"}),
        persistence="none",
        startup_latency="low",
        scoped_bindings=False,
    )

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        session: bool = False,
        session_max_memory_bytes: int | None = 512 * 1024 * 1024,
    ):
        self.timeout_seconds = timeout_seconds
        self.session = session
        self.session_max_memory_bytes = session_max_memory_bytes
        if session:
            self.capabilities = self.capabilities.model_copy(
                update={"persistence": "session"}
            )
        self._repl: MontyRepl | None = None
        self._pending_reset = False
        # one run at a time per session: MontyRepl holds an internal mutex
        # and a second feed raises instead of queueing, so overlapping
        # execute_code calls serialize here in arrival order
        self._session_lock = asyncio.Lock()

    async def run(
        self,
        code: str,
        *,
        bridge: HostBridge,
        inputs: Mapping[str, Any] | None = None,
        packages: Sequence[str] = (),
        namespace: Mapping[str, str] | None = None,
        scoped_namespace: Mapping[str, Mapping[str, str]] | None = None,
        ambient_cli: bool = False,
        ambient_cli_names: Sequence[str] = (),
        ambient_cli_allowed_binaries: Sequence[str] | None = None,
    ) -> ExecutionResult:
        if packages:
            raise BackendCapabilityError(
                "monty cannot install or import third-party packages; "
                "use the pyodide-deno backend for package workflows"
            )
        if self.session and inputs:
            # inputs are per-run by contract, but everything fed to a session
            # persists and monty has no `del` — accepting them would silently
            # turn one-shot host data (including secrets) into durable
            # session state (Codex adversarial finding on #86)
            raise BackendCapabilityError(
                "per-run inputs are not supported in session mode: session "
                "namespaces persist and monty cannot delete names. Assign "
                "values inside the snippet (they persist like any session "
                "variable), or construct the backend with session=False for "
                "input-driven runs."
            )

        started = time.perf_counter()
        input_namespace = dict(inputs or {})
        external_functions = self._external_functions(bridge, namespace or {})
        if ambient_cli:
            reserved = set(external_functions) | set(input_namespace)
            for name, fn in _cli_external_functions(
                bridge,
                ambient_cli_names,
                reserved=reserved,
            ).items():
                external_functions.setdefault(name, fn)
        for name, fn in build_result_bindings(
            bridge,
            reserved=set(external_functions) | set(input_namespace),
        ).items():
            external_functions.setdefault(name, fn)
        for name, fn in build_artifact_bindings(
            bridge,
            reserved=set(external_functions) | set(input_namespace),
        ).items():
            external_functions.setdefault(name, fn)
        _ensure_no_input_collisions(input_namespace, set(external_functions))

        streams = CollectStreams()
        preflight = find_unawaited_calls(code, set(external_functions))
        if preflight:
            return self._result(
                started,
                streams,
                error=ExecutionError(
                    type=UNAWAITED_CALL_ERROR_TYPE,
                    message="; ".join(preflight),
                ),
            )
        if self.session:
            return await self._run_session(
                code, started, streams, input_namespace, external_functions
            )
        try:
            interpreter = await Monty.acreate(
                wrap_async_main(code) + "\n\nawait __toolplane_main__()",
                script_name="toolplane_snippet.py",
                inputs=sorted(input_namespace) or None,
            )
            value = await asyncio.wait_for(
                interpreter.run_async(
                    inputs=input_namespace or None,
                    limits=ResourceLimits(max_duration_secs=self.timeout_seconds),
                    external_functions=external_functions,
                    print_callback=streams,
                ),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            return self._result(
                started,
                streams,
                error=ExecutionError(
                    type="TimeoutError",
                    message=f"monty execution timed out after {self.timeout_seconds:g}s",
                ),
            )
        except MontyRuntimeError as exc:
            cause = exc.exception()
            return self._result(
                started,
                streams,
                error=ExecutionError(
                    type=type(cause).__name__,
                    message=str(cause),
                    traceback=_format_frames(exc),
                ),
            )
        except MontyError as exc:
            return self._result(
                started,
                streams,
                error=ExecutionError(
                    type=type(exc).__name__,
                    message=str(exc),
                ),
            )
        if _contains_unawaited_future(value) or _printed_unawaited_future(streams):
            return self._result(
                started,
                streams,
                error=ExecutionError(
                    type=UNAWAITED_CALL_ERROR_TYPE,
                    message=UNAWAITED_CALL_MESSAGE,
                ),
            )
        return self._result(started, streams, value=value)

    async def _run_session(
        self,
        code: str,
        started: float,
        streams: CollectStreams,
        input_namespace: dict[str, Any],
        external_functions: dict[str, Any],
    ) -> ExecutionResult:
        """Run at REPL top level so assignments persist across runs.

        Snippets keep their one-shot contract: top-level ``return`` works
        (monty's REPL accepts it, stops the run, and keeps prior state), a
        trailing bare expression is the value otherwise, and a failed run
        does not wipe the namespace.
        """
        rebindings = find_reserved_rebindings(code, set(external_functions))
        if rebindings:
            # in a session a top-level assignment outlives the run and
            # permanently masks the injected binding (monty has no `del`);
            # shadowing reset_session would even remove the escape hatch —
            # fail loudly before feeding instead
            joined = ", ".join(sorted(rebindings))
            return self._result(
                started,
                streams,
                error=ExecutionError(
                    type="NamespaceCollisionError",
                    message=(
                        f"the snippet assigns to Toolplane bindings: "
                        f"{joined}. In a session that assignment would "
                        "persist and mask the binding until the session is "
                        "reset — use different variable names."
                    ),
                ),
            )
        async with self._session_lock:
            repl = self._ensure_repl()
            pending_reset_before = self._pending_reset
            try:
                snapshot: bytes | None = repl.dump()
            except Exception:
                # rollback protection is best-effort; a run that cannot be
                # checkpointed still executes, and a timeout then resets the
                # session instead of restoring it
                snapshot = None
            try:
                value = await asyncio.wait_for(
                    repl.feed_run_async(
                        code,
                        inputs=input_namespace or None,
                        external_functions=external_functions,
                        print_callback=streams,
                    ),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError:
                # a cancelled feed leaves partial mutations in the heap and —
                # whenever the run interned a new string — permanently
                # poisons the interpreter (pydantic/monty#533). Restoring the
                # pre-run snapshot fixes both: the namespace is as if the run
                # never happened. Host-side effects are NOT rolled back; the
                # message must say so instead of promising a transaction.
                # The reset flag is namespace state too: a reset requested by
                # the timed-out run must not fire after "rolled back" was
                # reported (Codex adversarial finding on #86)
                self._pending_reset = pending_reset_before
                if snapshot is not None:
                    self._repl = MontyRepl.load(snapshot)
                    recovery = (
                        "Session variables were rolled back to the state "
                        "before this run"
                    )
                else:
                    self._repl = None
                    recovery = (
                        "The session could not be checkpointed, so its "
                        "variables were cleared"
                    )
                return self._result(
                    started,
                    streams,
                    error=ExecutionError(
                        type="TimeoutError",
                        message=(
                            f"monty execution timed out after "
                            f"{self.timeout_seconds:g}s. {recovery}; "
                            "capability calls, CLI commands, and results or "
                            "artifacts the run saved before timing out "
                            "stand. Execute again to retry."
                        ),
                    ),
                )
            except MontyRuntimeError as exc:
                # the session survives a failed run: prior state persists,
                # and statements completed before the raise persist too
                cause = exc.exception()
                message = str(cause)
                if isinstance(cause, MemoryError):
                    # NOT `del x` — monty's parser has no del statement
                    message += (
                        " — the session memory cap was hit; free space by "
                        "reassigning large session variables (`big = None`) "
                        "or call `await reset_session()`, then re-run"
                    )
                return self._result(
                    started,
                    streams,
                    error=ExecutionError(
                        type=type(cause).__name__,
                        message=message,
                        traceback=_format_frames(exc),
                    ),
                )
            except MontyError as exc:
                return self._result(
                    started,
                    streams,
                    error=ExecutionError(
                        type=type(exc).__name__,
                        message=str(exc),
                    ),
                )
            except Exception as exc:
                # monty's REPL driver can raise bare builtins — awaiting a
                # future persisted un-awaited by an EARLIER run raises
                # RuntimeError("No pending async tasks but ResolveFutures
                # requested"). Every failure mode here must come back as a
                # structured error, never crash the caller.
                if "No pending async tasks" in str(exc):
                    return self._result(
                        started,
                        streams,
                        error=ExecutionError(
                            type=UNAWAITED_CALL_ERROR_TYPE,
                            message=(
                                "this value came from a call in an earlier "
                                "run that was never awaited — a pending "
                                "call cannot cross runs. Re-run the call "
                                "and await it in the same run (e.g. "
                                "`handle = await save_result(value)`)."
                            ),
                        ),
                    )
                return self._result(
                    started,
                    streams,
                    error=ExecutionError(
                        type=type(exc).__name__,
                        message=str(exc),
                    ),
                )
        if _contains_unawaited_future(value) or _printed_unawaited_future(streams):
            return self._result(
                started,
                streams,
                error=ExecutionError(
                    type=UNAWAITED_CALL_ERROR_TYPE,
                    message=UNAWAITED_CALL_MESSAGE,
                ),
            )
        return self._result(started, streams, value=value)

    def _ensure_repl(self) -> MontyRepl:
        if self._repl is None or self._pending_reset:
            limits: ResourceLimits | None = None
            if self.session_max_memory_bytes is not None:
                # literal key only: ResourceLimits silently ignores unknown
                # keys (pydantic/monty#534), so the cap is also asserted
                # empirically in tests, not trusted from construction
                limits = ResourceLimits(
                    max_memory=self.session_max_memory_bytes
                )
            # no max_duration_secs: the REPL clock runs from construction,
            # not per feed (pydantic/monty#483) — per-run timeouts are the
            # host's asyncio.wait_for
            self._repl = MontyRepl(
                script_name="toolplane_session.py", limits=limits
            )
            self._pending_reset = False
        return self._repl

    def _make_reset_session(self) -> Any:
        async def reset_session() -> str:
            self._pending_reset = True
            return (
                "Session variables will be cleared after this run "
                "completes; saved results and artifacts are unaffected."
            )

        return reset_session

    def _external_functions(
        self,
        bridge: HostBridge,
        namespace: Mapping[str, str],
    ) -> dict[str, Any]:
        external: dict[str, Any] = {"call_tool": bridge.call_tool}
        if self.session:
            # installed before capability aliases so a capability that
            # happens to be named reset_session cannot claim the slot: the
            # escape hatch must always be the escape hatch
            external["reset_session"] = self._make_reset_session()
        for callable_name, capability_name in namespace.items():
            if callable_name.isidentifier() and callable_name not in external:
                external[callable_name] = _make_bound_tool(bridge, capability_name)
        return external

    def _result(
        self,
        started: float,
        streams: CollectStreams,
        *,
        value: Any = None,
        error: ExecutionError | None = None,
    ) -> ExecutionResult:
        stdout, stderr = _split_streams(streams)
        return ExecutionResult(
            value=value,
            stdout=stdout,
            stderr=stderr,
            duration_ms=(time.perf_counter() - started) * 1000,
            backend=self.name,
            error=error,
        )


def _printed_unawaited_future(streams: CollectStreams) -> bool:
    """Catch `print(save_result(...))` — inspecting a value is how agents
    most often hit the missing-await bug, and the printed repr is the trace."""
    stdout, _ = _split_streams(streams)
    return any(_UNAWAITED_FUTURE.match(line) for line in stdout.splitlines())


def _contains_unawaited_future(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_UNAWAITED_FUTURE.match(value))
    if isinstance(value, Mapping):
        return any(
            _contains_unawaited_future(item)
            for pair in value.items()
            for item in pair
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_unawaited_future(item) for item in value)
    return False


def _make_bound_tool(bridge: HostBridge, capability_name: str) -> Any:
    async def call_bound_tool(*args: Any, **params: Any) -> Any:
        if args:
            # a bare **params signature leaks
            # "call_bound_tool() takes 0 positional arguments" — the wrong
            # guess must teach the right call (live #80 agent run)
            raise TypeError(
                f"capability functions take keyword arguments only — "
                f"e.g. await fn(param=value), or "
                f'await call_tool("{capability_name}", {{...params}})'
            )
        return await bridge.call_tool(capability_name, params)

    return call_bound_tool


def _cli_external_functions(
    bridge: HostBridge,
    names: Sequence[str],
    *,
    reserved: set[str],
) -> dict[str, Any]:
    external: dict[str, Any] = {}
    if "cli_run" not in reserved:
        external["cli_run"] = _make_cli_run(bridge)
    for name in names:
        if name not in reserved and is_safe_cli_name(name):
            external[name] = _make_cli_binary(bridge, name)
    return external


def _make_cli_run(bridge: HostBridge) -> Any:
    async def cli_run(
        binary: str,
        subcommand: str | None = None,
        options: Mapping[str, Any] | None = None,
        **flags: Any,
    ) -> Any:
        # flags may come as a positional dict or as keyword arguments —
        # one convention shared with the flat bindings, not two to guess
        if options is not None and not isinstance(options, Mapping):
            raise TypeError(
                f"cli_run options must be a dict of flags, got "
                f"{type(options).__name__!r}. {CLI_SHAPE_GUIDANCE}"
            )
        merged = dict(options or {})
        overlap = sorted(set(merged) & set(flags))
        if overlap:
            raise TypeError(
                f"cli_run got flags both in the options dict and as "
                f"keyword arguments: {', '.join(overlap)}. Pass each flag "
                f"once. {CLI_SHAPE_GUIDANCE}"
            )
        merged.update(flags)
        return await bridge.call_tool(
            AMBIENT_CLI_CAPABILITY,
            {
                "binary": binary,
                "subcommand": subcommand,
                "options": merged,
            },
        )

    return cli_run


def _make_cli_binary(bridge: HostBridge, binary: str) -> Any:
    async def run_binary(
        subcommand: str | None = None, *args: Any, **options: Any
    ) -> Any:
        if args:
            raise TypeError(
                f"{binary}() takes at most one positional argument (the "
                f"subcommand); flags are keyword arguments. "
                f"{CLI_SHAPE_GUIDANCE}"
            )
        return await bridge.call_tool(
            AMBIENT_CLI_CAPABILITY,
            {
                "binary": binary,
                "subcommand": subcommand,
                "options": options,
            },
        )

    run_binary.__name__ = binary
    return run_binary


def _split_streams(streams: CollectStreams) -> tuple[str, str]:
    stdout: list[str] = []
    stderr: list[str] = []
    for stream, text in streams.output:
        (stdout if stream == "stdout" else stderr).append(text)
    return "".join(stdout), "".join(stderr)


def _format_frames(exc: MontyRuntimeError) -> str:
    try:
        frames = exc.traceback()
    except Exception:
        return ""
    return "\n".join(
        f'  File "{frame.filename}", line {frame.line}, in {frame.function_name}'
        for frame in frames
    )


def _ensure_no_input_collisions(
    inputs: Mapping[str, Any],
    reserved_names: set[str],
) -> None:
    collisions = sorted(set(inputs) & reserved_names)
    if collisions:
        joined = ", ".join(collisions)
        raise NamespaceCollisionError(
            f"Input names collide with Toolplane namespace bindings: {joined}"
        )
