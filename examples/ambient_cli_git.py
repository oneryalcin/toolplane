"""Use a CLI through ambient lazy code-mode access."""

from __future__ import annotations

import asyncio

from toolplane import Toolplane


async def main() -> None:
    runtime = Toolplane()
    result = await runtime.execute(
        """
status = await git.status(short=True).text()
files = await git.diff(name_only=True, _=["HEAD~1", "HEAD"]).lines()
return {"status": status, "recent_changed_files": files[:5]}
"""
    )

    print("ok:", result.ok)
    print("value:", result.value)


if __name__ == "__main__":
    asyncio.run(main())
