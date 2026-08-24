"""Many-server cold-start measurement (#118).

Times ``register_mcp_config`` over real stdio subprocesses as the server
count grows, with and without one artificially slow upstream. Local and
free: no model client involved. Run:

    uv run --no-project --prerelease=allow --with-editable . \
        python bench/coldstart.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from toolplane import Toolplane

SERVER = str(Path(__file__).with_name("coldstart_server.py"))
SLOW_DELAY = 3.0
SLOW_AT_INDEX = 0  # the slow server sits first: worst case for sequential


def config_for(count: int, slow: bool) -> dict[str, object]:
    servers: dict[str, dict[str, object]] = {}
    for i in range(count):
        entry: dict[str, object] = {
            "command": sys.executable,
            "args": [SERVER],
        }
        if slow and i == SLOW_AT_INDEX:
            entry["args"] = [SERVER, "--delay", str(SLOW_DELAY)]
        servers[f"srv_{i:02d}"] = entry
    return {"mcpServers": servers}


async def timed_register(count: int, slow: bool) -> tuple[float, int]:
    runtime = Toolplane()
    started = time.perf_counter()
    caps = await runtime.register_mcp_config(config_for(count, slow))
    wall = time.perf_counter() - started
    return wall, len(caps)


async def main() -> None:
    print(f"slow-server delay: {SLOW_DELAY:g}s at index {SLOW_AT_INDEX}\n")
    print("| servers | all fast (s) | one slow (s) |")
    print("|---|---|---|")
    for count in (1, 2, 4, 8, 16):
        fast_wall, n_caps = await timed_register(count, slow=False)
        assert n_caps == count
        slow_wall, _ = await timed_register(count, slow=True)
        print(f"| {count} | {fast_wall:.2f} | {slow_wall:.2f} |")


if __name__ == "__main__":
    asyncio.run(main())
