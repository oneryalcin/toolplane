from __future__ import annotations

import asyncio
import shutil

import pytest

from toolplane import PyodideDenoBackend, Toolplane


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
    runtime = Toolplane(backends=[PyodideDenoBackend(timeout_seconds=120)])

    @runtime.tool
    def add(x: int, y: int) -> int:
        return x + y

    result = run(
        runtime.execute(
            """
import pandas as pd

x = await add(x=2, y=3)
df = pd.DataFrame([{"value": x}])
return int(df["value"].sum())
""",
            backend="pyodide-deno",
            packages=["pandas"],
        )
    )

    assert result.ok, result.error
    assert result.value == 5
    assert result.backend == "pyodide-deno"
