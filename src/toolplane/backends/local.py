"""Development-only local Python backend."""

from __future__ import annotations

import contextlib
import io
import textwrap
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..errors import BackendCapabilityError
from ..execution import BackendCapabilities, ExecutionError, ExecutionResult


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
        namespace: Mapping[str, Callable[..., Any]],
        inputs: Mapping[str, Any] | None = None,
        packages: Sequence[str] = (),
    ) -> ExecutionResult:
        if packages:
            raise BackendCapabilityError(
                "local_unsafe can import installed packages but does not install packages"
            )

        started = time.perf_counter()
        stdout = io.StringIO()
        stderr = io.StringIO()
        scope: dict[str, Any] = {
            "__name__": "__toolplane_local__",
            **dict(namespace),
            **dict(inputs or {}),
        }

        try:
            exec(_wrap_async_function(code), scope, scope)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                value = await scope["__toolplane_main__"]()
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


def _wrap_async_function(code: str) -> str:
    body = code.rstrip()
    if not body.strip():
        body = "return None"
    return "async def __toolplane_main__():\n" + textwrap.indent(body, "    ")


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
