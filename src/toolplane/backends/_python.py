"""Shared Python execution helpers for backends."""

from __future__ import annotations

import textwrap

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
