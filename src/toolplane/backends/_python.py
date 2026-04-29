"""Shared Python execution helpers for backends."""

from __future__ import annotations

import textwrap


def wrap_async_main(code: str, *, function_name: str = "__toolplane_main__") -> str:
    body = code.rstrip()
    if not body.strip():
        body = "return None"
    return f"async def {function_name}():\n" + textwrap.indent(body, "    ")
