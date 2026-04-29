"""Mix ambient CLI, MCP, host Python helpers, stdlib, and pandas.

This example depends on Deno/Pyodide package loading, network access, and the
live Context7 service contract, so it is intentionally not part of
`make examples`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from toolplane import Toolplane


async def main() -> None:
    runtime = Toolplane()

    @runtime.tool(tags={"repo", "filesystem"})
    def read_text(path: str) -> str:
        return Path(path).read_text(encoding="utf-8", errors="ignore")

    @runtime.tool(tags={"repo"})
    def classify_path(path: str) -> str:
        if path.startswith("tests/"):
            return "tests"
        if path.startswith("docs/") or path == "README.md":
            return "docs"
        if path.startswith("examples/"):
            return "examples"
        if path.startswith("src/"):
            return "library"
        return "repo"

    await runtime.register_mcp_config(
        {
            "mcpServers": {
                "context7": {
                    "url": "https://mcp.context7.com/mcp",
                }
            }
        }
    )

    result = await runtime.execute(
        """
import ast
import pandas as pd

files = await git.diff(name_only=True, _=["HEAD~1", "HEAD"]).lines()

rows = []
python_files = 0
for path in files:
    area = await classify_path(path=path)
    if not path.endswith(".py"):
        continue
    python_files += 1
    source = await read_text(path=path)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                rows.append({
                    "path": path,
                    "area": area,
                    "import": alias.name.split(".")[0],
                })
        elif isinstance(node, ast.ImportFrom) and node.module:
            rows.append({
                "path": path,
                "area": area,
                "import": node.module.split(".")[0],
            })

df = pd.DataFrame(rows)
if len(df):
    summary = (
        df.groupby(["area", "import"])
        .size()
        .reset_index(name="count")
        .sort_values(["count", "area", "import"], ascending=[False, True, True])
    )
    summary_records = summary.head(10).to_dict("records")
else:
    summary_records = []

libraries = await context7_resolve_library_id(
    libraryName="pandas",
    query="pandas DataFrame groupby reset_index to_dict records",
)
docs = await context7_query_docs(
    libraryId="/pandas-dev/pandas",
    query="How do I group rows and convert a DataFrame to records?",
)

return {
    "changed_files": len(files),
    "python_files": python_files,
    "summary": summary_records,
    "context7_library_prefix": libraries[:240],
    "context7_docs_prefix": docs[:360],
}
""",
        backend="pyodide-deno",
        packages=["pandas"],
    )

    print("ok:", result.ok)
    if result.ok:
        print(json.dumps(result.value, indent=2))
    else:
        print(result.error)


if __name__ == "__main__":
    asyncio.run(main())
