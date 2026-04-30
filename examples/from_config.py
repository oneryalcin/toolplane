"""Build a Toolplane runtime from Toolplane-native TOML config."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import textwrap
from pathlib import Path

from toolplane import Toolplane


async def main() -> None:
    server_path = Path(__file__).with_name("mcp_stdio_server.py").resolve()

    with tempfile.TemporaryDirectory(prefix="toolplane-config-example-") as temp_dir:
        config_path = Path(temp_dir) / "toolplane.toml"
        config_path.write_text(
            textwrap.dedent(
                f"""
                [toolplane]
                default_backend = "local_unsafe"

                [cli]
                mode = "allowlist"
                allow = ["git"]

                [mcp.servers.local_math]
                command = {json.dumps(sys.executable)}
                args = [{json.dumps(str(server_path))}]
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        runtime = await Toolplane.from_config(config_path)
        result = await runtime.execute(
            """
product = await local_math.multiply(x=6, y=7)
blocked = "curl" not in globals()
return {"product": product["product"], "curl_top_level_blocked": blocked}
"""
        )

    if not result.ok:
        raise SystemExit(result.error)
    print(result.value)


if __name__ == "__main__":
    asyncio.run(main())
