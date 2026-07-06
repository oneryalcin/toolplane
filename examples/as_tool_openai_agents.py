"""Embed toolplane in the OpenAI Agents SDK as a single run_code tool.

Keyless run (default): wraps the tool with function_tool to prove schema
extraction, then invokes the underlying tool directly to show the contract.
With OPENAI_API_KEY set: a real model drives a loop-shaped task through it.

Run: uv run --no-project --with-editable . --with openai-agents python examples/as_tool_openai_agents.py
"""

from __future__ import annotations

import asyncio
import json
import os

from agents import Agent, Runner, function_tool

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
    tool = function_tool(run_code)
    print(f"tool: {tool.name} | params: {tool.params_json_schema['properties']}")
    print(f"description ({len(tool.description or '')} chars):")
    print(tool.description)
    print()

    if os.environ.get("OPENAI_API_KEY"):
        agent = Agent(name="toolplane-demo", tools=[tool])
        result = await Runner.run(agent, TASK)
        print("agent output:", result.final_output)
        return

    print("keyless mode: function_tool schema extracted OK; direct invocation:")
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
