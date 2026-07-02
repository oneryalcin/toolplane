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
    MontyRuntimeError,
    ResourceLimits,
)

from ..adapters.ambient_cli import AMBIENT_CLI_CAPABILITY, is_safe_cli_name
from ..bridges.base import HostBridge
from ..errors import BackendCapabilityError, NamespaceCollisionError
from ..execution import BackendCapabilities, ExecutionError, ExecutionResult
from ..results import build_result_bindings
from ._python import (
    UNAWAITED_CALL_ERROR_TYPE,
    UNAWAITED_CALL_MESSAGE,
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
    )

    def __init__(self, *, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds

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
        _ensure_no_input_collisions(input_namespace, set(external_functions))

        streams = CollectStreams()
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

    def _external_functions(
        self,
        bridge: HostBridge,
        namespace: Mapping[str, str],
    ) -> dict[str, Any]:
        external: dict[str, Any] = {"call_tool": bridge.call_tool}
        for callable_name, capability_name in namespace.items():
            if callable_name.isidentifier() and callable_name != "call_tool":
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
    async def call_bound_tool(**params: Any) -> Any:
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
    ) -> Any:
        return await bridge.call_tool(
            AMBIENT_CLI_CAPABILITY,
            {
                "binary": binary,
                "subcommand": subcommand,
                "options": dict(options or {}),
            },
        )

    return cli_run


def _make_cli_binary(bridge: HostBridge, binary: str) -> Any:
    async def run_binary(subcommand: str | None = None, **options: Any) -> Any:
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
