"""Code-mode benchmark harness (#72).

Arm A ("direct"): the orders MCP server registered directly in Claude Code —
classic per-record tool invocations (the client may batch them in parallel
within one API request; tool invocations are NOT API round-trips).
Arm B ("toolplane"): the same server behind the toolplane facade — the agent
discovers capabilities and writes Python snippets against them.

Same model, same prompts, same data, fresh empty cwd per run (no CLAUDE.md,
no user MCP servers thanks to --strict-mcp-config). Results are published
win or lose; the harness exists to state the envelope, not a slogan.

The M axis (--servers, #107 item 8): each run can add distractor MCP servers
(realistic-but-irrelevant tool surfaces from distractor_server.py) to BOTH
arms — registered directly alongside orders in arm A, behind the toolplane
facade in arm B. M counts total configured servers including orders.

Usage:
    uv run python bench/run.py --reps 3 [--model sonnet] [--tasks loop,single,filter] [--servers 1,5,15]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
REPO_DIR = BENCH_DIR.parent
sys.path.insert(0, str(BENCH_DIR))

from orders_data import emea_over_500, orders, totals_by_region  # noqa: E402

ANSWER_RE = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.DOTALL)

_LOOP_PROMPT = (
    "Using the available tools, compute the total order amount per "
    "region across ALL orders in the store. Round each regional "
    "total to 2 decimals. Reply with ONLY the totals, one line per "
    "region formatted exactly as region,total, sorted "
    "alphabetically by region, wrapped in <answer></answer> tags."
)


def _check_region_totals(answer: str, n: int) -> bool:
    """Semantic comparison: agents legitimately render 4520.5 as 4520.50."""
    try:
        got = {}
        for line in answer.strip().splitlines():
            region, value = line.split(",")
            got[region.strip().lower()] = float(value)
    except (ValueError, AttributeError):
        return False
    want = totals_by_region(n)
    return set(got) == set(want) and all(
        abs(got[region] - want[region]) < 0.005 for region in want
    )


def _check_single(answer: str, n: int) -> bool:
    expected = next(
        o["status"] for o in orders(n) if o["order_id"] == "ORD-017"
    )
    return (answer or "").strip().lower() == expected


def _check_filter(answer: str, n: int) -> bool:
    try:
        return int((answer or "").strip()) == emea_over_500(n)
    except ValueError:
        return False


TASKS = {
    "loop": {
        "prompt": _LOOP_PROMPT,
        "check": _check_region_totals,
        "orders_n": 30,
    },
    "loop100": {
        "prompt": _LOOP_PROMPT,
        "check": _check_region_totals,
        "orders_n": 100,
    },
    "single": {
        "prompt": (
            "Using the available tools, find the status of order ORD-017. "
            "Reply with just the status word wrapped in <answer></answer> "
            "tags."
        ),
        "check": _check_single,
        "orders_n": 30,
    },
    "filter": {
        "prompt": (
            "Using the available tools, count how many orders in region "
            "emea have an amount greater than 500. Reply with just the "
            "number wrapped in <answer></answer> tags."
        ),
        "check": _check_filter,
        "orders_n": 30,
    },
}


# distractor rollout order for the M axis; M=15 wraps around with a second
# "-eu" workspace per profile, mimicking multi-tenant real setups
_PROFILES = ("crm", "calendar", "tickets", "wiki", "payments", "analytics", "files")


def distractors(m_servers: int) -> list[tuple[str, str]]:
    """(server_name, profile) pairs for M total servers (orders included)."""
    if m_servers - 1 > 2 * len(_PROFILES):
        raise ValueError(f"max servers is {2 * len(_PROFILES) + 1}")
    out = []
    for i in range(m_servers - 1):
        profile = _PROFILES[i % len(_PROFILES)]
        name = profile if i < len(_PROFILES) else f"{profile}-eu"
        out.append((name, profile))
    return out


def mcp_config(arm: str, workdir: Path, orders_n: int, m_servers: int) -> dict:
    def uv_cmd(script: str, env: dict) -> dict:
        return {
            "command": "uv",
            "args": [
                "run",
                "--project",
                str(REPO_DIR),
                "python",
                str(BENCH_DIR / script),
            ],
            "env": env,
        }

    server_cmd = uv_cmd("order_server.py", {"BENCH_ORDERS_N": str(orders_n)})
    extra = {
        name: uv_cmd("distractor_server.py", {"DISTRACTOR_PROFILE": profile})
        for name, profile in distractors(m_servers)
    }
    if arm == "direct":
        return {
            "mcpServers": {
                "orders": {**server_cmd, "type": "stdio"},
                **{name: {**cmd, "type": "stdio"} for name, cmd in extra.items()},
            }
        }
    if arm == "toolplane":
        # generated with absolute paths: every process here runs from a
        # scratch cwd, so nothing may be cwd-relative
        toml_path = workdir / f"toolplane-bench-{orders_n}-m{m_servers}.toml"
        sections = []
        for name, cmd in {"orders": server_cmd, **extra}.items():
            args_toml = ", ".join(json.dumps(a) for a in cmd["args"])
            env_toml = ", ".join(
                f'{k} = "{v}"' for k, v in cmd["env"].items()
            )
            sections.append(
                f'[mcp.servers."{name}"]\ncommand = "uv"\n'
                f"args = [{args_toml}]\nenv = {{ {env_toml} }}\n"
            )
        toml_path.write_text("\n".join(sections), encoding="utf-8")
        return {
            "mcpServers": {
                "toolplane": {
                    "type": "stdio",
                    "command": "uv",
                    "args": [
                        "run",
                        "--project",
                        str(REPO_DIR),
                        "toolplane",
                        "serve",
                        "mcp",
                        "--config",
                        str(toml_path),
                    ],
                }
            }
        }
    raise ValueError(arm)


def run_case(
    arm: str, task: str, model: str, workdir: Path, m_servers: int = 1
) -> dict:
    orders_n = TASKS[task]["orders_n"]
    config_path = workdir / f"mcp-{arm}-{orders_n}-m{m_servers}.json"
    config_path.write_text(
        json.dumps(mcp_config(arm, workdir, orders_n, m_servers)),
        encoding="utf-8",
    )
    cwd = workdir / f"cwd-{arm}-{task}-{time.time_ns()}"
    cwd.mkdir()

    cmd = [
        "claude",
        "-p",
        TASKS[task]["prompt"],
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--mcp-config",
        str(config_path),
        "--strict-mcp-config",
        "--permission-mode",
        "bypassPermissions",
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=900
        )
    except subprocess.TimeoutExpired as exc:
        # a hung run must not lose the rows already collected in memory
        return {
            "arm": arm,
            "task": task,
            "orders_n": orders_n,
            "m_servers": m_servers,
            "model": None,
            "correct": False,
            "answer": None,
            "tool_calls": 0,
            "tool_call_names": [],
            "num_turns": None,
            "input_tokens": 0,
            "uncached_input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": None,
            "api_duration_ms": None,
            "wall_s": round(time.monotonic() - started, 1),
            "exit_code": -1,
            "error": f"timeout after {exc.timeout}s",
        }
    wall_s = time.monotonic() - started

    tool_calls: list[str] = []
    result_event: dict = {}
    model_used = None
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            model_used = event.get("model")
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_calls.append(block.get("name", "?"))
        if event.get("type") == "result":
            result_event = event

    text = result_event.get("result", "") or ""
    match = ANSWER_RE.search(text)
    answer = match.group(1).strip() if match else None
    usage = result_event.get("usage", {})
    return {
        "arm": arm,
        "task": task,
        "orders_n": orders_n,
        "m_servers": m_servers,
        "model": model_used,
        "correct": TASKS[task]["check"](answer or "", orders_n),
        "answer": answer,
        "tool_calls": len(tool_calls),
        "tool_call_names": tool_calls,
        "num_turns": result_event.get("num_turns"),
        "input_tokens": usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0),
        "uncached_input_tokens": usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cost_usd": result_event.get("total_cost_usd"),
        "api_duration_ms": result_event.get("duration_api_ms"),
        "wall_s": round(wall_s, 1),
        "exit_code": proc.returncode,
    }


def summarize(rows: list[dict]) -> str:
    lines = [
        "| task | M | arm | ok | tool calls | turns | out tokens | uncached in | cost $ | wall s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    m_values = sorted({r.get("m_servers", 1) for r in rows})
    for task in TASKS:
        for m in m_values:
            for arm in ("direct", "toolplane"):
                group = [
                    r
                    for r in rows
                    if r["task"] == task
                    and r["arm"] == arm
                    and r.get("m_servers", 1) == m
                ]
                if not group:
                    continue

                def med(key):
                    vals = [r[key] for r in group if r[key] is not None]
                    return round(statistics.median(vals), 2) if vals else "-"

                ok = f"{sum(r['correct'] for r in group)}/{len(group)}"
                lines.append(
                    f"| {task} | {m} | {arm} | {ok} | {med('tool_calls')} | "
                    f"{med('num_turns')} | {med('output_tokens')} | "
                    f"{med('uncached_input_tokens')} | {med('cost_usd')} | "
                    f"{med('wall_s')} |"
                )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--tasks", default=",".join(TASKS))
    parser.add_argument("--arms", default="direct,toolplane")
    parser.add_argument(
        "--servers",
        default="1",
        help="comma-separated M values: total configured MCP servers "
        "(orders + M-1 distractors), e.g. 1,5,15",
    )
    args = parser.parse_args()

    tasks = [t for t in args.tasks.split(",") if t in TASKS]
    arms = args.arms.split(",")
    m_values = [int(m) for m in args.servers.split(",")]
    client_version = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True
    ).stdout.strip()
    rows = []
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        for rep in range(args.reps):
            for task in tasks:
                for m in m_values:
                    for arm in arms:
                        print(
                            f"[{rep + 1}/{args.reps}] {task}/M={m}/{arm} ...",
                            flush=True,
                        )
                        row = run_case(arm, task, args.model, workdir, m)
                        row["client_version"] = client_version
                        rows.append(row)
                        print(
                            f"  ok={row['correct']} tools={row['tool_calls']} "
                            f"turns={row['num_turns']} cost=${row['cost_usd']} "
                            f"wall={row['wall_s']}s",
                            flush=True,
                        )

    results_dir = BENCH_DIR / "results"
    results_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = results_dir / f"run-{stamp}.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nwrote {out}\n")
    print(summarize(rows))
    return 0 if all(r["exit_code"] == 0 for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
