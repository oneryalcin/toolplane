"""Execution backend protocol."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from ..execution import BackendCapabilities, ExecutionResult


class CodeBackend(Protocol):
    name: str
    capabilities: BackendCapabilities

    async def run(
        self,
        code: str,
        *,
        namespace: Mapping[str, Callable[..., Any]],
        inputs: Mapping[str, Any] | None = None,
        packages: Sequence[str] = (),
    ) -> ExecutionResult: ...
