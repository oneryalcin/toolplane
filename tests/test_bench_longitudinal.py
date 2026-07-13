"""Contracts for the longitudinal-session benchmark (#119)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

import longitudinal  # noqa: E402


def test_longitudinal_validators_cover_every_turn() -> None:
    answers = (
        "amer,4520.50\napac,4666.50\nemea,5043.50",
        "5",
        "ORD-023,967.10",
        "amer,452.05\napac,466.65\nemea,504.35",
        "7",
        "amer,4520.50\napac,4666.50\nemea,5043.50",
    )
    assert len(answers) == len(longitudinal.TASKS)
    assert all(task["check"](answer) for task, answer in zip(longitudinal.TASKS, answers))
    assert not longitudinal.TASKS[2]["check"]("ORD-023,967.11")


def test_stream_metrics_extract_context_tools_answer_and_reset() -> None:
    events = [
        {
            "type": "assistant",
            "request_id": "req-1",
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 30,
                },
                "content": [
                    {
                        "type": "tool_use",
                        "name": "mcp__toolplane__execute_code",
                        "input": {"code": "await reset_session()"},
                    }
                ],
            },
        }
    ]
    result = {"result": "prefix <answer> 42 </answer> suffix"}

    assert longitudinal._answer(result) == "42"
    assert longitudinal._context_tokens(events) == 60
    assert longitudinal._tool_names(events) == ["mcp__toolplane__execute_code"]
    events.append(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "mcp__toolplane__execute_code",
                        "input": {"code": "return 1"},
                    }
                ]
            },
        }
    )
    assert longitudinal._uses_reset_contract(events)


def test_result_usage_is_per_turn_not_a_cumulative_delta() -> None:
    first = {"usage": {"input_tokens": 8, "cache_creation_input_tokens": 50_000}}
    smaller_second = {
        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1_000}
    }

    assert longitudinal._turn_usage(first)["cache_creation_input_tokens"] == 50_000
    assert (
        longitudinal._turn_usage(smaller_second)["cache_creation_input_tokens"]
        == 1_000
    )


def test_reset_detector_rejects_search_and_same_execution() -> None:
    search = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "ToolSearch",
                        "input": {"query": "reset_session"},
                    }
                ]
            },
        }
    ]
    combined = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "mcp__toolplane__execute_code",
                        "input": {
                            "code": "await reset_session()\nreturn await orders_list_order_ids()"
                        },
                    }
                ]
            },
        }
    ]
    assert not longitudinal._uses_reset_contract(search)
    assert not longitudinal._uses_reset_contract(combined)
    for false_positive in (
        'return "await reset_session()"',
        "# await reset_session()\nreturn 1",
        "if False:\n    await reset_session()\nreturn 1",
        'return "done"\nawait reset_session()',
        'raise Exception("stop")\nawait reset_session()',
        'await reset_session()\nmarker = "not dedicated"',
    ):
        assert not longitudinal._is_dedicated_reset_code(false_positive)
    assert longitudinal._is_dedicated_reset_code(
        'await reset_session()\nreturn "reset"'
    )


def test_call_log_is_wired_through_both_arms(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    code = {
        "fixtures_dir": str(fixtures),
        "python": "/frozen/python",
        "toolplane_bin": "/frozen/toolplane",
    }
    call_log = tmp_path / "calls.jsonl"

    direct = longitudinal._config_with_call_log(
        "direct", tmp_path, code, call_log
    )
    assert (
        direct["mcpServers"]["orders"]["env"]["BENCH_CALL_LOG"]
        == str(call_log)
    )

    toolplane = longitudinal._config_with_call_log(
        "toolplane", tmp_path, code, call_log
    )
    config_path = Path(toolplane["mcpServers"]["toolplane"]["args"][-1])
    assert f'BENCH_CALL_LOG = "{call_log}"' in config_path.read_text()


def test_call_log_reader_handles_missing_and_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    assert longitudinal._call_log_rows(path) == []
    path.write_text(
        json.dumps({"tool": "get_order", "params": {"order_id": "ORD-001"}})
        + "\n"
    )
    assert longitudinal._call_log_rows(path)[0]["tool"] == "get_order"


def test_snapshot_cell_measures_serialized_state() -> None:
    row = asyncio.run(longitudinal.snapshot_cell(1_000, repeats=3))

    assert row["namespace_payload_bytes"] == 1_000
    assert row["snapshot_bytes"] >= 1_000
    assert row["dump_python_peak_bytes_median"] >= 1_000
    assert row["dump_ms_median"] >= 0
    assert row["noop_execute_ms_median"] >= 0
