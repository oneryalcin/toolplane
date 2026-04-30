from __future__ import annotations

import asyncio
import json

import pytest

from toolplane import (
    BackendCapabilityError,
    DuplicateCapabilityError,
    NamespaceCollisionError,
    Toolplane,
)


def run(coro):
    return asyncio.run(coro)


def test_register_search_and_get_schema() -> None:
    runtime = Toolplane()

    @runtime.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    search = run(runtime.search("add numbers"))
    assert "- add: Add two numbers." in search

    schema = run(runtime.get_schema(["add"]))
    assert "### add" in schema
    assert "`x` (integer, required)" in schema
    assert "`y` (integer, required)" in schema
    assert "**Returns**" in schema

    full = run(runtime.get_schema(["add"], detail="full"))
    parsed = json.loads(full)
    assert parsed[0]["name"] == "add"
    assert parsed[0]["inputSchema"]["required"] == ["x", "y"]


def test_execute_calls_registered_tools_and_captures_stdout() -> None:
    runtime = Toolplane()

    @runtime.tool
    async def double(x: int) -> int:
        """Double a number."""
        return x * 2

    result = run(
        runtime.execute(
            """
print("starting")
value = await call_tool("double", {"x": 4})
return {"value": value}
"""
        )
    )

    assert result.ok
    assert result.backend == "local_unsafe"
    assert result.value == {"value": 8}
    assert result.stdout == "starting\n"
    assert result.stderr == ""
    assert result.duration_ms >= 0


def test_execute_injects_safe_python_callables() -> None:
    runtime = Toolplane()

    @runtime.tool
    def add(x: int, y: int) -> int:
        return x + y

    result = run(
        runtime.execute(
            """
value = await add(x=2, y=3)
return value
"""
        )
    )

    assert result.ok
    assert result.value == 5


def test_register_python_namespace_exposes_scoped_and_flat_callables() -> None:
    runtime = Toolplane()

    def read_text(path: str) -> str:
        return f"read:{path}"

    def classify_path(path: str) -> str:
        return "library" if path.startswith("src/") else "repo"

    capabilities = runtime.register_python_namespace(
        "repo",
        {
            "read_text": read_text,
            "classify_path": classify_path,
        },
    )

    result = run(
        runtime.execute(
            """
scoped = await repo.read_text(path="src/toolplane/runtime.py")
flat = await repo_classify_path(path="src/toolplane/runtime.py")
canonical = await call_tool("py:repo/read_text", {"path": "README.md"})
return {"scoped": scoped, "flat": flat, "canonical": canonical}
"""
        )
    )

    assert [capability.name for capability in capabilities] == [
        "py:repo/read_text",
        "py:repo/classify_path",
    ]
    assert result.ok, result.error
    assert result.value == {
        "scoped": "read:src/toolplane/runtime.py",
        "flat": "library",
        "canonical": "read:README.md",
    }


def test_scoped_namespace_root_collisions_fail_loudly() -> None:
    runtime = Toolplane()

    def read_text(path: str) -> str:
        return path

    runtime.register_python_namespace("repo", {"read_text": read_text})

    with pytest.raises(DuplicateCapabilityError):
        @runtime.tool(name="repo")
        def repo_tool() -> str:
            return "shadow"


def test_execution_input_cannot_shadow_toolplane_namespace() -> None:
    runtime = Toolplane()

    def read_text(path: str) -> str:
        return path

    runtime.register_python_namespace("repo", {"read_text": read_text})

    result = run(runtime.execute("return repo", inputs={"repo": "shadow"}))

    assert not result.ok
    assert result.error is not None
    assert result.error.type == NamespaceCollisionError.__name__
    assert "repo" in result.error.message


def test_execute_returns_structured_error() -> None:
    runtime = Toolplane()

    result = run(runtime.execute('return await call_tool("missing", {})'))

    assert not result.ok
    assert result.error is not None
    assert result.error.type == "CapabilityNotFoundError"
    assert "Unknown capability: missing" in result.error.message
    assert "CapabilityNotFoundError" in result.error.traceback


def test_duplicate_registration_is_rejected() -> None:
    runtime = Toolplane()

    @runtime.tool(name="same")
    def first() -> str:
        return "first"

    with pytest.raises(DuplicateCapabilityError):
        @runtime.tool(name="same")
        def second() -> str:
            return "second"


def test_local_unsafe_rejects_package_install_request() -> None:
    runtime = Toolplane()

    with pytest.raises(BackendCapabilityError):
        run(runtime.execute("return 1", packages=["pandas"]))
