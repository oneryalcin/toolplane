"""In-process bridge to the host capability registry."""

from __future__ import annotations

import traceback
from collections.abc import Mapping
from typing import Any

from ..adapters.ambient_cli import AMBIENT_CLI_CAPABILITY
from ..errors import CliPolicyError
from ..registry import CapabilityRegistry
from .base import ToolCallError, ToolCallRequest, ToolCallResponse


class InProcessBridge:
    """Dispatch capability calls in the current Python process."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        ambient_cli_allowed_binaries: set[str] | frozenset[str] | None = None,
    ) -> None:
        self.registry = registry
        self._ambient_cli_allowed_binaries = (
            frozenset(ambient_cli_allowed_binaries)
            if ambient_cli_allowed_binaries is not None
            else None
        )

    async def call_tool(
        self,
        name: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        normalized_params = dict(params or {})
        self._enforce_ambient_cli_policy(name, normalized_params)
        return await self.registry.call(name, normalized_params)

    async def dispatch(self, request: ToolCallRequest) -> ToolCallResponse:
        try:
            return ToolCallResponse.success(
                await self.call_tool(request.name, request.params)
            )
        except Exception as exc:
            return ToolCallResponse.failure(
                ToolCallError(
                    type=type(exc).__name__,
                    message=str(exc),
                    traceback=traceback.format_exc(),
                )
            )

    def _enforce_ambient_cli_policy(
        self,
        name: str,
        params: Mapping[str, Any],
    ) -> None:
        if (
            name != AMBIENT_CLI_CAPABILITY
            or self._ambient_cli_allowed_binaries is None
        ):
            return
        binary = str(params.get("binary", ""))
        if binary not in self._ambient_cli_allowed_binaries:
            raise CliPolicyError(
                f"CLI binary is not allowed by Toolplane policy: {binary}"
            )
