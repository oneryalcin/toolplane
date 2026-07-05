"""Embed toolplane in LangGraph (via langchain-core tools) as run_code.

Keyless run (default): wraps the tool with @tool to prove schema
extraction, then invokes the underlying function directly to show the
contract. With ANTHROPIC_API_KEY set: a real LangGraph ReAct agent drives
a loop-shaped task through it (requires --with langchain-anthropic
--with langgraph).

Run: uv run --no-project --with-editable . --with langchain-core python examples/as_tool_langgraph.py
"""

from __future__ import annotations

import asyncio
import json
import os

from langchain_core.tools import tool as lc_tool

from toolplane import Toolplane


def build_runtime() -> Toolplane:
    runtime = Toolplane(ambient_cli=True, ambient_cli_allowlist=["git"])

    @runtime.tool(tags={"metrics"})
    def word_count(text: str) -> int:
        """Count the words in a text."""
        return len(text.split())

    return runtime


TASK = (
    "Using the run_code tool once: get the last 3 git commit subjects "
    "(git log, oneline), count the words in each with word_count, and "
    "return a list of {subject, words} dicts."
)


async def main() -> None:
    runtime = build_runtime()
    run_code = runtime.as_tool(backend="monty")
    tool = lc_tool(run_code)
    print(f"tool: {tool.name} | args: {tool.args}")
    print(f"description ({len(tool.description)} chars):")
    print(tool.description)
    print()

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from langchain.chat_models import init_chat_model
            from langgraph.prebuilt import create_react_agent
        except ImportError:
            print(
                "ANTHROPIC_API_KEY is set but langgraph/langchain-anthropic "
                "are not installed; rerun with --with langgraph "
                "--with langchain-anthropic --with langchain"
            )
        else:
            model = init_chat_model("anthropic:claude-sonnet-5")
            agent = create_react_agent(model, [tool])
            state = await agent.ainvoke(
                {"messages": [{"role": "user", "content": TASK}]}
            )
            print("agent output:", state["messages"][-1].content)
            return

    print("keyless mode: @tool schema extracted OK; direct invocation:")
    result = await run_code(
        "log = await git('log', oneline=True, max_count=3)\n"
        "counts = []\n"
        "for line in log['stdout'].splitlines():\n"
        "    n = await word_count(text=line)\n"
        "    counts.append({'subject': line, 'words': n})\n"
        "return counts"
    )
    print(json.dumps(result["value"], indent=2))
    assert result["error"] is None, result["error"]


if __name__ == "__main__":
    asyncio.run(main())
