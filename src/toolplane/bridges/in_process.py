"""In-process bridge to the host capability registry."""

from __future__ import annotations

import traceback
from collections.abc import Mapping
from typing import Any

from ..registry import CapabilityRegistry
from .base import ToolCallError, ToolCallRequest, ToolCallResponse


class InProcessBridge:
    """Dispatch capability calls in the current Python process."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    async def call_tool(
        self,
        name: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        return await self.registry.call(name, dict(params or {}))

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
