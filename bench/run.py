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
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
REPO_DIR = BENCH_DIR.parent
sys.path.insert(0, str(BENCH_DIR))

from orders_data import (  # noqa: E402
    chain_answer,
    emea_over_500,
    orders,
    totals_by_region,
)

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


def _check_chain(answer: str, n: int) -> bool:
    want = chain_answer(n)
    try:
        order_id, status = (part.strip() for part in answer.strip().split(","))
    except (ValueError, AttributeError):
        return False
    return (
        order_id.upper() == want["order_id"]
        and status.lower() == want["status"]
    )


_CHAIN_PROMPT = (
    "Using the available tools, start at order ORD-001 and follow the "
    "follow-up chain for exactly 4 hops. Each order's note says which "
    "order continues the case — and also mentions an unrelated or "
    "cancelled order that does NOT. Reply with the final order's id and "
    "status formatted exactly as order_id,status wrapped in "
    "<answer></answer> tags."
)

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
    # crossover localization under N=30 (#107 item 1, post-#111 envelope)
    "loop5": {
        "prompt": _LOOP_PROMPT,
        "check": _check_region_totals,
        "orders_n": 5,
    },
    "loop10": {
        "prompt": _LOOP_PROMPT,
        "check": _check_region_totals,
        "orders_n": 10,
    },
    "loop20": {
        "prompt": _LOOP_PROMPT,
        "check": _check_region_totals,
        "orders_n": 20,
    },
    # the shape prior work says code mode loses (#107 item 2): each note
    # names the next order plus a decoy, inviting judgment per hop. The
    # templated prose IS heuristically separable (a keyword regex walks
    # the chain — review-verified), so this measures what agents choose
    # to do with an adaptive-looking task, not impossibility.
    "chain": {
        "prompt": _CHAIN_PROMPT,
        "check": _check_chain,
        "orders_n": 30,
        "server_env": {"BENCH_NOTES": "chain"},
    },
    # latency axis (#107 item 10 / #109 gate): 100ms per tool call —
    # direct batches in parallel, monty awaits sequentially
    "loop_lat100": {
        "prompt": _LOOP_PROMPT,
        "check": _check_region_totals,
        "orders_n": 30,
        "server_env": {"BENCH_TOOL_LATENCY_MS": "100"},
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
    if m_servers < 1:
        raise ValueError("m_servers counts total servers including orders; min is 1")
    if m_servers - 1 > 2 * len(_PROFILES):
        raise ValueError(f"max servers is {2 * len(_PROFILES) + 1}")
    out = []
    for i in range(m_servers - 1):
        profile = _PROFILES[i % len(_PROFILES)]
        name = profile if i < len(_PROFILES) else f"{profile}-eu"
        out.append((name, profile))
    return out


def task_server_env(task: str) -> dict[str, str]:
    return {
        "BENCH_ORDERS_N": str(TASKS[task]["orders_n"]),
        **TASKS[task].get("server_env", {}),
    }


# every file whose bytes are part of the measurement: the fixtures are
# snapshotted into the run's scratch dir and served from there, so an edit
# to the working tree mid-matrix cannot reach a running measurement (the
# #111 contamination incident; #116 item 2)
_FIXTURE_FILES = ("order_server.py", "distractor_server.py", "orders_data.py")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_code_under_test(workdir: Path) -> dict:
    """Freeze the code under test and return its provenance.

    The toolplane facade is built into a wheel and installed into a scratch
    venv; the bench fixture scripts are copied next to it. Every server the
    matrix spawns runs from these frozen copies. The returned dict carries
    both the execution paths and the provenance recorded on every result
    row (git SHA + dirty flag + wheel/fixture hashes) so any published
    number can be traced to exact bytes.
    """
    git_sha = subprocess.run(
        ["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    git_dirty = bool(
        subprocess.run(
            ["git", "-C", str(REPO_DIR), "status", "--porcelain"],
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    dist = workdir / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "-o", str(dist), "--project", str(REPO_DIR)],
        check=True,
        capture_output=True,
    )
    wheels = sorted(dist.glob("toolplane-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel in {dist}, got {wheels}")
    wheel = wheels[0]
    venv = workdir / "venv"
    python = venv / "bin" / "python"
    subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        check=True,
        capture_output=True,
    )
    fixtures = workdir / "fixtures"
    fixtures.mkdir()
    fixture_hashes = {}
    for name in _FIXTURE_FILES:
        shutil.copy2(BENCH_DIR / name, fixtures / name)
        fixture_hashes[name] = _sha256(fixtures / name)
    return {
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "wheel": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "fixtures_sha256": fixture_hashes,
        "python": str(python),
        "toolplane_bin": str(venv / "bin" / "toolplane"),
        "fixtures_dir": str(fixtures),
    }


def provenance_row(code: dict) -> dict:
    """The provenance subset stamped onto every result row."""
    return {
        "git_sha": code["git_sha"],
        "git_dirty": code["git_dirty"],
        "wheel_sha256": code["wheel_sha256"],
        "fixtures_sha256": code["fixtures_sha256"],
    }


def mcp_config(
    arm: str, workdir: Path, task: str, m_servers: int, code: dict
) -> dict:
    fixtures_dir = Path(code["fixtures_dir"])

    def frozen_cmd(script: str, env: dict) -> dict:
        return {
            "command": code["python"],
            "args": [str(fixtures_dir / script)],
            "env": env,
        }

    server_cmd = frozen_cmd("order_server.py", task_server_env(task))
    extra = {
        name: frozen_cmd("distractor_server.py", {"DISTRACTOR_PROFILE": profile})
        for name, profile in distractors(m_servers)
    }
    if arm == "direct":
        return {
            "mcpServers": {
                "orders": {**server_cmd, "type": "stdio"},
                **{name: {**cmd, "type": "stdio"} for name, cmd in extra.items()},
            }
        }
    # all three toolplane arms serve the SAME servers through the SAME
    # facade. "hybrid" adds --hybrid (re-export the WHOLE registry, #114's
    # held baseline); "curated" adds a [hybrid] config section that
    # re-exports ONLY the orders tools (#125 — the selective form).
    if arm in ("toolplane", "hybrid", "curated"):
        # generated with absolute paths: every process here runs from a
        # scratch cwd, so nothing may be cwd-relative
        toml_path = workdir / f"toolplane-bench-{arm}-{task}-m{m_servers}.toml"
        sections = []
        if arm == "curated":
            # curate the single/adaptive capabilities: the orders server's
            # tools, by canonical-name glob. Distractors stay behind the
            # facade — the whole #125 hypothesis.
            sections.append(
                '[hybrid]\nenabled = true\ninclude = ["mcp:orders/*"]\n'
            )
        for name, cmd in {"orders": server_cmd, **extra}.items():
            command_toml = json.dumps(cmd["command"])
            args_toml = ", ".join(json.dumps(a) for a in cmd["args"])
            env_toml = ", ".join(
                f'{k} = "{v}"' for k, v in cmd["env"].items()
            )
            sections.append(
                f'[mcp.servers."{name}"]\ncommand = {command_toml}\n'
                f"args = [{args_toml}]\nenv = {{ {env_toml} }}\n"
            )
        toml_path.write_text("\n".join(sections), encoding="utf-8")
        serve_args = ["serve", "mcp", "--config", str(toml_path)]
        if arm == "hybrid":
            serve_args.append("--hybrid")
        return {
            "mcpServers": {
                "toolplane": {
                    "type": "stdio",
                    "command": code["toolplane_bin"],
                    "args": serve_args,
                }
            }
        }
    raise ValueError(arm)


# init-event fields that describe the local client environment, not the
# measurement; stripped before a transcript is persisted (#104)
_INIT_ENV_FIELDS = ("slash_commands", "agents", "skills", "plugins", "memory_paths")


def _redacted_transcript(stdout: str) -> str:
    lines = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            for key in _INIT_ENV_FIELDS:
                if key in event:
                    event[key] = "<redacted: local client environment>"
        lines.append(json.dumps(event))
    return "\n".join(lines) + "\n"


def _unique_request_ids(stdout: str) -> int:
    """Exact model-request count from the transcript.

    Every assistant event carries the API request_id it arrived on; the
    unique count is the run's real number of model requests — stronger
    than inferring round-trips from cache-read arithmetic, and the metric
    that exposed the client-side double-discovery gap (#115).
    """
    ids = set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        request_id = event.get("request_id")
        if request_id:
            ids.add(request_id)
    return len(ids)


def arm_order(arms: list[str], rep: int) -> list[str]:
    """Deterministic counterbalance: odd reps reverse the arm order.

    A fixed order confounds arm with prompt-cache warmth and time drift
    (direct always ran first through #112). Alternation is deterministic
    on purpose — anyone can recompute which order a rep ran in from its
    rep number. With an odd rep count the first-position split is uneven
    by one; use even reps for headline cells.
    """
    return list(arms) if rep % 2 == 0 else list(reversed(arms))


def run_case(
    arm: str,
    task: str,
    model: str,
    workdir: Path,
    code: dict,
    m_servers: int = 1,
    transcript_path: Path | None = None,
) -> dict:
    orders_n = TASKS[task]["orders_n"]
    config_path = workdir / f"mcp-{arm}-{task}-m{m_servers}.json"
    config_path.write_text(
        json.dumps(mcp_config(arm, workdir, task, m_servers, code)),
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
        if transcript_path is not None and exc.stdout:
            partial = exc.stdout
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", errors="replace")
            transcript_path.write_text(
                _redacted_transcript(partial), encoding="utf-8"
            )
        return {
            "arm": arm,
            "task": task,
            "orders_n": orders_n,
            "m_servers": m_servers,
            "model": None,
            "correct": False,
            "answer": None,
            "tool_calls": None,
            "tool_call_names": [],
            "model_requests": None,
            "num_turns": None,
            "input_tokens": 0,
            "uncached_input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": None,
            "api_duration_ms": None,
            "wall_s": round(time.monotonic() - started, 1),
            "exit_code": -1,
            "error": f"timeout after {exc.timeout}s",
            "transcript": transcript_path.name if transcript_path else None,
        }
    wall_s = time.monotonic() - started
    if transcript_path is not None:
        transcript_path.write_text(
            _redacted_transcript(proc.stdout), encoding="utf-8"
        )

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
        "model_requests": _unique_request_ids(proc.stdout),
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
        "transcript": transcript_path.name if transcript_path else None,
    }


def _cell_stats(group: list[dict], key: str) -> tuple:
    # .get: rows from pre-#116 result files lack newer keys (model_requests)
    vals = [r.get(key) for r in group if r.get(key) is not None]
    if not vals:
        return None, None, None
    return statistics.median(vals), min(vals), max(vals)


def summarize(rows: list[dict]) -> str:
    """Median table with mechanical honesty annotations (#107 items 3-4).

    † on cost/wall: the OBSERVED per-rep ranges of the two arms overlap
    for this task. That is a statement about the samples, not a noise
    conclusion — at these rep counts it means the median gap is
    unresolved; do not publish it as a win without more reps.
    cost/pass = total spend / successful runs (TPS-Bench cost-of-pass):
    a cheap wrong answer prices in as a loss; a run with unknown spend
    (timeout) makes the cell "n/a" rather than silently pricing as free.
    """
    lines = [
        "| task | M | arm | ok | tool calls | reqs | turns | out tokens "
        "| uncached in | cost $ | cost/pass | wall s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    m_values = sorted({r.get("m_servers", 1) for r in rows})
    # arms in a stable, meaningful order; only those actually present render
    arm_order_display = ["direct", "toolplane", "hybrid", "curated"]
    present = {r["arm"] for r in rows}
    arms = [a for a in arm_order_display if a in present] + sorted(
        present - set(arm_order_display)
    )
    overlap_seen = False
    for task in TASKS:
        for m in m_values:
            groups = {
                arm: [
                    r
                    for r in rows
                    if r["task"] == task
                    and r["arm"] == arm
                    and r.get("m_servers", 1) == m
                ]
                for arm in arms
            }
            # † marks an arm whose per-rep range overlaps direct's (the
            # reference) for this task — direct itself never gets the mark
            overlaps = {arm: {} for arm in arms}
            direct_group = groups.get("direct")
            for key in ("cost_usd", "wall_s"):
                direct_span = (
                    _cell_stats(direct_group, key) if direct_group else None
                )
                if not direct_span or None in direct_span[1:]:
                    continue
                _, lo_a, hi_a = direct_span
                for arm, g in groups.items():
                    if arm == "direct" or not g:
                        continue
                    _, lo_b, hi_b = _cell_stats(g, key)
                    if None not in (lo_b, hi_b):
                        overlaps[arm][key] = lo_a <= hi_b and lo_b <= hi_a
            for arm, group in groups.items():
                if not group:
                    continue

                def med(key, group=group):
                    value = _cell_stats(group, key)[0]
                    return round(value, 2) if value is not None else "-"

                def flagged(key, arm=arm):
                    mark = "†" if overlaps[arm].get(key) else ""
                    return f"{med(key)}{mark}"

                successes = sum(r["correct"] for r in group)
                costs = [r["cost_usd"] for r in group]
                if any(c is None for c in costs):
                    # a timed-out run billed an unknown amount; pricing it
                    # as zero would make unreliable arms look cheaper
                    cost_of_pass = "n/a"
                elif successes:
                    cost_of_pass = round(sum(costs) / successes, 2)
                else:
                    cost_of_pass = "inf"
                overlap_seen = overlap_seen or any(overlaps[arm].values())
                ok = f"{successes}/{len(group)}"
                lines.append(
                    f"| {task} | {m} | {arm} | {ok} | {med('tool_calls')} | "
                    f"{med('model_requests')} | "
                    f"{med('num_turns')} | {med('output_tokens')} | "
                    f"{med('uncached_input_tokens')} | {flagged('cost_usd')} | "
                    f"{cost_of_pass} | {flagged('wall_s')} |"
                )
    if overlap_seen:
        lines.append(
            "\n† this arm's observed per-rep range overlaps direct's for "
            "this task — the median gap is unresolved at this rep count."
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
    stamp = time.strftime("%Y%m%d-%H%M%S")
    transcripts_dir = BENCH_DIR / "results" / "transcripts" / f"run-{stamp}"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        code = build_code_under_test(workdir)
        print(
            f"code under test: {code['git_sha'][:12]}"
            f"{' DIRTY' if code['git_dirty'] else ''} "
            f"wheel {code['wheel_sha256'][:12]}",
            flush=True,
        )
        if code["git_dirty"]:
            print(
                "WARNING: working tree is dirty — the frozen wheel/fixtures "
                "are still immutable for this run, but the recorded git SHA "
                "does not describe the measured bytes",
                flush=True,
            )
        prov = provenance_row(code)
        for rep in range(args.reps):
            ordered_arms = arm_order(arms, rep)
            for task in tasks:
                for m in m_values:
                    for arm in ordered_arms:
                        print(
                            f"[{rep + 1}/{args.reps}] {task}/M={m}/{arm} ...",
                            flush=True,
                        )
                        transcript = (
                            transcripts_dir
                            / f"{task}-{arm}-m{m}-rep{rep + 1}.jsonl"
                        )
                        row = run_case(
                            arm, task, args.model, workdir, code, m, transcript
                        )
                        row["client_version"] = client_version
                        row["arm_order"] = "->".join(ordered_arms)
                        row.update(prov)
                        rows.append(row)
                        print(
                            f"  ok={row['correct']} tools={row['tool_calls']} "
                            f"reqs={row['model_requests']} "
                            f"turns={row['num_turns']} cost=${row['cost_usd']} "
                            f"wall={row['wall_s']}s",
                            flush=True,
                        )

    out = BENCH_DIR / "results" / f"run-{stamp}.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    print(f"transcripts in {transcripts_dir}\n")
    print(summarize(rows))
    return 0 if all(r["exit_code"] == 0 for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
