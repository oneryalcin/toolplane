"""Embed toolplane in a pydantic-ai agent as a single run_code tool.

Keyless run (default): wires the tool into an Agent with TestModel to prove
the integration, then invokes the tool directly to show the contract.
With ANTHROPIC_API_KEY or OPENAI_API_KEY set: a real model drives a
loop-shaped task through the one tool.

Run: uv run --no-project --with-editable . --with pydantic-ai python examples/as_tool_pydantic_ai.py
"""

from __future__ import annotations

import asyncio
import json
import os

from pydantic_ai import Agent

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
    tool = runtime.as_tool(backend="monty")
    print(f"tool: {tool.__name__} — description ({len(tool.__doc__ or '')} chars):")
    print(tool.__doc__)
    print()

    model = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        model = "anthropic:claude-sonnet-5"
    elif os.environ.get("OPENAI_API_KEY"):
        model = "openai:gpt-5.2"

    if model:
        agent = Agent(model, tools=[tool])
        result = await agent.run(TASK)
        print("agent output:", result.output)
        return

    # keyless: prove the wiring with TestModel, then show the real contract
    from pydantic_ai.models.test import TestModel

    Agent(TestModel(), tools=[tool])  # raises if the tool shape is wrong
    print("keyless mode: tool wired into Agent OK; direct invocation:")
    result = await tool(
        "log = await git('log', oneline=True, max_count=3)\n"
        "subjects = log['stdout'].splitlines()\n"
        "counts = []\n"
        "for line in subjects:\n"
        "    n = await word_count(text=line)\n"
        "    counts.append({'subject': line, 'words': n})\n"
        "return counts"
    )
    print(json.dumps(result["value"], indent=2))
    assert result["error"] is None, result["error"]


if __name__ == "__main__":
    asyncio.run(main())
