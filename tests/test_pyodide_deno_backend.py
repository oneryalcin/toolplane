from __future__ import annotations

import asyncio
import shutil

import pytest

from toolplane import PyodideDenoBackend, Toolplane

pytest.importorskip("fastmcp")
from fastmcp import FastMCP  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_pyodide_deno_reports_missing_deno() -> None:
    runtime = Toolplane(
        backends=[PyodideDenoBackend(deno_path="definitely-not-deno")]
    )

    result = run(runtime.execute("return 1", backend="pyodide-deno"))

    assert not result.ok
    assert result.error is not None
    assert result.error.type == "DenoNotFoundError"
    assert "Deno executable not found" in result.error.message


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_pyodide_deno_runs_pandas_and_host_callback() -> None:
    runtime = Toolplane(backends=[PyodideDenoBackend(timeout_seconds=240)])

    @runtime.tool
    def add(x: int, y: int) -> int:
        return x + y

    result = run(
        runtime.execute(
            """
import pandas as pd

x = await add(x=2, y=3)
status = await git.status(short=True).text()
df = pd.DataFrame([{"value": x}])
return {"value": int(df["value"].sum()), "status_is_text": isinstance(status, str)}
""",
            backend="pyodide-deno",
            packages=["pandas"],
        )
    )

    assert result.ok, result.error
    assert result.value == {"value": 5, "status_is_text": True}
    assert result.backend == "pyodide-deno"


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_pyodide_deno_mixes_cli_mcp_host_python_stdlib_and_pandas() -> None:
    async def exercise() -> dict:
        runtime = Toolplane(backends=[PyodideDenoBackend(timeout_seconds=240)])
        mcp = FastMCP("Docs")

        @mcp.tool
        def lookup(topic: str) -> dict:
            return {"topic": topic, "hint": "use groupby then reset_index"}

        def read_text(path: str) -> str:
            assert path == "src/sample.py"
            return "import os\nfrom collections import defaultdict\n"

        def classify_path(path: str) -> str:
            return "library" if path.startswith("src/") else "repo"

        await runtime.register_mcp("docs", mcp)
        runtime.register_python_namespace(
            "repo",
            {
                "read_text": read_text,
                "classify_path": classify_path,
            },
        )

        result = await runtime.execute(
            """
import ast
import pandas as pd

status = await git.status(short=True).text()
path = "src/sample.py"
source = await repo.read_text(path=path)
area = await repo.classify_path(path=path)
tree = ast.parse(source)
rows = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            rows.append({"area": area, "import": alias.name.split(".")[0]})
    elif isinstance(node, ast.ImportFrom) and node.module:
        rows.append({"area": area, "import": node.module.split(".")[0]})

df = pd.DataFrame(rows)
summary = (
    df.groupby(["area", "import"])
    .size()
    .reset_index(name="count")
    .sort_values(["area", "import"])
    .to_dict("records")
)
docs_result = await docs.lookup(topic="pandas groupby")
return {
    "status_is_text": isinstance(status, str),
    "summary": summary,
    "docs": docs_result,
}
""",
            backend="pyodide-deno",
            packages=["pandas"],
        )
        assert result.ok, result.error
        return result.value

    assert run(exercise()) == {
        "status_is_text": True,
        "summary": [
            {"area": "library", "import": "collections", "count": 1},
            {"area": "library", "import": "os", "count": 1},
        ],
        "docs": {"topic": "pandas groupby", "hint": "use groupby then reset_index"},
    }


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_pyodide_save_result_non_json_error_carries_guidance() -> None:
    async def exercise():
        runtime = Toolplane(ambient_cli=False)
        return await runtime.execute(
            """
try:
    await save_result({1, 2})
except Exception as exc:
    return str(exc)
""",
            backend="pyodide-deno",
        )

    result = run(exercise())

    assert result.error is None, result.error
    assert "save a JSON-shaped projection instead" in result.value
    assert "set" in result.value


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_pyodide_nested_unawaited_call_fails_instead_of_corrupting() -> None:
    async def exercise():
        runtime = Toolplane(ambient_cli=False)
        return await runtime.execute(
            "return {'h': save_result({'v': 1}), 'n': 5}",
            backend="pyodide-deno",
        )

    result = run(exercise())

    # previously serialized the coroutine to {} with error=None
    assert result.error is not None
    assert result.error.type == "UnawaitedToolCallError"
    assert "await" in result.error.message


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_pyodide_canonical_call_tool_non_json_error_carries_guidance() -> None:
    async def exercise():
        runtime = Toolplane(ambient_cli=False)
        return await runtime.execute(
            """
try:
    await call_tool("toolplane:results/save", {"value": {1, 2}})
except Exception as exc:
    return str(exc)
""",
            backend="pyodide-deno",
        )

    result = run(exercise())

    assert result.error is None, result.error
    assert "save a JSON-shaped projection instead" in result.value


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_pyodide_user_value_matching_old_sentinel_returns_unchanged() -> None:
    async def exercise():
        runtime = Toolplane(ambient_cli=False)
        return await runtime.execute(
            "return {'__toolplane_unawaited_call__': True, 'data': 123}",
            backend="pyodide-deno",
        )

    result = run(exercise())

    # the unawaited signal is out-of-band; no user JSON shape is reserved
    assert result.error is None, result.error
    assert result.value == {"__toolplane_unawaited_call__": True, "data": 123}


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_pyodide_assign_then_print_unawaited_fails() -> None:
    async def exercise():
        runtime = Toolplane(ambient_cli=False)
        return await runtime.execute(
            "result = save_result({'a': 1})\nprint(result)",
            backend="pyodide-deno",
        )

    result = run(exercise())

    # the driver's original repro: assign, then print to inspect
    assert result.error is not None
    assert result.error.type == "UnawaitedToolCallError"


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_pyodide_uncaught_error_carries_real_type_and_message() -> None:
    async def exercise():
        runtime = Toolplane(ambient_cli=False)
        return await runtime.execute("return 1/0", backend="pyodide-deno")

    result = run(exercise())

    # the Deno layer reports 'PythonError'/'PythonError'; the real error
    # must be recovered from the captured traceback
    assert result.error is not None
    assert result.error.type == "ZeroDivisionError"
    assert result.error.message == "division by zero"


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_pyodide_cli_policy_rejection_is_legible() -> None:
    async def exercise():
        runtime = Toolplane(
            ambient_cli=True, ambient_cli_allowlist=["git"]
        )
        return await runtime.execute(
            "return await cli.curl()", backend="pyodide-deno"
        )

    result = run(exercise())

    # the policy signpost must survive the Deno error path
    assert result.error is not None
    assert result.error.type == "RuntimeError"
    assert "not allowed by Toolplane policy: curl" in result.error.message
    assert "Allowed binaries: git" in result.error.message


def test_recover_python_error_keeps_opaque_when_no_traceback() -> None:
    from toolplane.backends.pyodide_deno import _recover_python_error

    # no traceback in stderr -> caller must keep the original error rather
    # than fabricate one from unrelated stderr noise
    assert _recover_python_error("") is None
    assert _recover_python_error("some print to stderr\nmore noise") is None
