"""Contract tests for the #104 transcript classifier.

The production bug each prevents: a swapped or over-eager retry/staging
split would misdirect the #106 discovery-tax work (teaching-surface fixes
vs nothing-to-fix), and a misparsed ExecutionResult would count failed
snippets as successes — execute_code failures are SUCCESSFUL MCP calls
carrying a non-null "error" payload.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

from classify import classify_run  # noqa: E402


def _assistant(tool_id: str, name: str, **input_kwargs) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": name,
                        "input": input_kwargs,
                    }
                ]
            },
        }
    )


def _result(tool_id: str, text: str, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "is_error": is_error,
                        "content": [{"type": "text", "text": text}],
                    }
                ]
            },
        }
    )


def _execute_payload(error_type: str | None) -> str:
    error = {"type": error_type, "message": "boom"} if error_type else None
    return json.dumps({"backend": "monty", "error": error, "value": None})


def _write(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "loop-toolplane-m1-rep1.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_failed_snippet_then_execute_is_retry(tmp_path: Path) -> None:
    lines = [
        _assistant("t1", "mcp__toolplane__search_capabilities", query="order"),
        _result("t1", "- mcp:orders/get_order: fetch one"),
        _assistant("t2", "mcp__toolplane__execute_code", code="x"),
        _result("t2", _execute_payload("MontySyntaxError")),
        _assistant("t3", "mcp__toolplane__execute_code", code="y"),
        _result("t3", _execute_payload(None)),
    ]
    run = classify_run(_write(tmp_path, lines))
    assert run["executes"] == 2
    assert run["failed_executes"] == 1
    assert run["retries_after_error"] == 1
    assert run["staged_after_success"] == 0
    assert run["snippet_error_types"] == {"MontySyntaxError": 1}


def test_successful_snippet_then_execute_is_staged(tmp_path: Path) -> None:
    lines = [
        _assistant("t1", "mcp__toolplane__execute_code", code="stage one"),
        _result("t1", _execute_payload(None)),
        _assistant("t2", "mcp__toolplane__execute_code", code="stage two"),
        _result("t2", _execute_payload(None)),
    ]
    run = classify_run(_write(tmp_path, lines))
    assert run["retries_after_error"] == 0
    assert run["staged_after_success"] == 1
    assert run["failed_executes"] == 0


def test_discovery_anatomy_measured_before_first_execute(tmp_path: Path) -> None:
    manifest = "namespace doc " * 50
    lines = [
        _assistant("t1", "mcp__toolplane__search_capabilities", query="order"),
        _result("t1", "- mcp:orders/get_order"),
        _assistant("t2", "ReadMcpResourceTool", uri="toolplane://namespace"),
        _result("t2", manifest),
        _assistant("t3", "mcp__toolplane__execute_code", code="z"),
        _result("t3", _execute_payload(None)),
    ]
    run = classify_run(_write(tmp_path, lines))
    names = [d["name"] for d in run["discovery_calls_before_first_execute"]]
    assert names == ["search_capabilities", "ReadMcpResourceTool"]
    assert run["manifest_read_chars"] == [len(manifest)]


def test_transport_error_counts_as_failure(tmp_path: Path) -> None:
    lines = [
        _assistant("t1", "mcp__toolplane__execute_code", code="x"),
        _result("t1", "MCP error -32000", is_error=True),
        _assistant("t2", "mcp__toolplane__execute_code", code="x again"),
        _result("t2", _execute_payload(None)),
    ]
    run = classify_run(_write(tmp_path, lines))
    assert run["failed_executes"] == 1
    assert run["retries_after_error"] == 1
    assert run["snippet_error_types"] == {"TransportError": 1}
