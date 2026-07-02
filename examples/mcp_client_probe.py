"""MCP client-capability probe server: one feature per tool/resource.

Serves as the empirical instrument behind docs/mcp-client-capability-spike.md.
Point any MCP client at this server to measure what protocol features that
client actually supports, rather than trusting its documentation.

Ground-truth check (full-featured fastmcp client, everything should pass):

    uv run python examples/mcp_client_probe.py --self-test

Probe a real client by registering the server over stdio, e.g.:

    claude -p "Call client_capabilities on the probe server; print raw JSON." \
        --mcp-config probe-mcp.json --strict-mcp-config \
        --allowedTools "mcp__probe__client_capabilities"

    codex exec \
        -c 'mcp_servers.probe.command="python"' \
        -c 'mcp_servers.probe.args=["examples/mcp_client_probe.py"]' \
        "Call client_capabilities on the probe server; print raw JSON."
"""

import sys

from fastmcp import Context, FastMCP

mcp = FastMCP(
    "Probe",
    instructions="Feature-probe server. Each tool tests one MCP client capability.",
)


@mcp.resource("probe://static")
def static_res() -> str:
    return "STATIC_RESOURCE_MARKER_42"


@mcp.resource("probe://item/{item_id}")
def template_res(item_id: str) -> str:
    return f"TEMPLATE_RESOURCE_MARKER_{item_id}"


@mcp.resource("probe://blob", mime_type="application/octet-stream")
def blob_res() -> bytes:
    return b"\x00\x01BINARY_MARKER\xff"


@mcp.tool
async def client_capabilities(ctx: Context) -> dict:
    """Return the capabilities the client declared during initialize."""
    params = ctx.session.client_params
    return {
        "clientInfo": params.clientInfo.model_dump() if params else None,
        "protocolVersion": params.protocolVersion if params else None,
        "capabilities": params.capabilities.model_dump() if params else None,
    }


@mcp.tool
async def try_sampling(ctx: Context) -> str:
    """Ask the client's LLM to reply PONG. Reports success or exact failure."""
    try:
        result = await ctx.sample("Reply with exactly the word: PONG")
        return f"SAMPLING_OK: {result.text!r}"
    except Exception as exc:
        return f"SAMPLING_FAIL: {type(exc).__name__}: {exc}"


@mcp.tool
async def try_elicitation(ctx: Context) -> str:
    """Ask the user for a short string via elicitation. Reports the outcome."""
    try:
        result = await ctx.elicit(
            "Probe question: reply with any short string.", response_type=str
        )
        return f"ELICIT_{result.action}: {getattr(result, 'data', None)!r}"
    except Exception as exc:
        return f"ELICIT_FAIL: {type(exc).__name__}: {exc}"


@mcp.tool
async def try_progress_and_logging(ctx: Context) -> str:
    """Emit 3 progress updates and 3 log messages, then return."""
    for i in range(3):
        await ctx.report_progress(progress=i + 1, total=3)
        await ctx.info(f"probe log message {i + 1}/3")
    return "PROGRESS_AND_LOGGING_DONE"


async def _self_test() -> None:
    """Drive this server with a client that supports every feature."""
    import base64

    from fastmcp import Client

    async def elicitation_handler(message, response_type, params, context):
        return "probe-answer"

    async def sampling_handler(messages, params, context):
        return "PONG"

    logs: list = []
    progress: list = []

    async def log_handler(message):
        logs.append(message.data)

    async def progress_handler(value, total, message):
        progress.append((value, total))

    async with Client(
        mcp,
        elicitation_handler=elicitation_handler,
        sampling_handler=sampling_handler,
        log_handler=log_handler,
        progress_handler=progress_handler,
    ) as client:
        resources = await client.list_resources()
        print("resources:", [str(r.uri) for r in resources])
        templates = await client.list_resource_templates()
        print("templates:", [str(t.uriTemplate) for t in templates])
        print("static:", (await client.read_resource("probe://static"))[0].text)
        print("template:", (await client.read_resource("probe://item/7"))[0].text)
        blob = (await client.read_resource("probe://blob"))[0]
        print("blob:", base64.b64decode(blob.blob), blob.mimeType)
        for tool in (
            "client_capabilities",
            "try_sampling",
            "try_elicitation",
            "try_progress_and_logging",
        ):
            result = await client.call_tool(tool, {})
            print(f"{tool}:", result.content[0].text[:120])
        print("progress events:", progress)
        print("log events:", [entry["msg"] for entry in logs])


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        import asyncio

        asyncio.run(_self_test())
    else:
        mcp.run(show_banner=False)
