"""Shared Python execution helpers for backends."""

from __future__ import annotations

import ast
import re
import textwrap
from collections.abc import Iterable

# CPython names the coroutine when it garbage-collects one that was never
# awaited; on backends with real coroutines (local, pyodide) this is the only
# trace of assign-then-inspect misuse that neither the AST preflight nor the
# result scan can see
_UNAWAITED_WARNING = re.compile(r"coroutine '([^']+)' was never awaited")

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


def stderr_reports_unawaited(stderr: str, binding_names: Iterable[str]) -> bool:
    """True when a never-awaited-coroutine warning names one of our bindings.

    Matching on the warning's coroutine name keeps this precise: a user's own
    un-awaited coroutines never trip it. The qualname's last segment is
    compared because local bindings surface as e.g.
    ``build_result_bindings.<locals>.save_result``.
    """
    names = set(binding_names)
    return any(
        match.group(1).rsplit(".", 1)[-1] in names
        for match in _UNAWAITED_WARNING.finditer(stderr)
    )


def find_reserved_rebindings(code: str, reserved: Iterable[str]) -> list[str]:
    """Names from ``reserved`` that the snippet rebinds at session top level.

    In a persistent session a top-level assignment outlives the run and
    permanently masks the injected binding of the same name — monty has no
    ``del``, so even ``reset_session`` itself can be shadowed away, removing
    the only escape hatch. Bindings inside nested function bodies are local
    and harmless; the nested function's own NAME still binds at top level
    and is checked.
    """
    names = set(reserved)
    if not names:
        return []
    try:
        tree = ast.parse(wrap_async_main(code))
    except SyntaxError:
        return []  # the backend reports its own, better syntax error
    main = tree.body[0]
    assert isinstance(main, ast.AsyncFunctionDef)

    found: dict[str, None] = {}

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                if child.name in names:
                    found.setdefault(child.name)
                continue  # inner bindings are function-local
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                if child.id in names:
                    found.setdefault(child.id)
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                # imports bind names too: `import math as reset_session` /
                # `from math import sqrt as save_result` (Codex finding
                # on #86); `import a.b` binds the root `a`
                for alias in child.names:
                    if alias.name == "*":
                        continue
                    bound = alias.asname or alias.name.split(".")[0]
                    if bound in names:
                        found.setdefault(bound)
            visit(child)

    visit(main)
    return list(found)


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
