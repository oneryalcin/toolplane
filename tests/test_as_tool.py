"""runtime.as_tool(): the embeddable single-tool adapter (issue #75).

Contract: a plain async function with a generated docstring — the shape
pydantic-ai, the OpenAI Agents SDK, and LangChain all accept directly.
The docstring is the only discovery channel in embedded mode, so it must
name the bindings and fit strict description caps (OpenAI: ~1024 chars).
"""

from __future__ import annotations

import asyncio
import inspect
import typing

from toolplane import Toolplane


def run(coro):
    return asyncio.run(coro)


def _runtime() -> Toolplane:
    runtime = Toolplane(ambient_cli=True, ambient_cli_allowlist=["git"])

    @runtime.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    return runtime


def test_as_tool_returns_plain_async_function_with_docstring() -> None:
    tool = _runtime().as_tool()

    assert inspect.iscoroutinefunction(tool)
    assert tool.__name__ == "run_code"
    assert list(inspect.signature(tool).parameters) == ["code"]
    # frameworks resolve annotations with get_type_hints, which must see a
    # real `str` even though runtime.py defers annotations
    assert typing.get_type_hints(tool)["code"] is str


def test_description_names_the_bindings_and_fits_strict_caps() -> None:
    doc = _runtime().as_tool().__doc__

    assert doc is not None
    # the docstring is the discovery channel: bindings must be named in it
    assert "add" in doc
    assert "call_tool" in doc
    assert "git" in doc
    assert "save_result" in doc
    assert "save_artifact" in doc
    assert "await" in doc
    assert len(doc) <= 1024


def test_description_stays_capped_with_a_large_registry() -> None:
    runtime = _runtime()
    for i in range(50):
        runtime.register(lambda x: x, name=f"tool_number_{i:02d}")

    doc = runtime.as_tool().__doc__

    assert doc is not None
    assert len(doc) <= 1024
    assert "total" in doc  # truncated lists must say how many exist


def test_executes_and_returns_the_result_contract() -> None:
    tool = _runtime().as_tool(backend="monty")

    result = run(tool('value = await add(x=2, y=3)\nreturn {"sum": value}'))

    assert result["error"] is None
    assert result["value"] == {"sum": 5}
    assert set(result) >= {"value", "stdout", "stderr", "error", "artifacts"}


def test_errors_surface_in_the_dict_not_as_exceptions() -> None:
    tool = _runtime().as_tool(backend="monty")

    result = run(tool("return unknown_name"))

    assert result["error"] is not None
    assert result["error"]["type"] == "NameError"


def test_description_override_and_custom_name() -> None:
    tool = _runtime().as_tool(name="toolplane_exec", description="Custom.")

    assert tool.__name__ == "toolplane_exec"
    assert tool.__doc__ == "Custom."


def test_disabled_surfaces_stay_out_of_the_description() -> None:
    runtime = Toolplane(ambient_cli=False)
    runtime.result_store.disable()
    runtime.artifact_store.disable()

    doc = runtime.as_tool().__doc__

    assert doc is not None
    assert "save_result" not in doc
    assert "save_artifact" not in doc
    assert "Allowed" not in doc
