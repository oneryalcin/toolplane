from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any

from toolplane.bridges import (
    HttpCallbackBridge,
    InProcessBridge,
    ToolCallRequest,
    ToolCallResponse,
)
from toolplane.registry import CapabilityRegistry


def run(coro):
    return asyncio.run(coro)


def test_in_process_bridge_dispatches_to_registry() -> None:
    async def exercise() -> ToolCallResponse:
        registry = CapabilityRegistry()

        def add(x: int, y: int) -> int:
            return x + y

        registry.register(add)
        bridge = InProcessBridge(registry)
        return await bridge.dispatch(
            ToolCallRequest(name="add", params={"x": 2, "y": 3})
        )

    response = run(exercise())

    assert response.ok
    assert response.value == 5
    assert response.error is None


def test_in_process_bridge_returns_structured_errors() -> None:
    async def exercise() -> ToolCallResponse:
        bridge = InProcessBridge(CapabilityRegistry())
        return await bridge.dispatch(ToolCallRequest(name="missing"))

    response = run(exercise())

    assert not response.ok
    assert response.error is not None
    assert response.error.type == "CapabilityNotFoundError"
    assert "Unknown capability: missing" in response.error.message
    assert "CapabilityNotFoundError" in response.error.traceback


def test_http_callback_bridge_dispatches_to_registry() -> None:
    async def exercise() -> ToolCallResponse:
        registry = CapabilityRegistry()

        async def add(x: int, y: int) -> int:
            return x + y

        registry.register(add)
        bridge = InProcessBridge(registry)
        callback = HttpCallbackBridge(
            bridge=bridge,
            loop=asyncio.get_running_loop(),
            token="test-token",
        )
        callback.start()
        try:
            return await asyncio.to_thread(
                _post_callback,
                callback.url,
                "test-token",
                {"name": "add", "params": {"x": 10, "y": 4}},
            )
        finally:
            callback.close()

    response = run(exercise())

    assert response.ok
    assert response.value == 14


def test_http_callback_bridge_rejects_bad_token() -> None:
    async def exercise() -> ToolCallResponse:
        bridge = InProcessBridge(CapabilityRegistry())
        callback = HttpCallbackBridge(
            bridge=bridge,
            loop=asyncio.get_running_loop(),
            token="test-token",
        )
        callback.start()
        try:
            return await asyncio.to_thread(
                _post_callback,
                callback.url,
                "wrong-token",
                {"name": "anything", "params": {}},
            )
        finally:
            callback.close()

    response = run(exercise())

    assert not response.ok
    assert response.error is not None
    assert response.error.type == "Unauthorized"


def test_http_callback_bridge_returns_registry_errors() -> None:
    async def exercise() -> ToolCallResponse:
        bridge = InProcessBridge(CapabilityRegistry())
        callback = HttpCallbackBridge(
            bridge=bridge,
            loop=asyncio.get_running_loop(),
            token="test-token",
        )
        callback.start()
        try:
            return await asyncio.to_thread(
                _post_callback,
                callback.url,
                "test-token",
                {"name": "missing", "params": {}},
            )
        finally:
            callback.close()

    response = run(exercise())

    assert not response.ok
    assert response.error is not None
    assert response.error.type == "CapabilityNotFoundError"
    assert "Unknown capability: missing" in response.error.message


def _post_callback(
    url: str,
    token: str,
    payload: dict[str, Any],
) -> ToolCallResponse:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return ToolCallResponse.model_validate_json(response.read())
