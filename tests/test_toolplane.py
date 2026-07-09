from __future__ import annotations

import asyncio
import json

import pytest

from toolplane import (
    BackendCapabilities,
    BackendCapabilityError,
    DuplicateCapabilityError,
    ExecutionResult,
    NamespaceCollisionError,
    Toolplane,
)
from toolplane.results import ResultStore


def run(coro):
    return asyncio.run(coro)


def test_register_search_and_get_schema() -> None:
    runtime = Toolplane()

    @runtime.tool(tags={"math"})
    def add(x: int, y: int) -> int:
        """Add two numbers."""
        return x + y

    search = run(runtime.search("add numbers"))
    # one search turn must carry the executable call shape (kwargs only),
    # the canonical name for schema escalation, and the snippet rules —
    # this is the #106 short path
    assert "- `await add(x=<integer>, y=<integer>)` — Add two numbers. [add]" in search
    assert "every binding is async" in search
    assert "toolplane://namespace" in search

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


def test_execute_preserves_legacy_custom_backend_signature() -> None:
    class LegacyBackend:
        name = "legacy"
        capabilities = BackendCapabilities(
            imports=False,
            third_party_packages=False,
            package_install=False,
            filesystem="none",
            network="none",
        )

        async def run(
            self,
            code,
            *,
            bridge,
            inputs=None,
            packages=(),
            namespace=None,
            scoped_namespace=None,
            ambient_cli=False,
            ambient_cli_names=(),
        ):
            return ExecutionResult(
                value={"ambient_cli_names": tuple(ambient_cli_names)},
                backend=self.name,
            )

    runtime = Toolplane(
        backends=[LegacyBackend()],
        default_backend="legacy",
        ambient_cli_allowlist=["git"],
    )

    result = run(runtime.execute("return 1"))

    assert result.ok
    assert result.value == {"ambient_cli_names": ("git",)}


def _deepwiki_like_runtime() -> Toolplane:
    """Registry shaped like the live session that exposed the search bug."""
    runtime = Toolplane(ambient_cli=False)

    @runtime.tool(name="read_wiki_structure", tags={"deepwiki"})
    def read_wiki_structure(repoName: str) -> str:
        """Get a list of documentation topics for a GitHub repository in
        the owner/repo format."""
        return ""

    @runtime.tool(name="ask_question", tags={"deepwiki"})
    def ask_question(repoName: str, question: str) -> str:
        """Ask any question about a GitHub repository and get an AI-powered
        response grounded in the repository context."""
        return ""

    return runtime


def test_search_does_not_match_query_words_as_substrings() -> None:
    runtime = _deepwiki_like_runtime()

    # 'is' hid inside "list", 'git' inside "GitHub", short words matched
    # everything — each of these must now return no capabilities
    for query in ("is", "do", "git", "what is the weather in paris"):
        result = run(runtime.search(query))
        assert result.startswith("No capabilities matched the query."), (
            query,
            result,
        )


def test_search_still_matches_words_inside_underscored_names() -> None:
    runtime = _deepwiki_like_runtime()

    # subword of a snake_case name, whole name, and description words
    for query in ("wiki", "structure", "read_wiki_structure", "documentation"):
        result = run(runtime.search(query))
        assert "read_wiki_structure" in result, (query, result)

    result = run(runtime.search("question"))
    assert "ask_question" in result


def test_search_no_match_is_a_signpost_not_a_dead_end() -> None:
    runtime = _deepwiki_like_runtime()

    result = run(runtime.search("cli shell command"))

    # a cold agent recovering from a failed search needs the registry size
    # and a browse path, not silence
    assert "2 capabilities are registered" in result
    assert "empty query" in result
    assert "toolplane://namespace" in result


def test_describe_namespace_covers_disabled_surfaces() -> None:
    runtime = Toolplane(
        ambient_cli=False,
        result_store=ResultStore(enabled=False),
    )

    manifest = runtime.describe_namespace()

    assert "CLI access is disabled" in manifest
    assert "result store is disabled" in manifest


def test_search_matches_words_inside_camel_case_names() -> None:
    # MCP servers commonly expose camelCase tool names; substring scoring
    # covered them by accident, token matching must split case boundaries
    runtime = Toolplane(ambient_cli=False)

    @runtime.tool(name="createIssue", tags={"tracker"})
    def create_issue(repoName: str) -> str:
        """Open a ticket."""
        return ""

    for query in ("issue", "create", "createIssue", "repo name"):
        result = run(runtime.search(query))
        assert "createIssue" in result, (query, result)

    result = run(runtime.search("wiki"))
    assert result.startswith("No capabilities matched the query.")


def test_manifest_hides_scoped_sugar_the_default_backend_cannot_bind() -> None:
    # a cold agent read `await context7.query_docs(...)` in the manifest and
    # burned a NameError turn on the flat-only monty default (0.3.0
    # quickstart cert) — the manifest lists only forms this server binds
    def ask(question: str) -> str:
        """Answer a question."""
        return question

    monty_default = Toolplane(default_backend="monty", ambient_cli=False)
    monty_default.register_python_namespace("helper", {"ask": ask})
    manifest = monty_default.describe_namespace()
    assert "helper.ask" not in manifest
    assert "flat aliases above" in manifest

    local_default = Toolplane(default_backend="local_unsafe", ambient_cli=False)
    local_default.register_python_namespace("helper", {"ask": ask})
    assert "`await helper.ask(...)`" in local_default.describe_namespace()


def test_call_shape_orders_required_first_and_marks_optional() -> None:
    from toolplane.capabilities import Capability
    from toolplane.discovery import call_shape

    capability = Capability(
        name="mcp:crm/search_contacts",
        callable=lambda: None,
        description="Search contacts.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
        returns=None,
        tags=frozenset(),
        source="mcp:crm",
        aliases=frozenset({"crm_search_contacts"}),
    )
    # a truncated read must still see the mandatory part; optionals carry ?
    assert call_shape(capability) == (
        "await crm_search_contacts(query=<string>, limit=<integer>?)"
    )


def test_call_shape_falls_back_to_none_without_safe_binding() -> None:
    from toolplane.capabilities import Capability
    from toolplane.discovery import call_shape, render_capabilities

    capability = Capability(
        name="mcp:x/1weird-name",
        callable=lambda: None,
        description="No safe binding.",
        parameters={"type": "object", "properties": {}},
        returns=None,
        tags=frozenset(),
        source="mcp:x",
        aliases=frozenset({"class"}),  # keyword: unsafe as identifier
    )
    assert call_shape(capability) is None
    # brief rendering must not advertise an unresolvable call
    assert render_capabilities([capability]) == (
        "- mcp:x/1weird-name: No safe binding."
    )
