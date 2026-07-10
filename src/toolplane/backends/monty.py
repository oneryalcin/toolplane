"""Monty sandboxed Python backend."""

from __future__ import annotations

import asyncio
import os
import re
import signal
import time
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic_monty import (
    AsyncMonty,
    AsyncMontySession,
    CollectStreams,
    MontyCrashedError,
    MontyError,
    MontyRuntimeError,
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
        # one pool of subprocess workers per backend, entered lazily on the
        # first run and left open for the process lifetime (workers die with
        # the host process)
        self._pool: AsyncMonty | None = None
        self._pool_lock = asyncio.Lock()
        self._session: AsyncMontySession | None = None
        self._session_pid: int | None = None
        self._pending_reset = False
        # one run at a time per session: a session worker serves one feed at
        # a time and a second feed raises instead of queueing, so overlapping
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
            # one-shot = a session worker used for exactly one feed. The
            # REPL accepts top-level await/return and yields the trailing
            # expression, so raw code goes in unwrapped. The in-sandbox
            # duration cap excludes time suspended on host calls, so the
            # host-side wait_for below is still the wall-clock authority.
            interpreter, worker_pid = await self._checkout(
                script_name="toolplane_snippet.py",
                limits={"max_duration_secs": self.timeout_seconds},
            )
            interrupted = False
            try:
                try:
                    value = await asyncio.wait_for(
                        interpreter.feed_run(
                            code,
                            inputs=input_namespace or None,
                            external_lookup=external_functions,
                            print_callback=streams,
                        ),
                        timeout=self.timeout_seconds,
                    )
                except TimeoutError:
                    interrupted = True
                    raise
            finally:
                await _close_worker(
                    interpreter, worker_pid, interrupted=interrupted
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
            repl = await self._ensure_session()
            pending_reset_before = self._pending_reset
            try:
                snapshot: bytes | None = await repl.dump()
            except Exception:
                # rollback protection is best-effort; a run that cannot be
                # checkpointed still executes, and a timeout then resets the
                # session instead of restoring it
                snapshot = None
            try:
                value = await asyncio.wait_for(
                    repl.feed_run(
                        code,
                        inputs=input_namespace or None,
                        external_lookup=external_functions,
                        print_callback=streams,
                    ),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError:
                # a cancelled feed wedges the worker: every later feed on it
                # raises "feed called while a suspension is awaiting an
                # answer" (pydantic/monty#533's successor behavior). The
                # worker is discarded and the pre-run snapshot is loaded into
                # a FRESH checkout — load() is only valid before the first
                # feed — so the namespace is as if the run never happened.
                # Host-side effects are NOT rolled back; the message must say
                # so instead of promising a transaction.
                # The reset flag is namespace state too: a reset requested by
                # the timed-out run must not fire after "rolled back" was
                # reported (Codex adversarial finding on #86)
                self._pending_reset = pending_reset_before
                await self._discard_session(repl, interrupted=True)
                recovery = await self._restore_snapshot(snapshot)
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
            except MontyCrashedError as exc:
                # the dedicated worker process died mid-run (OOM-kill, crash,
                # external signal). Its namespace died with it — drop the
                # session so the next run checks out a fresh worker instead
                # of feeding a corpse.
                self._pending_reset = False
                await self._discard_session(repl, interrupted=False)
                return self._result(
                    started,
                    streams,
                    error=ExecutionError(
                        type=type(exc).__name__,
                        message=(
                            f"{exc}. The session worker process died and "
                            "its variables were lost; a fresh session "
                            "starts on the next run. Saved results and "
                            "artifacts are unaffected."
                        ),
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
                # Unlike user-code errors, these protocol-level failures
                # FINISH the checkout on the subprocess API (a later feed
                # raises "this checkout has already been finished"), so the
                # session must be restored from the pre-run snapshot for
                # its variables to survive — same recovery as a timeout.
                self._pending_reset = pending_reset_before
                await self._discard_session(repl, interrupted=False)
                # the rollback is forced by upstream, so the message must
                # disclose it: on 0.0.18 statements completed before the
                # raise persisted, here they do not (unbiased-review
                # finding on the port)
                recovery = await self._restore_snapshot(snapshot)
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
                                "`handle = await save_result(value)`). "
                                f"{recovery}; capability calls and saved "
                                "results or artifacts from this run stand."
                            ),
                        ),
                    )
                return self._result(
                    started,
                    streams,
                    error=ExecutionError(
                        type=type(exc).__name__,
                        message=(
                            f"{exc}. {recovery}; capability calls and "
                            "saved results or artifacts from this run "
                            "stand."
                        ),
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

    async def _ensure_pool(self) -> AsyncMonty:
        # locked: concurrent first one-shot runs would otherwise each spawn
        # and enter a pool, leaking all but the last (both reviewers)
        async with self._pool_lock:
            if self._pool is None:
                # request_timeout is the in-pool backstop for CPU-bound
                # overruns: the pool SIGKILLs a worker whose single feed
                # exceeds it and raises MontyCrashedError. It counts
                # execution only (host suspensions excluded) and does not
                # accumulate across feeds, so with a margin over the host
                # wait_for it can never kill a run the host would allow.
                pool = AsyncMonty(request_timeout=self.timeout_seconds + 5)
                await pool.__aenter__()
                self._pool = pool
        return self._pool

    async def _checkout(
        self, *, script_name: str, limits: dict[str, Any] | None
    ) -> tuple[AsyncMontySession, int | None]:
        # pool and sessions are async context managers; a long-lived backend
        # enters them manually, discards workers explicitly, and leaves the
        # pool open for the process lifetime. The worker pid is only
        # readable while the session is idle — capture it now, because a
        # cancelled feed needs it for the kill and reads None by then.
        pool = await self._ensure_pool()
        checkout = pool.checkout(script_name=script_name, limits=limits)
        session = await checkout.__aenter__()
        pid = session.worker_pid
        return session, (pid if isinstance(pid, int) else None)

    async def _discard_session(
        self, session: AsyncMontySession, *, interrupted: bool
    ) -> None:
        pid = self._session_pid
        self._session = None
        self._session_pid = None
        await _close_worker(session, pid, interrupted=interrupted)

    async def _restore_snapshot(self, snapshot: bytes | None) -> str:
        """Load the pre-run snapshot into a fresh checkout; report honestly.

        Every failure ends with ``_session = None`` (next run gets a fresh
        empty session) and a message that says what actually happened —
        recovery failing must never raise past the structured-error contract
        (both reviewers, fault-injection confirmed).
        """
        if snapshot is None:
            return (
                "The session could not be checkpointed, so its variables "
                "were cleared"
            )
        fresh: AsyncMontySession | None = None
        pid: int | None = None
        try:
            fresh, pid = await self._checkout(
                script_name="toolplane_session.py",
                limits=self._session_limits(),
            )
            await fresh.load(snapshot)
        except Exception:
            if fresh is not None:
                await _close_worker(fresh, pid, interrupted=False)
            self._session = None
            self._session_pid = None
            return (
                "The session could not be restored from its pre-run "
                "checkpoint, so its variables were cleared"
            )
        self._session = fresh
        self._session_pid = pid
        return (
            "Session variables were rolled back to the state before this run"
        )

    def _session_limits(self) -> dict[str, Any] | None:
        if self.session_max_memory_bytes is None:
            return None
        # literal key only: the limits dict silently ignores unknown keys
        # (pydantic/monty#534), so the cap is also asserted empirically in
        # tests, not trusted from construction.
        # no max_duration_secs: the cap accumulates across feeds, not per
        # feed (pydantic/monty#483 behavior persists on the subprocess
        # API) — per-run timeouts are the host's asyncio.wait_for
        return {"max_memory": self.session_max_memory_bytes}

    async def _ensure_session(self) -> AsyncMontySession:
        if self._pending_reset and self._session is not None:
            await self._discard_session(self._session, interrupted=False)
        if self._session is None or self._pending_reset:
            self._session, self._session_pid = await self._checkout(
                script_name="toolplane_session.py",
                limits=self._session_limits(),
            )
            self._pending_reset = False
        return self._session

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


# bound on a worker close: a clean close is instant, so anything slower is
# a worker that will never come back on its own
_CLOSE_TIMEOUT_S = 2.0


def _kill_worker(pid: int | None) -> None:
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        # already dead, or not ours to kill — the close below still runs
        pass


async def _close_worker(
    session: AsyncMontySession, pid: int | None, *, interrupted: bool
) -> None:
    """Shut a checked-out worker down without ever blocking the caller.

    ``__aexit__`` waits for the in-flight turn to finish. A worker whose
    feed was cancelled while suspended on a host call closes instantly, but
    one cancelled mid-computation never finishes its turn — the close hangs
    forever (both reviewers; empirically a hot ``while True`` loop wedged
    ``run()`` permanently). When the run was interrupted, SIGKILL first:
    the pool replaces killed workers by contract (MontyCrashedError), so
    the kill is safe. Otherwise try a clean close, and escalate to the
    kill only if it stalls.
    """
    if interrupted:
        _kill_worker(pid)
    try:
        await asyncio.wait_for(
            session.__aexit__(None, None, None), timeout=_CLOSE_TIMEOUT_S
        )
    except TimeoutError:
        _kill_worker(pid)
        try:
            await asyncio.wait_for(
                session.__aexit__(None, None, None), timeout=_CLOSE_TIMEOUT_S
            )
        except Exception:
            pass
    except Exception:
        # a wedged or crashed worker may refuse a clean shutdown; the pool
        # has already replaced it, so failing to close is not an error the
        # caller can act on
        pass


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
