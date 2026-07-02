from __future__ import annotations

import asyncio
from typing import Any

import pytest

from toolplane import Toolplane
from toolplane.backends import MontyBackend
from toolplane.errors import BackendCapabilityError, NamespaceCollisionError


def run(coro):
    return asyncio.run(coro)


class _StubBridge:
    """Minimal bridge standing in for InProcessBridge."""

    def __init__(self, tools: dict[str, Any] | None = None) -> None:
        self.tools = tools or {}
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def call_tool(self, name: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, params))
        handler = self.tools.get(name)
        if handler is None:
            raise RuntimeError(f"unknown tool: {name}")
        return await handler(**(params or {}))


async def _multiply(x: int, y: int) -> int:
    return x * y


def test_monty_runs_flat_alias_call_tool_inputs_and_stdout() -> None:
    backend = MontyBackend()
    bridge = _StubBridge({"mcp.math.multiply": _multiply})

    result = run(
        backend.run(
            """
print("composing")
a = await math_multiply(x=6, y=7)
b = await call_tool("mcp.math.multiply", {"x": 2, "y": 3})
return {"a": a, "b": b, "bias": bias}
""",
            bridge=bridge,
            namespace={"math_multiply": "mcp.math.multiply"},
            inputs={"bias": 100},
        )
    )

    assert result.error is None, result.error
    assert result.value == {"a": 42, "b": 6, "bias": 100}
    assert result.stdout == "composing\n"
    assert result.backend == "monty"


def test_monty_reports_snippet_error_with_original_type() -> None:
    backend = MontyBackend()

    result = run(
        backend.run('raise ValueError("boom")', bridge=_StubBridge())
    )

    assert result.error is not None
    assert result.error.type == "ValueError"
    assert result.error.message == "boom"
    assert "toolplane_snippet.py" in result.error.traceback


def test_monty_tool_error_is_catchable_in_snippet() -> None:
    async def explode() -> None:
        raise RuntimeError("tool exploded")

    backend = MontyBackend()
    bridge = _StubBridge({"broken.tool": explode})

    result = run(
        backend.run(
            """
try:
    await broken()
except Exception as exc:
    return f"caught: {exc}"
""",
            bridge=bridge,
            namespace={"broken": "broken.tool"},
        )
    )

    assert result.error is None, result.error
    assert result.value == "caught: tool exploded"


def test_monty_times_out_hot_loop() -> None:
    backend = MontyBackend(timeout_seconds=0.5)

    result = run(
        backend.run("while True:\n    pass", bridge=_StubBridge())
    )

    assert result.error is not None
    assert result.error.type == "TimeoutError"


def test_monty_times_out_hanging_tool_call() -> None:
    async def hang() -> None:
        await asyncio.sleep(30)

    backend = MontyBackend(timeout_seconds=0.5)
    bridge = _StubBridge({"slow.tool": hang})

    result = run(
        backend.run(
            'return await call_tool("slow.tool", {})',
            bridge=bridge,
        )
    )

    assert result.error is not None
    assert result.error.type == "TimeoutError"
    assert result.duration_ms < 5000


def test_monty_rejects_packages() -> None:
    backend = MontyBackend()

    with pytest.raises(BackendCapabilityError, match="pyodide-deno"):
        run(backend.run("return 1", bridge=_StubBridge(), packages=["pandas"]))


def test_monty_rejects_ambient_cli() -> None:
    backend = MontyBackend()

    with pytest.raises(BackendCapabilityError, match="ambient CLI"):
        run(backend.run("return 1", bridge=_StubBridge(), ambient_cli=True))


def test_monty_rejects_input_collisions() -> None:
    backend = MontyBackend()

    with pytest.raises(NamespaceCollisionError, match="call_tool"):
        run(
            backend.run(
                "return 1",
                bridge=_StubBridge(),
                inputs={"call_tool": 1},
            )
        )


def test_runtime_default_backends_include_monty() -> None:
    runtime = Toolplane(ambient_cli=False)

    assert "monty" in runtime.backends

    result = run(runtime.execute("return 40 + 2", backend="monty"))

    assert result.error is None, result.error
    assert result.value == 42
