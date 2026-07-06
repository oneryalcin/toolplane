"""Development-only local Python backend."""

from __future__ import annotations

import contextlib
import inspect
import io
import time
import traceback
import warnings
from collections.abc import Mapping, Sequence
from typing import Any

from ..adapters.ambient_cli import build_local_cli_namespace
from ..bridges.base import HostBridge
from ..errors import BackendCapabilityError, NamespaceCollisionError
from ..execution import BackendCapabilities, ExecutionError, ExecutionResult
from ..artifacts import build_artifact_bindings
from ..results import build_result_bindings
from ._python import (
    UNAWAITED_CALL_ERROR_TYPE,
    UNAWAITED_CALL_MESSAGE,
    find_unawaited_calls,
    stderr_reports_unawaited,
    wrap_async_main,
)


class LocalUnsafeBackend:
    """Run code in the current Python process.

    This backend is intentionally unsafe. It is for validating the runtime shape
    and for trusted local development only.
    """

    name = "local_unsafe"
    capabilities = BackendCapabilities(
        imports=True,
        third_party_packages=True,
        package_install=False,
        filesystem="full",
        network="full",
        persistence="none",
        startup_latency="low",
    )

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
                "local_unsafe can import installed packages but does not install packages"
            )

        started = time.perf_counter()
        stdout = io.StringIO()
        stderr = io.StringIO()
        capability_namespace = dict(namespace or {})
        scoped_capability_namespace = _copy_scoped_namespace(scoped_namespace or {})
        input_namespace = dict(inputs or {})
        scope: dict[str, Any] = {
            "__name__": "__toolplane_local__",
            "call_tool": bridge.call_tool,
        }

        try:
            _ensure_no_input_collisions(
                input_namespace,
                {"call_tool", "cli"}
                | set(capability_namespace)
                | set(scoped_capability_namespace),
            )
            if ambient_cli:
                scope.update(
                    build_local_cli_namespace(
                        bridge,
                        ambient_cli_names,
                        reserved=set(scope)
                        | set(capability_namespace)
                        | set(scoped_capability_namespace)
                        | set(input_namespace),
                        allowed_binaries=(
                            set(ambient_cli_allowed_binaries)
                            if ambient_cli_allowed_binaries is not None
                            else None
                        ),
                    )
                )
            scope.update(
                build_result_bindings(
                    bridge,
                    reserved=set(scope)
                    | set(capability_namespace)
                    | set(scoped_capability_namespace)
                    | set(input_namespace),
                )
            )
            scope.update(
                build_artifact_bindings(
                    bridge,
                    reserved=set(scope)
                    | set(capability_namespace)
                    | set(scoped_capability_namespace)
                    | set(input_namespace),
                )
            )
            scope.update(_callable_namespace(bridge, capability_namespace))
            scope.update(_scoped_namespace(bridge, scoped_capability_namespace))
            # async bindings only: cli namespace objects are excluded because
            # their bare calls have no side effects to lose
            binding_names = {
                name
                for name, bound in scope.items()
                if inspect.iscoroutinefunction(bound)
            }
            scope.update(input_namespace)
            binding_names -= set(input_namespace)
            preflight = find_unawaited_calls(code, binding_names)
            if preflight:
                return ExecutionResult(
                    duration_ms=_elapsed_ms(started),
                    backend=self.name,
                    error=ExecutionError(
                        type=UNAWAITED_CALL_ERROR_TYPE,
                        message="; ".join(preflight),
                    ),
                )
            exec(wrap_async_main(code), scope, scope)
            # capture warnings directly: pytest or any upstream warnings
            # filter can intercept them before they ever reach stderr
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                warnings.catch_warnings(record=True) as caught,
            ):
                warnings.simplefilter("always")
                value = await scope["__toolplane_main__"]()
            unawaited_warning = False
            for warning in caught:
                if issubclass(
                    warning.category, RuntimeWarning
                ) and stderr_reports_unawaited(str(warning.message), binding_names):
                    unawaited_warning = True
                else:
                    stderr.write(
                        warnings.formatwarning(
                            warning.message,
                            warning.category,
                            warning.filename,
                            warning.lineno,
                        )
                    )
            if unawaited_warning:
                return ExecutionResult(
                    stdout=stdout.getvalue(),
                    stderr=stderr.getvalue(),
                    duration_ms=_elapsed_ms(started),
                    backend=self.name,
                    error=ExecutionError(
                        type=UNAWAITED_CALL_ERROR_TYPE,
                        message=UNAWAITED_CALL_MESSAGE,
                    ),
                )
            if _close_unawaited(value):
                return ExecutionResult(
                    stdout=stdout.getvalue(),
                    stderr=stderr.getvalue(),
                    duration_ms=_elapsed_ms(started),
                    backend=self.name,
                    error=ExecutionError(
                        type=UNAWAITED_CALL_ERROR_TYPE,
                        message=UNAWAITED_CALL_MESSAGE,
                    ),
                )
            return ExecutionResult(
                value=value,
                stdout=stdout.getvalue(),
                stderr=stderr.getvalue(),
                duration_ms=_elapsed_ms(started),
                backend=self.name,
            )
        except Exception as exc:  # local unsafe backend reports structured failures
            return ExecutionResult(
                stdout=stdout.getvalue(),
                stderr=stderr.getvalue(),
                duration_ms=_elapsed_ms(started),
                backend=self.name,
                error=ExecutionError(
                    type=type(exc).__name__,
                    message=str(exc),
                    traceback=traceback.format_exc(),
                ),
            )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _close_unawaited(value: Any) -> bool:
    """Detect awaitables in a result value; close coroutines to avoid warnings.

    The list comprehensions are deliberate: every item must be visited so all
    coroutines get closed, so short-circuiting generators would be wrong.
    """
    if inspect.iscoroutine(value):
        value.close()
        return True
    if inspect.isawaitable(value):
        return True
    if isinstance(value, Mapping):
        return any(
            [_close_unawaited(item) for pair in value.items() for item in pair]
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any([_close_unawaited(item) for item in value])
    return False


def _callable_namespace(
    bridge: HostBridge,
    namespace: Mapping[str, str],
) -> dict[str, Any]:
    callables: dict[str, Any] = {}
    for callable_name, capability_name in namespace.items():
        call_bound_tool = _make_bound_tool(bridge, capability_name)

        call_bound_tool.__name__ = callable_name
        callables[callable_name] = call_bound_tool
    return callables


def _copy_scoped_namespace(
    scoped_namespace: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    return {
        namespace: dict(members)
        for namespace, members in scoped_namespace.items()
    }


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


def _scoped_namespace(
    bridge: HostBridge,
    scoped_namespace: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    return {
        namespace: _ToolNamespace(bridge, bindings)
        for namespace, bindings in scoped_namespace.items()
    }


class _ToolNamespace:
    def __init__(self, bridge: HostBridge, bindings: Mapping[str, str]) -> None:
        self._bridge = bridge
        self._bindings = dict(bindings)

    def __getattr__(self, member: str) -> Any:
        if member.startswith("_") or member not in self._bindings:
            raise AttributeError(member)
        call_bound_tool = _make_bound_tool(self._bridge, self._bindings[member])
        call_bound_tool.__name__ = member
        return call_bound_tool


def _make_bound_tool(
    bridge: HostBridge,
    capability_name: str,
) -> Any:
    async def call_bound_tool(*args: Any, **params: Any) -> Any:
        if args:
            # same lesson as monty's binding: teach keyword-only instead of
            # leaking "call_bound_tool() takes 0 positional arguments"
            raise TypeError(
                f"capability functions take keyword arguments only — "
                f"e.g. await fn(param=value), or "
                f'await call_tool("{capability_name}", {{...params}})'
            )
        return await bridge.call_tool(capability_name, params)

    return call_bound_tool
