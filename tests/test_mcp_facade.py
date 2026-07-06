from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path

import pytest

from toolplane import (
    Toolplane,
    UnsafeFacadeConfigError,
    build_mcp_facade,
    build_mcp_facade_from_config,
)
from toolplane.cli import main
from toolplane.config import ToolplaneConfig
from toolplane.policy import EffectivePolicy, format_effective_policy

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import StdioTransport  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def write_stdio_demo_config(tmp_path: Path) -> Path:
    server_path = tmp_path / "server.py"
    server_path.write_text(
        textwrap.dedent(
            """
            from fastmcp import FastMCP

            mcp = FastMCP("Config Demo")

            @mcp.tool
            def multiply(x: int, y: int) -> int:
                return x * y

            if __name__ == "__main__":
                mcp.run(show_banner=False, log_level="ERROR")
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            [cli]
            mode = "disabled"

            [mcp.servers.docs]
            command = {json.dumps(sys.executable)}
            args = [{json.dumps(str(server_path))}]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_mcp_facade_exposes_only_toolplane_meta_tools() -> None:
    async def exercise() -> list[str]:
        app = build_mcp_facade(Toolplane(ambient_cli=False))
        async with Client(app) as client:
            tools = await client.list_tools()
        return sorted(tool.name for tool in tools)

    assert run(exercise()) == [
        "execute_code",
        "get_capability_schemas",
        "search_capabilities",
    ]


def test_schemas_not_found_signposts_on_every_detail_level() -> None:
    async def exercise() -> tuple[str, str]:
        runtime = Toolplane(ambient_cli=False)
        app = build_mcp_facade(runtime)
        async with Client(app) as client:
            detailed = await client.call_tool(
                "get_capability_schemas", {"names": ["git"]}
            )
            full = await client.call_tool(
                "get_capability_schemas", {"names": ["git"], "detail": "full"}
            )
        return detailed.content[0].text, full.content[0].text

    detailed, full = run(exercise())
    # a driver session hit the full path and got a bare not_found list —
    # the signpost must ride the JSON render too
    for text in (detailed, full):
        assert "toolplane://namespace" in text, text
        assert "empty query" in text, text
    payload = json.loads(full)
    assert payload[-1]["not_found"] == ["git"]


def test_results_resource_serves_saved_values_and_signposts_misses() -> None:
    async def exercise() -> tuple[list[str], str, str]:
        runtime = Toolplane(ambient_cli=False)
        handle = runtime.result_store.save({"answer": 42})
        app = build_mcp_facade(runtime)
        async with Client(app) as client:
            templates = [
                t.uriTemplate
                for t in await client.list_resource_templates()
                if t.uriTemplate.startswith("toolplane://results")
            ]
            content = await client.read_resource(f"toolplane://results/{handle}")
            try:
                await client.read_resource("toolplane://results/res_nope")
                missing_error = ""
            except Exception as exc:
                missing_error = str(exc)
        return templates, content[0].text, missing_error

    templates, payload, missing_error = run(exercise())
    assert templates == ["toolplane://results/{handle}"]
    assert json.loads(payload) == {"answer": 42}
    # the store's own message must reach the client, not a generic failure
    assert "unknown or expired result handle" in missing_error


def test_config_builder_fails_closed_on_multi_client_transport() -> None:
    async def exercise() -> list[str]:
        # default config: stores enabled, no CLI, no MCP servers
        app = await build_mcp_facade_from_config(
            ToolplaneConfig(), transport="http"
        )
        async with Client(app) as client:
            return [
                t.uriTemplate
                for t in await client.list_resource_templates()
                if t.uriTemplate.startswith("toolplane://")
            ]

    # adversarial review finding: an embedder building from config for a
    # multi-client transport must get the fail-closed stores — no results
    # or artifacts templates — without knowing about resolve_serve_config
    assert run(exercise()) == []


def test_artifact_resource_serves_saved_bytes() -> None:
    import base64

    async def exercise() -> tuple[bytes, str]:
        runtime = Toolplane(ambient_cli=False)
        data = bytes([0, 1, 2, 255]) * 4
        handle = runtime.artifact_store.save(data, filename="demo.bin")
        app = build_mcp_facade(runtime)
        async with Client(app) as client:
            content = await client.read_resource(
                f"toolplane://artifacts/{handle}"
            )
            item = content[0]
            raw = base64.b64decode(item.blob)
        runtime.artifact_store.close()
        return raw, item.mimeType

    raw, mime = run(exercise())
    assert raw == bytes([0, 1, 2, 255]) * 4
    assert mime == "application/octet-stream"


def test_results_resource_template_absent_when_store_disabled() -> None:
    from toolplane.results import ResultStore

    async def exercise() -> list[str]:
        runtime = Toolplane(
            ambient_cli=False, result_store=ResultStore(enabled=False)
        )
        app = build_mcp_facade(runtime)
        async with Client(app) as client:
            return [
                t.uriTemplate
                for t in await client.list_resource_templates()
                if t.uriTemplate.startswith("toolplane://results")
            ]

    # a template over a disabled store would be a signpost to nowhere
    assert run(exercise()) == []


def test_driving_toolplane_skill_is_served_and_manifest_valid() -> None:
    async def exercise() -> tuple[list[str], str, dict]:
        app = build_mcp_facade(Toolplane(ambient_cli=False))
        async with Client(app) as client:
            uris = [str(r.uri) for r in await client.list_resources()]
            skill = await client.read_resource("skill://driving-toolplane/SKILL.md")
            manifest = await client.read_resource(
                "skill://driving-toolplane/_manifest"
            )
        return uris, skill[0].text, json.loads(manifest[0].text)

    uris, skill, manifest = run(exercise())
    assert "skill://driving-toolplane/SKILL.md" in uris
    # the conventions the live drive-tests proved agents can't guess
    assert "await" in skill
    assert "call_tool" in skill
    assert "toolplane://namespace" in skill
    assert "save_result" in skill
    assert manifest["skill"] == "driving-toolplane"
    assert [f["path"] for f in manifest["files"]] == ["SKILL.md"]


def test_mcp_facade_namespace_resource_reflects_live_runtime() -> None:
    async def exercise() -> str:
        runtime = Toolplane(ambient_cli=True, ambient_cli_allowlist=["git", "jq"])

        @runtime.tool(name="lookup")
        def lookup(key: str) -> str:
            """Fetch a value."""
            return ""

        app = build_mcp_facade(runtime)
        async with Client(app) as client:
            resources = await client.list_resources()
            assert "toolplane://namespace" in [str(r.uri) for r in resources]
            content = await client.read_resource("toolplane://namespace")
            return content[0].text

    manifest = run(exercise())
    # the surfaces the cold-discovery test could not find: CLI allowlist
    # with both call shapes, result-store sugar, and the call_tool fallback
    assert "Allowed binaries: git, jq" in manifest
    assert "await git(" in manifest
    assert "cli_run" in manifest
    assert "await save_result(value)" in manifest
    assert "await lookup(...)" in manifest
    assert "call_tool" in manifest


def test_effective_policy_reports_allowlist_and_server_names() -> None:
    config = ToolplaneConfig.model_validate(
        {
            "toolplane": {"default_backend": "pyodide-deno"},
            "cli": {"mode": "allowlist", "allow": ["rg", "git"]},
            "mcp": {"servers": {"linear": {}, "docs": {}}},
        }
    )

    policy = EffectivePolicy.from_config(config)

    assert policy.default_backend == "pyodide-deno"
    assert policy.cli_mode == "allowlist"
    assert policy.cli_allowed_binaries == ("git", "rg")
    assert policy.mcp_server_names == ("docs", "linear")
    assert policy.unsafe_reasons == ()
    assert policy.allowed_backend_overrides == ("pyodide-deno",)
    assert format_effective_policy(policy) == (
        "Toolplane MCP policy: backend=pyodide-deno cli=allowlist "
        "allow=git,rg mcp_servers=docs,linear "
        "allowed_backend_overrides=pyodide-deno unsafe=false"
    )


def test_effective_policy_defaults_are_safe() -> None:
    policy = EffectivePolicy.from_config(ToolplaneConfig())

    assert policy.unsafe_reasons == ()
    assert policy.allowed_backend_overrides == ("monty",)
    assert format_effective_policy(policy) == (
        "Toolplane MCP policy: backend=monty cli=disabled allow=none "
        "mcp_servers=none allowed_backend_overrides=monty unsafe=false"
    )


def test_effective_policy_renders_ambient_as_all_when_unsafe_allowed() -> None:
    config = ToolplaneConfig.model_validate(
        {
            "toolplane": {"default_backend": "local_unsafe"},
            "cli": {"mode": "ambient"},
        }
    )
    policy = EffectivePolicy.from_config(config, allow_unsafe=True)

    assert policy.unsafe_reasons == ("local_unsafe", "ambient_cli")
    assert policy.allowed_backend_overrides is None
    assert format_effective_policy(policy) == (
        "Toolplane MCP policy: backend=local_unsafe cli=ambient allow=ALL "
        "mcp_servers=none allowed_backend_overrides=ALL unsafe=true "
        "reasons=local_unsafe,ambient_cli"
    )


def test_mcp_facade_search_schema_execute_flow() -> None:
    async def exercise() -> dict[str, object]:
        runtime = Toolplane(ambient_cli=False)

        @runtime.tool(tags={"math"})
        def add(x: int, y: int) -> int:
            """Add two numbers."""
            return x + y

        app = build_mcp_facade(runtime)
        async with Client(app) as client:
            search = await client.call_tool(
                "search_capabilities",
                {"query": "add numbers"},
            )
            schema = await client.call_tool(
                "get_capability_schemas",
                {"names": ["add"]},
            )
            execution = await client.call_tool(
                "execute_code",
                {"code": "return await add(x=2, y=3)"},
            )
        return {
            "search": search.data,
            "schema": schema.data,
            "execution": execution.data,
        }

    result = run(exercise())

    assert "- add: Add two numbers." in result["search"]
    assert "### add" in result["schema"]
    assert result["execution"]["value"] == 5
    assert result["execution"]["backend"] == "local_unsafe"
    assert result["execution"]["error"] is None


def test_mcp_facade_from_config_executes_stdio_mcp_tool(tmp_path: Path) -> None:
    config_path = write_stdio_demo_config(tmp_path)

    async def exercise() -> dict[str, object]:
        app = await build_mcp_facade_from_config(config_path)
        async with Client(app) as client:
            result = await client.call_tool(
                "execute_code",
                {"code": "return await docs_multiply(x=6, y=7)"},
            )
        return result.data

    result = run(exercise())

    assert result["value"] == 42
    assert result["error"] is None
    assert result["backend"] == "monty"


def test_cli_stdio_facade_round_trip_crosses_process_boundary(tmp_path: Path) -> None:
    config_path = write_stdio_demo_config(tmp_path)
    stderr_path = tmp_path / "toolplane-stderr.log"

    async def exercise() -> dict[str, object]:
        transport = StdioTransport(
            command=sys.executable,
            args=[
                "-m",
                "toolplane.cli",
                "serve",
                "mcp",
                "--config",
                str(config_path),
            ],
            cwd=str(Path.cwd()),
            keep_alive=False,
            log_file=stderr_path,
        )
        async with Client(transport) as client:
            tools = await client.list_tools()
            execution = await client.call_tool(
                "execute_code",
                {"code": "return await docs_multiply(x=6, y=7)"},
            )
            failure = await client.call_tool(
                "execute_code",
                {"code": "raise ValueError('stdio boundary detail')"},
            )
        return {
            "tools": sorted(tool.name for tool in tools),
            "execution": execution.data,
            "failure": failure.data,
        }

    result = run(exercise())

    assert result["tools"] == [
        "execute_code",
        "get_capability_schemas",
        "search_capabilities",
    ]
    assert result["execution"]["value"] == 42
    assert result["execution"]["error"] is None
    assert result["failure"]["error"]["type"] == "ValueError"
    assert "stdio boundary detail" in result["failure"]["error"]["message"]
    stderr = stderr_path.read_text(encoding="utf-8")
    assert "Toolplane MCP policy:" in stderr
    assert "backend=monty" in stderr
    assert "cli=disabled" in stderr
    assert "allow=none" in stderr
    assert "mcp_servers=docs" in stderr
    assert "allowed_backend_overrides=monty" in stderr
    assert "unsafe=false" in stderr
    assert "reasons=" not in stderr


def test_mcp_facade_from_config_serves_default_config_without_unsafe() -> None:
    run(build_mcp_facade_from_config({}))


def test_mcp_facade_from_config_rejects_unsafe_defaults() -> None:
    with pytest.raises(UnsafeFacadeConfigError) as error:
        run(
            build_mcp_facade_from_config(
                {
                    "toolplane": {"default_backend": "local_unsafe"},
                    "cli": {"mode": "ambient"},
                }
            )
        )

    message = str(error.value)
    assert "local_unsafe" in message
    assert "ambient" in message


def test_mcp_facade_from_config_allows_explicit_safe_policy() -> None:
    app = run(
        build_mcp_facade_from_config(
            {
                "toolplane": {"default_backend": "pyodide-deno"},
                "cli": {"mode": "disabled"},
            }
        )
    )

    assert app.name == "Toolplane"


def test_mcp_facade_from_config_blocks_local_unsafe_backend_override() -> None:
    async def exercise() -> dict[str, object]:
        app = await build_mcp_facade_from_config(
            {
                "toolplane": {"default_backend": "pyodide-deno"},
                "cli": {"mode": "disabled"},
            }
        )
        async with Client(app) as client:
            result = await client.call_tool(
                "execute_code",
                {
                    "code": "return 'host python'",
                    "backend": "local_unsafe",
                },
            )
        return result.data

    result = run(exercise())

    assert result["value"] is None
    assert result["backend"] == "local_unsafe"
    assert result["error"]["type"] == "BackendPolicyError"
    # a real-but-blocked backend says so and names what IS allowed
    assert "exists but is blocked" in result["error"]["message"]
    assert "pyodide-deno" in result["error"]["message"]


def test_mcp_facade_from_config_blocks_unknown_backend_override() -> None:
    async def exercise() -> dict[str, object]:
        app = await build_mcp_facade_from_config(
            {
                "toolplane": {"default_backend": "pyodide-deno"},
                "cli": {"mode": "disabled"},
            }
        )
        async with Client(app) as client:
            result = await client.call_tool(
                "execute_code",
                {
                    "code": "return 'custom backend'",
                    "backend": "future_custom_backend",
                },
            )
        return result.data

    result = run(exercise())

    assert result["value"] is None
    assert result["backend"] == "future_custom_backend"
    # a nonexistent backend is a different problem than a blocked one:
    # say it's unknown and list what exists, so a typo is self-correcting
    assert result["error"]["type"] == "BackendNotFoundError"
    assert "Unknown backend" in result["error"]["message"]
    assert "monty" in result["error"]["message"]


def test_mcp_facade_from_config_allows_unsafe_when_explicit() -> None:
    async def exercise() -> dict[str, object]:
        app = await build_mcp_facade_from_config({}, allow_unsafe=True)
        async with Client(app) as client:
            result = await client.call_tool(
                "execute_code",
                {
                    "code": "return 'host python'",
                    "backend": "local_unsafe",
                },
            )
        return {"name": app.name, "execution": result.data}

    result = run(exercise())

    assert result["name"] == "Toolplane"
    assert result["execution"]["value"] == "host python"
    assert result["execution"]["error"] is None


def test_cli_serve_mcp_reports_unsafe_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [toolplane]
            default_backend = "local_unsafe"

            [cli]
            mode = "ambient"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    assert main(["serve", "mcp", "--config", str(config_path)]) == 2
    captured = capsys.readouterr()
    assert "unsafe policy" in captured.err
    assert "local_unsafe" in captured.err
    assert "ambient" in captured.err
    assert "Toolplane MCP policy:" not in captured.err
    assert captured.out == ""


def test_cli_requires_nested_serve_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["serve"]) == 2
    captured = capsys.readouterr()
    assert "usage: toolplane" in captured.out


def test_mcp_facade_unknown_default_backend_returns_structured_error() -> None:
    async def exercise() -> dict[str, object]:
        app = await build_mcp_facade_from_config(
            {"toolplane": {"default_backend": "nope"}}
        )
        async with Client(app) as client:
            result = await client.call_tool("execute_code", {"code": "return 1"})
        return result.data

    result = run(exercise())

    assert result["error"]["type"] == "BackendNotFoundError"
    assert "Unknown backend 'nope'" in result["error"]["message"]
    assert "monty" in result["error"]["message"]


def test_mcp_facade_unsafe_policy_unknown_backend_returns_structured_error() -> None:
    async def exercise() -> dict[str, object]:
        app = await build_mcp_facade_from_config({}, allow_unsafe=True)
        async with Client(app) as client:
            result = await client.call_tool(
                "execute_code",
                {"code": "return 1", "backend": "bogus"},
            )
        return result.data

    result = run(exercise())

    assert result["error"]["type"] == "BackendNotFoundError"
    assert "Unknown backend 'bogus'" in result["error"]["message"]


def test_serve_refuses_unprimed_direct_oauth_with_login_hint() -> None:
    # a served process can't open a browser: startup previously blocked
    # for the whole OAuth callback timeout, then crashed raw (Sonnet
    # finding on #95)
    import asyncio

    import pytest

    from toolplane.credentials import CredentialStorageError
    from toolplane.mcp_facade import build_mcp_facade_from_config

    with pytest.raises(CredentialStorageError, match="toolplane mcp login linear"):
        asyncio.run(
            build_mcp_facade_from_config(
                {
                    "mcp": {
                        "servers": {
                            "linear": {
                                "url": "https://mcp.linear.app/mcp",
                                "auth": "oauth",
                            }
                        }
                    }
                }
            )
        )
