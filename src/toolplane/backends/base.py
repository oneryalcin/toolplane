"""Execution backend protocol."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ..bridges.base import HostBridge
from ..execution import BackendCapabilities, ExecutionResult


class CodeBackend(Protocol):
    name: str
    capabilities: BackendCapabilities

    async def run(
        self,
        code: str,
        *,
        bridge: HostBridge,
        inputs: Mapping[str, Any] | None = None,
        packages: Sequence[str] = (),
        namespace: Mapping[str, str] | None = None,
        ambient_cli: bool = False,
        ambient_cli_names: Sequence[str] = (),
    ) -> ExecutionResult: ...
