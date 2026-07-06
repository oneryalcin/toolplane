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
    # deliberately NOT git: the CLI example verb must be derived from the
    # actual allowlist (a hardcoded example verb misdirected every non-git
    # allowlist, and a git-based test masked it — Opus review on #80)
    runtime = Toolplane(ambient_cli=True, ambient_cli_allowlist=["rg"])

    @runtime.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    doc = runtime.as_tool().__doc__

    assert doc is not None
    # the docstring is the discovery channel: bindings must be named in it
    assert "add" in doc
    assert "call_tool" in doc
    assert "await rg(...)" in doc
    assert "git" not in doc
    assert "save_result" in doc
    assert "save_artifact" in doc
    assert len(doc) <= 1024


def test_empty_allowlist_shows_no_example_verb() -> None:
    runtime = Toolplane(ambient_cli=True, ambient_cli_allowlist=[])

    doc = runtime.as_tool().__doc__

    assert doc is not None
    assert "no binaries are allowed" in doc
    assert "await git" not in doc


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


def test_never_inherits_local_unsafe_implicitly() -> None:
    """All four #80 reviewers: Toolplane()'s dev default is local_unsafe,
    and as_tool is the surface where a MODEL authors the code — the tool
    must not inherit the unsafe backend without an explicit opt-in."""
    runtime = _runtime()  # default_backend is local_unsafe here

    result = run(runtime.as_tool()('return 1'))

    assert result["backend"] == "monty"


def test_explicit_local_unsafe_is_an_opt_in() -> None:
    runtime = _runtime()

    result = run(runtime.as_tool(backend="local_unsafe")('return 1'))

    assert result["backend"] == "local_unsafe"
    assert result["error"] is None


def test_non_unsafe_runtime_default_is_honored() -> None:
    runtime = Toolplane(ambient_cli=False, default_backend="monty")

    result = run(runtime.as_tool()('return 1'))

    assert result["backend"] == "monty"


def test_bad_backend_fails_at_construction_not_first_call() -> None:
    import pytest

    from toolplane.errors import BackendNotFoundError

    with pytest.raises(BackendNotFoundError, match="montey"):
        _runtime().as_tool(backend="montey")


def test_packages_on_incapable_backend_fail_at_construction() -> None:
    import pytest

    from toolplane.errors import BackendCapabilityError

    with pytest.raises(BackendCapabilityError, match="pyodide-deno"):
        _runtime().as_tool(backend="monty", packages=["pandas"])


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


# --- signposts discovered by the live #80 agent run --------------------------

_POSITIONAL_GUESS = 'return await add(2, 3)'


def test_positional_capability_call_teaches_keyword_shape() -> None:
    """Live #80 finding: the bare **params binding leaked
    "call_bound_tool() takes 0 positional arguments" — internals, no lesson."""
    for backend in ("monty", "local_unsafe"):
        result = run(
            _runtime().as_tool(backend=backend)(_POSITIONAL_GUESS)
        )
        assert result["error"] is not None
        assert "keyword arguments only" in result["error"]["message"]
        assert "call_bound_tool" not in result["error"]["message"]


def test_call_tool_miss_lists_registered_names() -> None:
    """Live #80 finding: the old signpost pointed at search tools and MCP
    resources that do not exist in embedded mode; the error must carry the
    registered names itself."""
    result = run(
        _runtime().as_tool()('return await call_tool("canonical:git", {})')
    )

    assert result["error"] is not None
    assert "Registered capability names: add" in result["error"]["message"]


def test_call_tool_miss_on_allowed_binary_teaches_flat_call() -> None:
    """Live #80 finding: a model tried call_tool('git', ...) twice and
    concluded git was unavailable — the miss must say git is a CLI binary
    and teach the flat shape."""
    result = run(_runtime().as_tool()('return await call_tool("git", {})'))

    assert result["error"] is not None
    message = result["error"]["message"]
    assert "CLI binary, not a capability" in message
    assert "await git('log', oneline=True" in message
