"""Longitudinal conversation and Monty snapshot benchmark (#119).

The conversation benchmark keeps one Claude Code process and one MCP server
alive for every user turn. Prompts are written only after the prior ``result``
event: pre-buffering JSONL messages can steer an in-flight turn instead of
creating a clean next turn.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import re
import selectors
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable

BENCH_DIR = Path(__file__).resolve().parent
REPO_DIR = BENCH_DIR.parent
sys.path.insert(0, str(BENCH_DIR))

import run as base  # noqa: E402
from orders_data import orders  # noqa: E402

N = 30
RECORD_BYTES = 2_000
_ANSWER_RE = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.DOTALL)


def _region_lines(values: dict[str, float]) -> str:
    return "\n".join(f"{key},{values[key]:.2f}" for key in sorted(values))


def _check_region_totals(answer: str) -> bool:
    return base._check_region_totals(answer, N)


def _check_int(expected: int) -> Callable[[str], bool]:
    def check(answer: str) -> bool:
        try:
            return int(answer.strip()) == expected
        except ValueError:
            return False

    return check


def _check_pair(expected_id: str, expected_value: float) -> Callable[[str], bool]:
    def check(answer: str) -> bool:
        try:
            got_id, got_value = (part.strip() for part in answer.split(","))
            return got_id == expected_id and abs(float(got_value) - expected_value) < 0.005
        except (ValueError, AttributeError):
            return False

    return check


def _check_region_values(expected: dict[str, float]) -> Callable[[str], bool]:
    def check(answer: str) -> bool:
        try:
            got = {}
            for line in answer.strip().splitlines():
                key, value = line.split(",")
                got[key.strip().lower()] = float(value)
        except (ValueError, AttributeError):
            return False
        return set(got) == set(expected) and all(
            abs(got[key] - expected[key]) < 0.005 for key in expected
        )

    return check


_DATA = orders(N)
_HIGHEST = max(_DATA, key=lambda row: row["amount"])
_AVERAGES = {
    region: round(
        sum(row["amount"] for row in _DATA if row["region"] == region)
        / sum(row["region"] == region for row in _DATA),
        2,
    )
    for region in {row["region"] for row in _DATA}
}
_PENDING = sum(row["status"] == "pending" for row in _DATA)

TASKS: tuple[dict[str, Any], ...] = (
    {
        "name": "load_and_total",
        "prompt": (
            "This is the first of several related questions about the same order "
            "store. Do not use Bash, files, web, or helper agents. Use only the "
            "available MCP tools. If your tool surface supports persistent Python "
            "session state, retain the complete fetched order list in a variable "
            "named orders_cache for later questions. Compute total order amount per "
            "region across all orders, rounded to 2 decimals. Reply only with "
            "alphabetically sorted region,total lines inside <answer></answer>."
        ),
        "check": _check_region_totals,
    },
    {
        "name": "filter_reuse",
        "prompt": (
            "Using the same order data from the prior turn, count EMEA orders with "
            "amount greater than 500. Reuse already-available data rather than "
            "refetching it when your tool surface supports that. Reply only with the "
            "integer inside <answer></answer>."
        ),
        "check": _check_int(sum(
            row["region"] == "emea" and row["amount"] > 500 for row in _DATA
        )),
    },
    {
        "name": "maximum_reuse",
        "prompt": (
            "Using the same order data, find the order with the largest amount. Reuse "
            "already-available data when possible. Reply only as order_id,amount "
            "inside <answer></answer>, with amount to 2 decimals."
        ),
        "check": _check_pair(_HIGHEST["order_id"], _HIGHEST["amount"]),
    },
    {
        "name": "averages_reuse",
        "prompt": (
            "Using the same order data, compute average amount per region, rounded to "
            "2 decimals. Reuse already-available data when possible. Reply only with "
            "alphabetically sorted region,average lines inside <answer></answer>."
        ),
        "check": _check_region_values(_AVERAGES),
    },
    {
        "name": "status_reuse",
        "prompt": (
            "Using the same order data, count orders whose status is pending. Reuse "
            "already-available data when possible. Reply only with the integer inside "
            "<answer></answer>."
        ),
        "check": _check_int(_PENDING),
    },
    {
        "name": "reset_and_refetch",
        "prompt": (
            "If the Toolplane execute_code surface is available, call "
            "`await reset_session()` exactly in its own tool execution before the "
            "calculation; assigning one cached variable to None is not a session "
            "reset. If that surface is unavailable, skip the reset. Then use the MCP "
            "tools again to recompute total order amount per region across all orders. "
            "Reply only with alphabetically sorted region,total lines inside "
            "<answer></answer>."
        ),
        "check": _check_region_totals,
        "reset_phase": True,
    },
)


def _user_message(prompt: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            },
        }
    )


def _answer(result: dict[str, Any]) -> str:
    match = _ANSWER_RE.search(result.get("result", ""))
    return match.group(1).strip() if match else ""


def _turn_usage(result: dict[str, Any]) -> dict[str, int]:
    """Result usage is per user turn; unlike total_cost_usd, never delta it."""
    usage = result.get("usage", {})
    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(
            usage.get("cache_creation_input_tokens", 0) or 0
        ),
        "cache_read_input_tokens": int(
            usage.get("cache_read_input_tokens", 0) or 0
        ),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
    }


def _context_tokens(events: list[dict[str, Any]]) -> int:
    totals = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        usage = event.get("message", {}).get("usage", {})
        totals.append(
            sum(
                int(usage.get(key, 0) or 0)
                for key in (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            )
        )
    return max(totals, default=0)


def _tool_names(events: list[dict[str, Any]]) -> list[str]:
    names = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                names.append(block.get("name", ""))
    return names


def _is_dedicated_reset_code(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    if not tree.body:
        return False
    first = tree.body[0]
    resets_first = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Await)
        and isinstance(first.value.value, ast.Call)
        and isinstance(first.value.value.func, ast.Name)
        and first.value.value.func.id == "reset_session"
        and not first.value.value.args
        and not first.value.value.keywords
    )
    inert_tail = all(
        isinstance(node, ast.Return)
        and (node.value is None or isinstance(node.value, ast.Constant))
        for node in tree.body[1:]
    )
    calls_orders = any(
        isinstance(node, ast.Name) and node.id.startswith("orders_")
        for node in ast.walk(tree)
    )
    return resets_first and inert_tail and not calls_orders


def _uses_reset_contract(events: list[dict[str, Any]]) -> bool:
    reset_index: int | None = None
    reset_tool_id: str | None = None
    later_execute_index: int | None = None
    for index, event in enumerate(events):
        if event.get("type") != "assistant":
            continue
        tool_uses = [
            block
            for block in event.get("message", {}).get("content", [])
            if block.get("type") == "tool_use"
        ]
        executes = [
            block
            for block in tool_uses
            if block.get("name", "").endswith("execute_code")
        ]
        if not executes:
            continue
        if reset_index is None:
            if (
                len(tool_uses) != 1
                or len(executes) != 1
                or not _is_dedicated_reset_code(
                    str(executes[0].get("input", {}).get("code", ""))
                )
            ):
                return False
            reset_index = index
            reset_tool_id = str(executes[0].get("id", ""))
        else:
            later_execute_index = index
            break
    if reset_index is None or not reset_tool_id or later_execute_index is None:
        return False
    for event in events[reset_index + 1 : later_execute_index]:
        for block in event.get("message", {}).get("content", []):
            if (
                block.get("type") == "tool_result"
                and block.get("tool_use_id") == reset_tool_id
            ):
                return block.get("is_error") is not True
    return False


def _call_log_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _config_with_call_log(
    arm: str, workdir: Path, code: dict[str, Any], call_log: Path
) -> dict[str, Any]:
    prior = base.TASKS["loop"].get("server_env")
    base.TASKS["loop"]["server_env"] = {"BENCH_CALL_LOG": str(call_log)}
    try:
        return base.mcp_config(
            arm,
            workdir,
            "loop",
            1,
            code,
            RECORD_BYTES,
            "fetch-one",
        )
    finally:
        if prior is None:
            base.TASKS["loop"].pop("server_env", None)
        else:
            base.TASKS["loop"]["server_env"] = prior


def run_session(
    arm: str,
    model: str,
    workdir: Path,
    code: dict[str, Any],
    transcript_path: Path,
) -> dict[str, Any]:
    call_log = workdir / f"calls-{arm}-{time.time_ns()}.jsonl"
    config_path = workdir / f"mcp-longitudinal-{arm}.json"
    config_path.write_text(
        json.dumps(_config_with_call_log(arm, workdir, code, call_log)),
        encoding="utf-8",
    )
    cwd = workdir / f"cwd-longitudinal-{arm}-{time.time_ns()}"
    cwd.mkdir()
    cmd = [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--mcp-config",
        str(config_path),
        "--strict-mcp-config",
        "--permission-mode",
        "bypassPermissions",
        "--disallowedTools",
        "Bash,Read,Write,Edit,NotebookEdit,WebFetch,WebSearch,Task",
    ]
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin and process.stdout and process.stderr
    stderr_lines: list[str] = []
    stderr_thread = threading.Thread(
        target=lambda: stderr_lines.extend(process.stderr.readlines()), daemon=True
    )
    stderr_thread.start()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    all_lines: list[str] = []
    turns = []
    previous_cost = 0.0
    prior_calls = 0
    session_id: str | None = None
    try:
        for index, task in enumerate(TASKS, 1):
            process.stdin.write(_user_message(task["prompt"]) + "\n")
            process.stdin.flush()
            events: list[dict[str, Any]] = []
            result: dict[str, Any] | None = None
            deadline = time.monotonic() + 180
            while result is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"turn {index} timed out")
                if not selector.select(timeout=min(remaining, 5)):
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"claude exited {process.returncode} before turn {index}"
                        )
                    continue
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(f"claude stdout closed before turn {index}")
                all_lines.append(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(event)
                if event.get("session_id"):
                    if session_id is None:
                        session_id = event["session_id"]
                    elif event["session_id"] != session_id:
                        raise RuntimeError("Claude session id changed mid-conversation")
                if event.get("type") == "result":
                    result = event
            turn_usage = _turn_usage(result)
            cumulative_cost = float(result.get("total_cost_usd", 0) or 0)
            turn_cost = cumulative_cost - previous_cost
            previous_cost = cumulative_cost
            calls = _call_log_rows(call_log)
            turn_calls = calls[prior_calls:]
            prior_calls = len(calls)
            answer = _answer(result)
            used_reset_contract = _uses_reset_contract(events)
            turns.append(
                {
                    "turn": index,
                    "task": task["name"],
                    "answer": answer,
                    "correct": bool(task["check"](answer)),
                    "cost_usd": turn_cost,
                    **turn_usage,
                    "peak_request_context_tokens": _context_tokens(events),
                    "model_requests": base._unique_request_ids("".join(
                        json.dumps(event) + "\n" for event in events
                    )),
                    "tool_names": _tool_names(events),
                    "fixture_calls": turn_calls,
                    "fixture_call_count": len(turn_calls),
                    "used_reset_session": used_reset_contract,
                    "compaction_events": sum(
                        "compact" in str(event.get("subtype", "")).lower()
                        or bool(event.get("message", {}).get("context_management"))
                        for event in events
                    ),
                }
            )
        process.stdin.close()
        process.wait(timeout=30)
    except Exception:
        process.kill()
        process.wait(timeout=10)
        raise
    finally:
        selector.close()
        stderr_thread.join(timeout=2)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        base._redacted_transcript("".join(all_lines)), encoding="utf-8"
    )
    return {
        "arm": arm,
        "session_id": session_id,
        "exit_code": process.returncode,
        "stderr": "".join(stderr_lines),
        "turns": turns,
        "all_correct": all(turn["correct"] for turn in turns),
        "reuse_turns_correct": all(turn["correct"] for turn in turns[:5]),
        "reset_turn_correct": turns[-1]["correct"],
        "reset_verified": (
            (
                turns[-1]["used_reset_session"]
                and turns[-1]["fixture_call_count"] > 0
            )
            if arm == "toolplane"
            else True
        ),
        "total_cost_usd": previous_cost,
        "peak_context_tokens": max(
            turn["peak_request_context_tokens"] for turn in turns
        ),
        "total_fixture_calls": sum(turn["fixture_call_count"] for turn in turns),
        "transcript": str(transcript_path),
    }


async def snapshot_cell(size: int, repeats: int = 7) -> dict[str, Any]:
    from toolplane import Toolplane

    runtime = Toolplane(
        ambient_cli=False, sessions=True, default_backend="monty"
    )
    seeded = await runtime.execute(
        f'snapshot_blob = "x" * {size}\nreturn len(snapshot_blob)'
    )
    if seeded.error or seeded.value != size:
        raise RuntimeError(f"failed to seed {size}: {seeded}")
    backend = runtime.backends["monty"]
    # pool API (#88): the live session is a pool checkout and dump() is an
    # awaited IPC round-trip to the worker — measured numbers on 0.0.19 are
    # not comparable to the 0.0.18 in-process MontyRepl rows
    session = backend._checkout_session
    if session is None:
        raise RuntimeError("session checkout was not created")
    dump_ms = []
    dump_bytes = []
    dump_peak_bytes = []
    for _ in range(repeats):
        started = time.perf_counter()
        blob = await session.dump()
        dump_ms.append((time.perf_counter() - started) * 1000)
        dump_bytes.append(len(blob))
    for _ in range(repeats):
        tracemalloc.start()
        blob = await session.dump()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        dump_peak_bytes.append(peak)
    noop_ms = []
    for _ in range(repeats):
        result = await runtime.execute("return 1")
        if result.error or result.value != 1:
            raise RuntimeError(f"snapshot noop failed: {result}")
        noop_ms.append(result.duration_ms)
    return {
        "namespace_payload_bytes": size,
        "snapshot_bytes": int(statistics.median(dump_bytes)),
        "dump_ms_median": statistics.median(dump_ms),
        "dump_ms_range": [min(dump_ms), max(dump_ms)],
        "dump_python_peak_bytes_median": int(statistics.median(dump_peak_bytes)),
        "noop_execute_ms_median": statistics.median(noop_ms),
        "noop_execute_ms_range": [min(noop_ms), max(noop_ms)],
        "repeats": repeats,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--arms", default="direct,toolplane")
    parser.add_argument("--snapshot-only", action="store_true")
    args = parser.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if args.snapshot_only:
        snapshot_rows = [
            asyncio.run(snapshot_cell(size))
            for size in (1_000, 100_000, 10_000_000)
        ]
        print(json.dumps(snapshot_rows, indent=2))
        return 0
    result_path = BENCH_DIR / "results" / f"longitudinal-{stamp}.json"
    transcript_dir = BENCH_DIR / "results" / "transcripts" / f"longitudinal-{stamp}"
    rows = []
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        code = base.build_code_under_test(workdir)
        provenance = base.provenance_row(code)
        provenance["longitudinal_harness_sha256"] = base._sha256(Path(__file__))
        snapshot_process = subprocess.run(
            [code["python"], str(Path(__file__)), "--snapshot-only"],
            check=True,
            capture_output=True,
            text=True,
        )
        snapshot_rows = json.loads(snapshot_process.stdout)
        arms = [arm for arm in args.arms.split(",") if arm]
        for rep in range(args.reps):
            for arm in base.arm_order(arms, rep):
                print(f"[{rep + 1}/{args.reps}] longitudinal/{arm}", flush=True)
                row = run_session(
                    arm,
                    args.model,
                    workdir,
                    code,
                    transcript_dir / f"{arm}-rep{rep + 1}.jsonl",
                )
                row.update(
                    {
                        "rep": rep + 1,
                        "model": args.model,
                        "record_bytes": RECORD_BYTES,
                        "orders_n": N,
                        **provenance,
                    }
                )
                rows.append(row)
                print(
                    f"  correct={row['all_correct']} "
                    f"cost=${row['total_cost_usd']:.2f} "
                    f"peak_ctx={row['peak_context_tokens']} "
                    f"fixture_calls={row['total_fixture_calls']}",
                    flush=True,
                )
    result_path.write_text(
        json.dumps(
            {
                "experiment": "#119 longitudinal sessions",
                "created_at": stamp,
                "rows": rows,
                "snapshot_scaling": snapshot_rows,
                "snapshot_provenance": provenance,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(result_path)
    return 0 if all(row["all_correct"] and row["reset_verified"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
