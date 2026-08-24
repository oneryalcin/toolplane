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


def _uri_template(template: object) -> str:
    """SDK v2 renames uriTemplate -> uri_template with a deprecation shim
    on the old name (#142); read whichever generation this object speaks."""
    value = getattr(template, "uri_template", None)
    if value is None:
        value = template.uriTemplate  # type: ignore[attr-defined]
    return str(value)

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
                _uri_template(t)
                for t in await client.list_resource_templates()
                if _uri_template(t).startswith("toolplane://results")
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
                _uri_template(t)
                for t in await client.list_resource_templates()
                if _uri_template(t).startswith("toolplane://")
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
        # mcp-sdk v2 / fastmcp>=3.4 renames mimeType -> mime_type with a
        # deprecation shim on the old name (#142); prefer snake_case and
        # fall back for pure v1 objects.
        mime = getattr(item, "mime_type", None)
        if mime is None:
            mime = item.mimeType
        return raw, mime

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
                _uri_template(t)
                for t in await client.list_resource_templates()
                if _uri_template(t).startswith("toolplane://results")
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

    assert (
        "- `await add(x=<integer>, y=<integer>)` — Add two numbers. [add]"
        in result["search"]
    )
    assert "**Call**: `await add(x=<integer>, y=<integer>)`" in result["schema"]
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


def _hybrid_runtime() -> Toolplane:
    runtime = Toolplane(ambient_cli=False)

    @runtime.tool(name="orders_get_order", tags={"orders"})
    async def get_order(order_id: str) -> dict:
        """Fetch one order record: order_id, region, amount, status."""
        return {"order_id": order_id, "status": "shipped"}

    @runtime.tool(name="orders_list_ids", tags={"orders"})
    async def list_ids() -> list[str]:
        """List every order id."""
        return ["ORD-001", "ORD-002"]

    return runtime


def test_hybrid_reexports_capabilities_alongside_meta_tools() -> None:
    async def exercise() -> list[str]:
        app = build_mcp_facade(_hybrid_runtime(), hybrid=True)
        async with Client(app) as client:
            tools = await client.list_tools()
        return sorted(t.name for t in tools)

    # the three meta-tools plus one tool per capability
    assert run(exercise()) == [
        "execute_code",
        "get_capability_schemas",
        "orders_get_order",
        "orders_list_ids",
        "search_capabilities",
    ]


def test_hybrid_off_by_default() -> None:
    async def exercise() -> list[str]:
        app = build_mcp_facade(_hybrid_runtime())
        async with Client(app) as client:
            return sorted(t.name for t in await client.list_tools())

    assert run(exercise()) == [
        "execute_code",
        "get_capability_schemas",
        "search_capabilities",
    ]


def _tool_schema_field(tool: object) -> dict:
    """SDK v2 renames inputSchema -> input_schema with a deprecation shim
    on the old name (#142)."""
    value = getattr(tool, "input_schema", None)
    if value is None:
        value = tool.inputSchema  # type: ignore[attr-defined]
    return value


def test_hybrid_tool_dispatches_through_call_tool_with_real_schema() -> None:
    async def exercise() -> tuple[dict, dict]:
        app = build_mcp_facade(_hybrid_runtime(), hybrid=True)
        async with Client(app) as client:
            tools = {t.name: t for t in await client.list_tools()}
            schema = _tool_schema_field(tools["orders_get_order"])
            result = await client.call_tool(
                "orders_get_order", {"order_id": "ORD-017"}
            )
        return schema, result.structured_content

    schema, result = run(exercise())
    # the capability's own parameter schema is exposed verbatim
    assert schema["properties"]["order_id"]["type"] == "string"
    assert schema["required"] == ["order_id"]
    # and the call actually dispatches the capability
    assert result == {"order_id": "ORD-017", "status": "shipped"}


def test_hybrid_excludes_hidden_capabilities() -> None:
    from dataclasses import replace

    async def exercise() -> list[str]:
        runtime = _hybrid_runtime()
        # hide one capability the way the registry stores it
        canonical = "orders_list_ids"
        runtime.registry._capabilities[canonical] = replace(
            runtime.registry._capabilities[canonical], hidden=True
        )
        app = build_mcp_facade(runtime, hybrid=True)
        async with Client(app) as client:
            return sorted(t.name for t in await client.list_tools())

    names = run(exercise())
    assert "orders_get_order" in names
    assert "orders_list_ids" not in names


def test_hybrid_tool_name_never_shadows_a_meta_tool() -> None:
    async def exercise() -> list[str]:
        runtime = Toolplane(ambient_cli=False)

        @runtime.tool(name="execute_code")
        async def clashing() -> str:
            """A capability whose safe name collides with a meta-tool."""
            return "dispatched"

        app = build_mcp_facade(runtime, hybrid=True)
        async with Client(app) as client:
            tools = {t.name: t for t in await client.list_tools()}
            # the meta-tool must still be the real execute_code (takes code),
            # the capability got a suffixed name
            meta = _tool_schema_field(tools["execute_code"])
            suffixed = await client.call_tool("execute_code_2", {})
        return list(meta["properties"]), suffixed.content[0].text

    meta_params, dispatched = run(exercise())
    assert "code" in meta_params
    assert dispatched == "dispatched"


def test_hybrid_input_cannot_change_the_dispatched_capability(
    tmp_path: Path,
) -> None:
    # gauntlet critical, precise severity: the real hazard is tool-IDENTITY
    # confusion — the client approves/displays `orders_get_order` while the
    # server runs a different capability, defeating per-tool approval and
    # audit expectations (NOT a policy bypass: call_tool still audits and
    # enforces the allowlist). The advertised capability is what must
    # dispatch and what the audit must name, no matter what is injected.
    from dataclasses import replace

    from toolplane.audit import AuditLog

    log_path = tmp_path / "audit.jsonl"

    async def exercise() -> list[str]:
        runtime = Toolplane(
            ambient_cli=False, audit_log=AuditLog(log_path, enabled=True)
        )

        @runtime.tool(name="orders_get_order")
        async def get_order(order_id: str) -> dict:
            """Fetch one order."""
            return {"order_id": order_id}

        @runtime.tool(name="other_capability")
        async def other(order_id: str) -> str:
            """A different, legitimately visible capability."""
            return "WRONG CAPABILITY RAN"

        # also a hidden one, to prove injection cannot reach it either
        @runtime.tool(name="secret_admin")
        async def secret_admin(order_id: str) -> str:
            """Hidden from re-export."""
            return "HIDDEN RAN"

        runtime.registry._capabilities["secret_admin"] = replace(
            runtime.registry._capabilities["secret_admin"], hidden=True
        )

        app = build_mcp_facade(runtime, hybrid=True)
        async with Client(app) as client:
            # inject canonical at both a visible sibling and the hidden cap
            for target in ("other_capability", "secret_admin"):
                await client.call_tool(
                    "orders_get_order",
                    {"order_id": "x", "canonical": target},
                    raise_on_error=False,
                )
            return sorted(t.name for t in await client.list_tools())

    names = run(exercise())
    assert "secret_admin" not in names  # hidden, not re-exported

    dispatched = [
        json.loads(line)["capability"]
        for line in log_path.read_text().splitlines()
        if json.loads(line)["event"] == "dispatch"
    ]
    # every injection dispatched the ADVERTISED capability, never the
    # injected target — audit proves identity is un-hijackable
    assert dispatched == ["orders_get_order", "orders_get_order"]
    assert "other_capability" not in dispatched
    assert "secret_admin" not in dispatched


def test_hybrid_reexport_cannot_invoke_a_disallowed_cli_binary() -> None:
    # re-export must not become a hole in the CLI allowlist: the ambient
    # CLI capability is hidden (not re-exported), and even a canonical
    # injection at it cannot run a binary the policy forbids
    async def exercise() -> tuple[list[str], bool]:
        runtime = await Toolplane.from_config(
            {"cli": {"mode": "allowlist", "allow": ["git"]}}
        )

        @runtime.tool(name="orders_get_order")
        async def get_order(order_id: str) -> dict:
            """Fetch one order."""
            return {"order_id": order_id}

        app = build_mcp_facade(runtime, hybrid=True, cli_escalation=False)
        async with Client(app) as client:
            names = sorted(t.name for t in await client.list_tools())
            # try to reach the ambient CLI capability by injection and run
            # a forbidden binary
            result = await client.call_tool(
                "orders_get_order",
                {
                    "order_id": "x",
                    "canonical": "toolplane:cli/run",
                    "binary": "curl",
                },
                raise_on_error=False,
            )
            text = result.content[0].text if result.content else ""
        return names, ("curl" in text and "not allowed" in text)

    names, allowlist_error_leaked = run(exercise())
    # the CLI capability is not a re-exported tool at all
    assert not any("cli" in n and "run" in n for n in names)
    # and the injection neither ran curl nor reached the allowlist path
    assert not allowlist_error_leaked


def test_hybrid_tool_with_a_canonical_parameter_still_works() -> None:
    # the closure fix must not break a benign capability whose own schema
    # legitimately has a parameter named `canonical`
    async def exercise() -> str:
        runtime = Toolplane(ambient_cli=False)

        @runtime.tool(name="lookup")
        async def lookup(canonical: str) -> dict:
            """Look something up by its canonical id."""
            return {"looked_up": canonical}

        app = build_mcp_facade(runtime, hybrid=True)
        async with Client(app) as client:
            result = await client.call_tool("lookup", {"canonical": "abc"})
        return result.structured_content

    assert run(exercise()) == {"looked_up": "abc"}


def test_hybrid_tool_names_are_ascii_even_for_unicode_identifiers() -> None:
    # _is_safe_python_name accepts Unicode identifiers; MCP tool names must
    # be ASCII, so a Unicode canonical/alias must be sanitized (gauntlet)
    async def exercise() -> list[str]:
        runtime = Toolplane(ambient_cli=False)

        async def cafe(x: str) -> str:
            return x

        runtime.register(cafe, name="café_lookup")
        app = build_mcp_facade(runtime, hybrid=True)
        async with Client(app) as client:
            return [t.name for t in await client.list_tools()]

    meta = {"search_capabilities", "get_capability_schemas", "execute_code"}
    names = run(exercise())
    reexported = [n for n in names if n not in meta]
    assert reexported, names
    for name in reexported:
        assert name.isascii(), name
        assert "é" not in name


def _signal_capability(name: str, description: str, alias: str | None):
    from toolplane.capabilities import Capability

    return Capability(
        name=name,
        callable=lambda **k: None,
        description=description,
        parameters={"type": "object", "properties": {}},
        returns=None,
        tags=frozenset(),
        aliases=frozenset({alias}) if alias else frozenset(),
    )


def test_hybrid_name_signal_embeds_domain_and_query_vocabulary() -> None:
    # #127 variant A: the query-shaped leaf carries the domain plus the
    # description terms (incl. "status") the flat binding omits.
    from toolplane.mcp_facade import _MCP_SAFE_NAME_RE, _hybrid_tool_name

    cap = _signal_capability(
        "mcp:orders/get_order",
        "Fetch one order record: order_id, region, amount, status.",
        "orders_get_order",
    )
    name = _hybrid_tool_name(cap, set(), signal="name")
    assert name.startswith("orders_")
    assert "status" in name
    assert name.isascii()
    assert _MCP_SAFE_NAME_RE.match(name)


def test_hybrid_description_signal_frontloads_domain_and_leaf() -> None:
    # #127 variant B: lead with the server/domain word and leaf verbs.
    from toolplane.mcp_facade import _hybrid_tool_description

    cap = _signal_capability(
        "mcp:orders/get_order", "Fetch one order record.", "orders_get_order"
    )
    desc = _hybrid_tool_description(cap, signal="description")
    assert desc.startswith("orders: get order.")
    assert "Fetch one order record." in desc  # original preserved, not replaced


def test_hybrid_control_signal_leaves_name_and_description_unchanged() -> None:
    from toolplane.mcp_facade import _hybrid_tool_description, _hybrid_tool_name

    cap = _signal_capability(
        "mcp:orders/get_order", "Fetch one order record.", "orders_get_order"
    )
    assert _hybrid_tool_name(cap, set(), signal="control") == "orders_get_order"
    assert (
        _hybrid_tool_description(cap, signal="control")
        == "Fetch one order record."
    )


def test_hybrid_name_signal_stays_ascii_for_unicode_capability() -> None:
    from toolplane.mcp_facade import _MCP_SAFE_NAME_RE, _hybrid_tool_name

    cap = _signal_capability("mcp:wéird/dö", "ünïcode ☃ description", None)
    name = _hybrid_tool_name(cap, set(), signal="name")
    assert name.isascii()
    assert _MCP_SAFE_NAME_RE.match(name)


def test_hybrid_signal_env_falls_back_to_control_on_unknown_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from toolplane.mcp_facade import _hybrid_signal

    monkeypatch.setenv("TOOLPLANE_HYBRID_SIGNAL", "bogus")
    assert _hybrid_signal() == "control"
    monkeypatch.setenv("TOOLPLANE_HYBRID_SIGNAL", "NAME")
    assert _hybrid_signal() == "name"
    monkeypatch.delenv("TOOLPLANE_HYBRID_SIGNAL")
    assert _hybrid_signal() == "control"


def test_hybrid_curated_reexports_only_the_selected_capabilities() -> None:
    # #125: the whole point — at scale, re-export ONLY the curated
    # single/adaptive capabilities, not registry.all() (which is the worst
    # arm, #114)
    async def exercise() -> tuple[list[str], dict]:
        runtime = Toolplane(ambient_cli=False)

        @runtime.tool(name="orders_get_order", tags={"orders"})
        async def get_order(order_id: str) -> dict:
            """Fetch one order."""
            return {"order_id": order_id}

        @runtime.tool(name="crm_search", tags={"crm"})
        async def crm_search(q: str) -> list:
            """Search CRM."""
            return []

        app = build_mcp_facade(runtime, hybrid_include=["tag:orders"])
        async with Client(app) as client:
            names = sorted(t.name for t in await client.list_tools())
            result = await client.call_tool(
                "orders_get_order", {"order_id": "X"}
            )
        return names, result.structured_content

    names, dispatched = run(exercise())
    assert "orders_get_order" in names  # curated in
    assert "crm_search" not in names  # not selected
    assert "execute_code" in names  # meta-tools stay
    assert dispatched == {"order_id": "X"}


def test_hybrid_include_from_config_drives_curated_reexport(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [hybrid]
            enabled = true
            include = ["mcp:orders/*"]

            [mcp.servers.orders]
            command = "%s"
            args = ["%s"]
            """
        ).strip()
        % (sys.executable, str(Path(__file__).parent / "_stub_orders_server.py")),
        encoding="utf-8",
    )
    # the stub server file is created by the shared helper below
    _write_stub_orders_server(Path(__file__).parent / "_stub_orders_server.py")

    async def exercise() -> list[str]:
        app = await build_mcp_facade_from_config(str(config_path))
        async with Client(app) as client:
            return sorted(t.name for t in await client.list_tools())

    try:
        names = run(exercise())
    finally:
        (Path(__file__).parent / "_stub_orders_server.py").unlink(missing_ok=True)

    # the orders tools are re-exported natively, plus the meta-tools
    assert "search_capabilities" in names
    assert "execute_code" in names
    assert any(n.startswith("orders") or "get_order" in n for n in names)


def _write_stub_orders_server(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            '''
            from fastmcp import FastMCP

            mcp = FastMCP("orders")

            @mcp.tool
            def get_order(order_id: str) -> dict:
                """Fetch one order record."""
                return {"order_id": order_id, "status": "shipped"}

            if __name__ == "__main__":
                mcp.run()
            '''
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_hybrid_empty_selection_warns_and_re_exports_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # a typo'd glob passes config validation but selects nothing; the build
    # must warn loudly instead of silently degrading to the plain facade
    async def exercise() -> list[str]:
        runtime = Toolplane(ambient_cli=False)

        @runtime.tool(name="orders_get_order")
        async def get_order(order_id: str) -> dict:
            """Fetch one order."""
            return {"order_id": order_id}

        app = build_mcp_facade(runtime, hybrid_include=["no_such_thing/*"])
        async with Client(app) as client:
            return sorted(t.name for t in await client.list_tools())

    names = run(exercise())
    assert names == [
        "execute_code",
        "get_capability_schemas",
        "search_capabilities",
    ]
    assert "matched no capabilities" in capsys.readouterr().err


def test_curated_config_path_still_blocks_canonical_injection(
    tmp_path: Path,
) -> None:
    # Codex: the #114 identity-confusion fix must stay pinned through the
    # CONFIG-DRIVEN curated path, not only the direct build call

    log_path = tmp_path / "audit.jsonl"
    server_path = Path(__file__).parent / "_stub_two_tool_server.py"
    server_path.write_text(
        textwrap.dedent(
            '''
            from fastmcp import FastMCP

            mcp = FastMCP("orders")

            @mcp.tool
            def get_order(order_id: str) -> dict:
                """Fetch one order record."""
                return {"order_id": order_id}

            @mcp.tool
            def wipe(order_id: str) -> str:
                """A different capability the injection must not reach."""
                return "WIPED"

            if __name__ == "__main__":
                mcp.run()
            '''
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [hybrid]
            enabled = true
            include = ["mcp:orders/*"]

            [audit]
            enabled = true
            path = "%s"

            [mcp.servers.orders]
            command = "%s"
            args = ["%s"]
            """
        ).strip()
        % (str(log_path), sys.executable, str(server_path)),
        encoding="utf-8",
    )

    async def exercise() -> None:
        app = await build_mcp_facade_from_config(str(config_path))
        async with Client(app) as client:
            tools = {t.name: t for t in await client.list_tools()}
            target = next(t for t in tools if "get_order" in t)
            await client.call_tool(
                target,
                {"order_id": "x", "canonical": "mcp:orders/wipe"},
                raise_on_error=False,
            )

    try:
        run(exercise())
    finally:
        server_path.unlink(missing_ok=True)

    dispatched = [
        json.loads(line)["capability"]
        for line in log_path.read_text().splitlines()
        if json.loads(line).get("event") == "dispatch"
    ]
    # the audit must name the advertised orders capability, never the
    # injected wipe target, through the config path
    assert dispatched, "no dispatch was audited"
    assert all("wipe" not in name for name in dispatched)
    assert any("get_order" in name for name in dispatched)
