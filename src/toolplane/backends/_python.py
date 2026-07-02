"""Shared Python execution helpers for backends."""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Iterable

UNAWAITED_CALL_ERROR_TYPE = "UnawaitedToolCallError"
UNAWAITED_CALL_MESSAGE = (
    "snippet returned the result of a call that was never awaited; "
    "capability, CLI, and result-store calls are async — add `await` "
    "(e.g. `handle = await save_result(value)`)"
)


def wrap_async_main(code: str, *, function_name: str = "__toolplane_main__") -> str:
    body = code.rstrip()
    if not body.strip():
        body = "return None"
    return f"async def {function_name}():\n" + textwrap.indent(body, "    ")


def find_unawaited_calls(code: str, binding_names: Iterable[str]) -> list[str]:
    """Preflight for calls to async bindings that can never be awaited.

    Flags only the definitively-broken shapes: a call discarded as a bare
    expression statement (fire-and-forget with swallowed errors on monty,
    never executed on the other backends) and a call stringified inside an
    f-string. Assignments are allowed — the value can still be awaited later,
    and the runtime result/stdout scans catch the ones that never are.
    """
    names = set(binding_names)
    if not names:
        return []
    try:
        tree = ast.parse(wrap_async_main(code))
    except SyntaxError:
        return []  # the backend reports its own, better syntax error

    findings: list[str] = []
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            if not (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id in names
            ):
                continue
            if isinstance(node, ast.Expr):
                reason = "its result is discarded"
            elif isinstance(node, ast.FormattedValue):
                reason = "it is stringified inside an f-string"
            else:
                continue
            # wrap_async_main prepends one header line
            findings.append(
                f"call to '{child.func.id}' on line {child.lineno - 1} is "
                f"never awaited ({reason}); capability, CLI, and "
                "result-store calls are async — add `await` (e.g. "
                "`handle = await save_result(value)`)"
            )
    return findings
