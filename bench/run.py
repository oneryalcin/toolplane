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
from shipment_data import shipments  # noqa: E402

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


def _check_single_shipment(answer: str, n: int) -> bool:
    # the task asks for the "status" of SHP-017; the tool exposes it as
    # "state" (the synonym), so a correct answer maps status -> state
    expected = next(
        s["state"] for s in shipments(n) if s["shipment_id"] == "SHP-017"
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
    # latency axis (#107 item 10 / #109 gate): 100ms per tool call.
    # Pre-port monty awaited sequentially (N x latency); the 0.0.19 pool
    # API dispatches eagerly, so fire-then-await overlaps — this cell
    # measures whether the model writes the pattern and beats direct's
    # parallel batching on wall-clock
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
    # #127 second-domain validation: same single-lookup shape, a different
    # domain, and the query word ("status") is a synonym ABSENT from the
    # get_shipment description ("state") — so a name-signal re-export's leaf
    # cannot carry it, yet "status" still collides with built-in Task tools
    # so the discovery difficulty matches orders. If name-signal helps here
    # like it did on orders, the bump generalizes; if not, it was a lexical
    # coincidence of "status" being in the orders description.
    "single_shipment": {
        "prompt": (
            "Using the available tools, find the status of shipment "
            "SHP-017. Reply with just the status word wrapped in "
            "<answer></answer> tags."
        ),
        "check": _check_single_shipment,
        "orders_n": 30,
        "domain": "shipments",
    },
}


# distractor rollout order for the M axis; M=15 wraps around with a second
# "-eu" workspace per profile, mimicking multi-tenant real setups
_PROFILES = ("crm", "calendar", "tickets", "wiki", "payments", "analytics", "files")


# domain of the task under test: which fixture server carries the answer,
# what the client sees it named, and the token that identifies its tool in
# a ToolSearch result. Tasks default to "orders"; #127's second-domain
# validation adds "shipments" (a name with no distractor-profile collision —
# unlike "tickets", which _PROFILES already uses).
_DOMAINS = {
    "orders": {
        "server": "order_server.py",
        "server_name": "orders",
        "include": "mcp:orders/*",
        "token": "order",
        "n_env": "BENCH_ORDERS_N",
    },
    "shipments": {
        "server": "shipment_server.py",
        "server_name": "shipments",
        "include": "mcp:shipments/*",
        "token": "shipment",
        "n_env": "BENCH_SHIPMENTS_N",
    },
}


def task_domain(task: str) -> dict:
    return _DOMAINS[TASKS[task].get("domain", "orders")]


def distractors(m_servers: int) -> list[tuple[str, str]]:
    """(server_name, profile) pairs for M total servers (target included)."""
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


_GRANULARITIES = ("fetch-one", "bulk")


def validate_axis_scope(
    tasks: list[str], record_bytes: list[int], granularities: list[str]
) -> None:
    unsupported = [
        task for task in tasks if task_domain(task)["server_name"] != "orders"
    ]
    if unsupported and (record_bytes != [0] or granularities != ["fetch-one"]):
        raise ValueError(
            "--record-bytes and --granularity currently apply only to "
            f"orders-domain tasks; unsupported tasks: {unsupported}"
        )


def task_server_env(
    task: str, record_bytes: int = 0, granularity: str = "fetch-one"
) -> dict[str, str]:
    if granularity not in _GRANULARITIES:
        raise ValueError(
            f"unknown API granularity {granularity!r}; expected {_GRANULARITIES}"
        )
    env = {
        task_domain(task)["n_env"]: str(TASKS[task]["orders_n"]),
        **TASKS[task].get("server_env", {}),
    }
    if task_domain(task)["server_name"] == "orders":
        env["BENCH_API_GRANULARITY"] = granularity
    if record_bytes:
        # payload axis (#117): only the orders server reads this; harmless
        # elsewhere. Sizes each record so a direct fetch pays it in context.
        env["BENCH_RECORD_BYTES"] = str(record_bytes)
    return env


# every file whose bytes are part of the measurement: the fixtures are
# snapshotted into the run's scratch dir and served from there, so an edit
# to the working tree mid-matrix cannot reach a running measurement (the
# #111 contamination incident; #116 item 2)
_FIXTURE_FILES = (
    "order_server.py",
    "distractor_server.py",
    "orders_data.py",
    "shipment_server.py",
    "shipment_data.py",
)


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
        # the harness itself (prompts, tasks, metric, mcp wiring) is not in
        # the wheel and runs from the repo, not a frozen copy — hash it so a
        # dirty run.py is provable from the row, not only from git_dirty
        # (which snapshots before result files are written and cannot tell
        # an uncommitted harness edit from an untracked result file, #127)
        "harness_sha256": _sha256(BENCH_DIR / "run.py"),
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
        "harness_sha256": code["harness_sha256"],
        "fixtures_sha256": code["fixtures_sha256"],
    }


# #127 A/B: all three re-export the SAME curated orders tools; they differ
# only in TOOLPLANE_HYBRID_SIGNAL, which controls how the re-exported
# tool's name/description is built. "curated" is the control.
_CURATED_ARMS = {
    "curated": "control",
    "curated_name": "name",
    "curated_desc": "description",
}


def mcp_config(
    arm: str,
    workdir: Path,
    task: str,
    m_servers: int,
    code: dict,
    record_bytes: int = 0,
    granularity: str = "fetch-one",
) -> dict:
    fixtures_dir = Path(code["fixtures_dir"])
    domain = task_domain(task)
    target_name = domain["server_name"]

    def frozen_cmd(script: str, env: dict) -> dict:
        return {
            "command": code["python"],
            "args": [str(fixtures_dir / script)],
            "env": env,
        }

    server_cmd = frozen_cmd(
        domain["server"], task_server_env(task, record_bytes, granularity)
    )
    extra = {
        name: frozen_cmd("distractor_server.py", {"DISTRACTOR_PROFILE": profile})
        for name, profile in distractors(m_servers)
    }
    if arm == "direct":
        return {
            "mcpServers": {
                target_name: {**server_cmd, "type": "stdio"},
                **{name: {**cmd, "type": "stdio"} for name, cmd in extra.items()},
            }
        }
    # all three toolplane arms serve the SAME servers through the SAME
    # facade. "hybrid" adds --hybrid (re-export the WHOLE registry, #114's
    # held baseline); "curated" adds a [hybrid] config section that
    # re-exports ONLY the orders tools (#125 — the selective form).
    if arm in ("toolplane", "hybrid") or arm in _CURATED_ARMS:
        # generated with absolute paths: every process here runs from a
        # scratch cwd, so nothing may be cwd-relative
        toml_path = (
            workdir
            / (
                f"toolplane-bench-{arm}-{task}-m{m_servers}-"
                f"b{record_bytes}-g{granularity}.toml"
            )
        )
        sections = []
        if arm in _CURATED_ARMS:
            # curate the single/adaptive capabilities: the target server's
            # tools, by canonical-name glob. Distractors stay behind the
            # facade — the whole #125 hypothesis.
            sections.append(
                f'[hybrid]\nenabled = true\ninclude = ["{domain["include"]}"]\n'
            )
        for name, cmd in {target_name: server_cmd, **extra}.items():
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
        server_entry = {
            "type": "stdio",
            "command": code["toolplane_bin"],
            "args": serve_args,
        }
        # #127 variant arms set the re-export naming/description signal in
        # the served process's env (bench-only knob, not public config)
        signal = _CURATED_ARMS.get(arm, "control")
        if signal != "control":
            server_entry["env"] = {"TOOLPLANE_HYBRID_SIGNAL": signal}
        return {"mcpServers": {"toolplane": server_entry}}
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


_STARTUP_ARTIFACT = "still connecting"


def _is_domain_tool(name: str, token: str) -> bool:
    # the target tool, whether direct (mcp__orders__get_order) or
    # re-exported (mcp__toolplane__orders_get_order, or the #127 name-signal
    # variant mcp__toolplane__orders_fetch_one_order_record_...). Built-ins
    # (TaskList, Monitor) and the meta-tools carry no domain token.
    return name.startswith("mcp__") and token in name.lower()


def _first_search_discovery(stdout: str, token: str = "order") -> dict:
    """First-valid-ToolSearch discovery of the domain tool (#127 primary).

    A ToolSearch whose result says a server is "still connecting" is a
    cold-start artifact, not a ranking miss: it is counted separately and
    excluded from the valid-search sequence, per the #127 pre-registration.
    """
    search_ids: set[str] = set()
    valid_hits: list[bool] = []  # per valid search: did it return the domain tool?
    startup_artifacts = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if (
                    block.get("type") == "tool_use"
                    and block.get("name") == "ToolSearch"
                ):
                    search_ids.add(block.get("id"))
        if event.get("type") == "user":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") != "tool_result":
                    continue
                if block.get("tool_use_id") not in search_ids:
                    continue
                # a ToolSearch result is either a list of tool_reference
                # dicts ({"tool_name": ...}), a list of text blocks, or a
                # plain string (the "still connecting" / "No matching"
                # messages). Collect tool names and free text from all shapes.
                content = block.get("content")
                names: list[str] = []
                text = ""
                if isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        if "tool_name" in item:
                            names.append(item["tool_name"])
                        elif "text" in item:
                            text += item["text"]
                elif isinstance(content, str):
                    text = content
                else:
                    text = json.dumps(content)
                if _STARTUP_ARTIFACT in text:
                    startup_artifacts += 1
                    continue
                # some clients embed the reference list as JSON in the text
                if not names and text.strip().startswith("["):
                    try:
                        names = [
                            r.get("tool_name", "") for r in json.loads(text)
                        ]
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        names = []
                valid_hits.append(any(_is_domain_tool(n, token) for n in names))
    hit_index = next((i for i, hit in enumerate(valid_hits) if hit), None)
    return {
        "first_search_hit": valid_hits[0] if valid_hits else None,
        "searches_to_domain_tool": (
            hit_index + 1 if hit_index is not None else None
        ),
        "valid_searches": len(valid_hits),
        "startup_artifact_searches": startup_artifacts,
    }


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
    record_bytes: int = 0,
    granularity: str = "fetch-one",
) -> dict:
    orders_n = TASKS[task]["orders_n"]
    config_path = workdir / (
        f"mcp-{arm}-{task}-m{m_servers}-b{record_bytes}-g{granularity}.json"
    )
    config_path.write_text(
        json.dumps(
            mcp_config(
                arm,
                workdir,
                task,
                m_servers,
                code,
                record_bytes,
                granularity,
            )
        ),
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
            "record_bytes": record_bytes,
            "granularity": granularity,
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
            "first_search_hit": None,
            "searches_to_domain_tool": None,
            "valid_searches": None,
            "startup_artifact_searches": None,
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
        "record_bytes": record_bytes,
        "granularity": granularity,
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
        **_first_search_discovery(proc.stdout, task_domain(task)["token"]),
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
        "| task | M | B | granularity | arm | ok | tool calls | reqs | turns "
        "| out tokens | uncached in | cost $ | cost/pass | wall s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    m_values = sorted({r.get("m_servers", 1) for r in rows})
    # payload axis (#117): record_bytes per fetched record; 0 = current
    b_values = sorted({r.get("record_bytes", 0) for r in rows})
    g_values = [
        g
        for g in _GRANULARITIES
        if g in {r.get("granularity", "fetch-one") for r in rows}
    ]
    # arms in a stable, meaningful order; only those actually present render
    arm_order_display = [
        "direct",
        "toolplane",
        "hybrid",
        "curated",
        "curated_name",
        "curated_desc",
    ]
    present = {r["arm"] for r in rows}
    arms = [a for a in arm_order_display if a in present] + sorted(
        present - set(arm_order_display)
    )
    overlap_seen = False
    for task in TASKS:
        for m in m_values:
            for b in b_values:
                for granularity in g_values:
                    groups = {
                        arm: [
                            r
                            for r in rows
                            if r["task"] == task
                            and r["arm"] == arm
                            and r.get("m_servers", 1) == m
                            and r.get("record_bytes", 0) == b
                            and r.get("granularity", "fetch-one") == granularity
                        ]
                        for arm in arms
                    }
                    # † marks an arm whose per-rep range overlaps direct's (the
                    # reference) for this cell — direct itself never gets the mark
                    overlaps = {arm: {} for arm in arms}
                    direct_group = groups.get("direct")
                    for key in ("cost_usd", "wall_s"):
                        direct_span = (
                            _cell_stats(direct_group, key) if direct_group else None
                        )
                        if not direct_span or None in direct_span[1:]:
                            continue
                        _, lo_a, hi_a = direct_span
                        for arm, group in groups.items():
                            if arm == "direct" or not group:
                                continue
                            _, lo_b, hi_b = _cell_stats(group, key)
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
                            f"| {task} | {m} | {b} | {granularity} | {arm} | {ok} "
                            f"| {med('tool_calls')} | {med('model_requests')} | "
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


def discovery_summary(rows: list[dict]) -> str:
    """First-search discovery table — the #127 primary outcome.

    first-hit rate = fraction of reps whose first valid ToolSearch returned
    the domain tool; searches = median valid searches to the first
    domain-tool hit; artifacts = median 'still connecting' searches, which
    are excluded from the hit accounting.
    """
    lines = [
        "\n## First-search discovery (#127 primary outcome)",
        "",
        "| task | M | B | granularity | arm | reps | first-hit rate "
        "| searches→tool | artifacts |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    arm_order_display = [
        "direct",
        "toolplane",
        "hybrid",
        "curated",
        "curated_name",
        "curated_desc",
    ]
    present = {r["arm"] for r in rows}
    arms = [a for a in arm_order_display if a in present] + sorted(
        present - set(arm_order_display)
    )
    tasks = sorted({r["task"] for r in rows})
    ms = sorted({r["m_servers"] for r in rows})
    payloads = sorted({r.get("record_bytes", 0) for r in rows})
    granularities = [
        g
        for g in _GRANULARITIES
        if g in {r.get("granularity", "fetch-one") for r in rows}
    ]
    for task in tasks:
        for m in ms:
            for payload in payloads:
                for granularity in granularities:
                    for arm in arms:
                        group = [
                            r
                            for r in rows
                            if r["task"] == task
                            and r["m_servers"] == m
                            and r["arm"] == arm
                            and r.get("record_bytes", 0) == payload
                            and r.get("granularity", "fetch-one") == granularity
                        ]
                        if not group:
                            continue
                        hits = [
                            r["first_search_hit"]
                            for r in group
                            if r.get("first_search_hit") is not None
                        ]
                        rate = f"{sum(hits)}/{len(hits)}" if hits else "n/a"
                        s2t = [
                            r["searches_to_domain_tool"]
                            for r in group
                            if r.get("searches_to_domain_tool") is not None
                        ]
                        s2t_med = statistics.median(s2t) if s2t else "n/a"
                        arts = [
                            r["startup_artifact_searches"]
                            for r in group
                            if r.get("startup_artifact_searches") is not None
                        ]
                        arts_med = statistics.median(arts) if arts else "n/a"
                        lines.append(
                            f"| {task} | {m} | {payload} | {granularity} | "
                            f"{arm} | {len(group)} | {rate} | {s2t_med} | "
                            f"{arts_med} |"
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
    parser.add_argument(
        "--record-bytes",
        default="0",
        help="comma-separated payload sizes per fetched record (#117): "
        "0 (current tiny records), 2000, 20000. A fat record inflates what "
        "a direct fetch drops into model context; the toolplane arm keeps "
        "it in the sandbox.",
    )
    parser.add_argument(
        "--granularity",
        default="fetch-one",
        help="comma-separated API profiles (#117): fetch-one or bulk. "
        "Profiles are mutually exclusive fixture surfaces, not optional "
        "endpoints presented together.",
    )
    args = parser.parse_args()

    tasks = [t for t in args.tasks.split(",") if t in TASKS]
    arms = args.arms.split(",")
    m_values = [int(m) for m in args.servers.split(",")]
    b_values = [int(b) for b in args.record_bytes.split(",")]
    g_values = [g for g in args.granularity.split(",") if g]
    unknown_granularities = [g for g in g_values if g not in _GRANULARITIES]
    if unknown_granularities:
        parser.error(
            f"unknown --granularity values {unknown_granularities}; "
            f"expected comma-separated {_GRANULARITIES}"
        )
    try:
        validate_axis_scope(tasks, b_values, g_values)
    except ValueError as exc:
        parser.error(str(exc))
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
                    for b in b_values:
                        for granularity in g_values:
                            for arm in ordered_arms:
                                tag = (
                                    f"{task}/M={m}/B={b}/G={granularity}/{arm}"
                                )
                                print(
                                    f"[{rep + 1}/{args.reps}] {tag} ...",
                                    flush=True,
                                )
                                transcript = (
                                    transcripts_dir
                                    / (
                                        f"{task}-{arm}-m{m}-b{b}-g{granularity}-"
                                        f"rep{rep + 1}.jsonl"
                                    )
                                )
                                row = run_case(
                                    arm,
                                    task,
                                    args.model,
                                    workdir,
                                    code,
                                    m,
                                    transcript,
                                    record_bytes=b,
                                    granularity=granularity,
                                )
                                row["client_version"] = client_version
                                row["arm_order"] = "->".join(ordered_arms)
                                row.update(prov)
                                rows.append(row)
                                print(
                                    f"  ok={row['correct']} "
                                    f"tools={row['tool_calls']} "
                                    f"reqs={row['model_requests']} "
                                    f"turns={row['num_turns']} "
                                    f"cost=${row['cost_usd']} "
                                    f"wall={row['wall_s']}s",
                                    flush=True,
                                )

    out = BENCH_DIR / "results" / f"run-{stamp}.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    print(f"transcripts in {transcripts_dir}\n")
    print(summarize(rows))
    print(discovery_summary(rows))
    return 0 if all(r["exit_code"] == 0 for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
