"""Bridge contracts and JSON-first RPC payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ToolCallError(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    message: str = ""
    traceback: str = ""


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class ToolCallResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    value: Any = None
    error: ToolCallError | None = None

    @classmethod
    def success(cls, value: Any) -> ToolCallResponse:
        return cls(ok=True, value=value)

    @classmethod
    def failure(cls, error: ToolCallError) -> ToolCallResponse:
        return cls(ok=False, error=error)


class HostBridge(Protocol):
    async def call_tool(
        self,
        name: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any: ...

    async def dispatch(self, request: ToolCallRequest) -> ToolCallResponse: ...
