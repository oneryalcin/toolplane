"""Classify toolplane bench transcripts: retries vs staging, discovery anatomy.

Answers the #104 question the summary rows cannot: of the multiple
execute_code calls in every toolplane run, how many followed a FAILED
snippet (the monty dialect tax — actionable teaching-surface work) versus
a successful one (deliberate staged execution — fine)?

Also measures the discovery phase directly from the transcript: which
facade calls happened before the first execute_code, in what order, and
how many result characters each returned (the manifest-size attribution
that summary-level token slopes could not isolate).

Usage:
    uv run python bench/classify.py bench/results/transcripts/run-<stamp>/
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

FACADE_TOOLS = {
    "search_capabilities",
    "get_capability_schemas",
    "execute_code",
}


def _tool_events(transcript: Path) -> list[dict]:
    """Ordered tool calls with paired results: name, input, result text, error."""
    uses: dict[str, dict] = {}
    ordered: list[dict] = []
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    entry = {
                        "name": _short_name(block.get("name", "?")),
                        "input": block.get("input"),
                        "result_chars": 0,
                        "is_error": False,
                        "snippet_error": None,
                    }
                    uses[block["id"]] = entry
                    ordered.append(entry)
        elif event.get("type") == "user":
            for block in event.get("message", {}).get("content", []):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") in uses
                ):
                    entry = uses[block["tool_use_id"]]
                    text = _result_text(block.get("content"))
                    entry["result_chars"] = len(text)
                    entry["is_error"] = bool(block.get("is_error"))
                    if entry["name"] == "execute_code":
                        entry["snippet_error"] = _snippet_error(
                            text, entry["is_error"]
                        )
    return ordered


def _short_name(name: str) -> str:
    return name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    return json.dumps(content) if content is not None else ""


def _snippet_error(text: str, transport_error: bool) -> str | None:
    """The ExecutionError.type of a failed snippet, or None on success.

    execute_code failures are SUCCESSFUL MCP calls carrying a non-null
    "error" in the ExecutionResult payload; is_error covers transport-level
    failure only. Anything that does not parse as an ExecutionResult
    counts AGAINST the run — a payload-format drift must never silently
    zero the failure counts.
    """
    if transport_error:
        return "TransportError"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return "UnparseableResult"
    if not isinstance(payload, dict) or "error" not in payload:
        return "UnparseableResult"
    error = payload["error"]
    if error is None:
        return None
    if isinstance(error, dict):
        return str(error.get("type", "UnknownError"))
    return "UnknownError"


def _is_manifest_read(event: dict) -> bool:
    """A namespace-manifest read specifically — not any resource read.

    Keyed on the requested URI: a skill:// or toolplane://results read
    through the same client tool must not count as manifest usage.
    """
    if event["name"] != "ReadMcpResourceTool":
        return False
    uri = (event.get("input") or {}).get("uri", "")
    return str(uri).startswith("toolplane://namespace")


def _is_facade_discovery(event: dict) -> bool:
    return event["name"] in FACADE_TOOLS or _is_manifest_read(event)


def classify_run(transcript: Path) -> dict:
    events = _tool_events(transcript)
    executes = [e for e in events if e["name"] == "execute_code"]
    first_execute_at = next(
        (i for i, e in enumerate(events) if e["name"] == "execute_code"),
        len(events),
    )
    # the full pre-execute sequence (client tools like ToolSearch/Bash
    # included) is context; the facade-only count is the discovery-tax
    # metric — conflating them was a review finding on #111
    discovery = [
        {"name": e["name"], "result_chars": e["result_chars"]}
        for e in events[:first_execute_at]
    ]
    facade_discovery = [
        e["name"] for e in events[:first_execute_at] if _is_facade_discovery(e)
    ]

    # an execute after a FAILED execute is a retry; after a successful one,
    # staging; error taxonomy recorded so teaching gaps are attributable
    retries = 0
    staged = 0
    error_types: Counter[str] = Counter()
    prev_failed = False
    for i, e in enumerate(executes):
        if i > 0:
            if prev_failed:
                retries += 1
            else:
                staged += 1
        if e["snippet_error"]:
            error_types[e["snippet_error"]] += 1
        prev_failed = e["snippet_error"] is not None

    manifest_reads = [e for e in events if _is_manifest_read(e)]
    return {
        "transcript": transcript.name,
        "tool_sequence": [e["name"] for e in events],
        "discovery_calls_before_first_execute": discovery,
        "facade_discovery_before_first_execute": facade_discovery,
        "executes": len(executes),
        "failed_executes": sum(1 for e in executes if e["snippet_error"]),
        "retries_after_error": retries,
        "staged_after_success": staged,
        "snippet_error_types": dict(error_types),
        "manifest_read_chars": [e["result_chars"] for e in manifest_reads],
        "search_result_chars": [
            e["result_chars"]
            for e in events
            if e["name"] == "search_capabilities"
        ],
        "schema_result_chars": [
            e["result_chars"]
            for e in events
            if e["name"] == "get_capability_schemas"
        ],
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    transcripts = sorted(root.glob("*toolplane*.jsonl"))
    if not transcripts:
        print(f"no toolplane transcripts under {root}")
        return 1

    runs = [classify_run(t) for t in transcripts]
    out = root / "classified.json"
    out.write_text(json.dumps(runs, indent=2), encoding="utf-8")

    total_extra = sum(r["retries_after_error"] + r["staged_after_success"] for r in runs)
    retries = sum(r["retries_after_error"] for r in runs)
    errors: Counter[str] = Counter()
    for r in runs:
        errors.update(r["snippet_error_types"])
    disc_counts = [len(r["discovery_calls_before_first_execute"]) for r in runs]
    facade_counts = [
        len(r["facade_discovery_before_first_execute"]) for r in runs
    ]
    print(f"runs: {len(runs)}  (wrote {out})")
    print(
        f"executes/run: median {statistics.median([r['executes'] for r in runs])}, "
        f"max {max(r['executes'] for r in runs)}"
    )
    if total_extra:
        print(
            f"extra executes: {total_extra} — {retries} retries after error "
            f"({100 * retries // total_extra}%), "
            f"{total_extra - retries} staged after success"
        )
    print(f"snippet error types: {dict(errors) or 'none'}")
    print(
        f"facade discovery calls before first execute "
        f"(search/schemas/manifest): median "
        f"{statistics.median(facade_counts)}, "
        f"range {min(facade_counts)}-{max(facade_counts)}"
    )
    print(
        f"all tool calls before first execute (client tools included): "
        f"median {statistics.median(disc_counts)}, "
        f"range {min(disc_counts)}-{max(disc_counts)}"
    )
    for label, key in (
        ("manifest", "manifest_read_chars"),
        ("search", "search_result_chars"),
        ("schemas", "schema_result_chars"),
    ):
        sizes = [c for r in runs for c in r[key]]
        if sizes:
            print(
                f"{label} result chars: median {statistics.median(sizes):.0f}, "
                f"range {min(sizes)}-{max(sizes)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
