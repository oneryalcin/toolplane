from __future__ import annotations

import asyncio

import pytest

from toolplane import CapabilityRegistry, Toolplane
from toolplane.errors import ResultStoreError
from toolplane.mcp_facade import resolve_serve_config
from toolplane.config import load_toolplane_config
from toolplane.results import ResultStore


def run(coro):
    return asyncio.run(coro)


# --- ResultStore unit behavior ---


def test_save_load_round_trip_canonicalizes_to_json() -> None:
    store = ResultStore()

    handle = store.save({"items": (1, 2), "ok": True}, label="issues")

    assert handle.startswith("res_")
    assert len(handle) > len("res_") + 10
    assert store.load(handle) == {"items": [1, 2], "ok": True}


def test_labels_are_metadata_not_authority() -> None:
    store = ResultStore()

    first = store.save({"v": 1}, label="issues")
    second = store.save({"v": 2}, label="issues")

    assert first != second
    assert store.load(first) == {"v": 1}
    assert store.load(second) == {"v": 2}


def test_unknown_handle_fails_with_handle_in_message() -> None:
    store = ResultStore()

    with pytest.raises(ResultStoreError, match="res_nope"):
        store.load("res_nope")


def test_non_json_value_fails_at_save_with_type() -> None:
    store = ResultStore()

    with pytest.raises(ResultStoreError, match="'object'.*not.*JSON"):
        store.save(object())


def test_non_finite_floats_rejected_at_save() -> None:
    store = ResultStore()

    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ResultStoreError, match="not JSON-serializable"):
            store.save({"v": bad})


def test_disabled_store_fails_loudly_on_save_and_load() -> None:
    store = ResultStore(enabled=False)

    with pytest.raises(ResultStoreError, match="disabled"):
        store.save(1)
    with pytest.raises(ResultStoreError, match="disabled"):
        store.load("res_any")


def test_max_entries_cap_fails_with_limit_in_message() -> None:
    store = ResultStore(max_entries=1)
    store.save(1)

    with pytest.raises(ResultStoreError, match="full \\(1 entries\\)"):
        store.save(2)


def test_max_entry_bytes_cap_suggests_smaller_projection() -> None:
    store = ResultStore(max_entry_bytes=8)

    with pytest.raises(ResultStoreError, match="smaller projection"):
        store.save("x" * 100)


def test_max_total_bytes_cap_counts_across_entries() -> None:
    store = ResultStore(max_total_bytes=30)
    store.save("x" * 20)

    with pytest.raises(ResultStoreError, match="total limit"):
        store.save("y" * 20)


def test_labels_count_against_size_caps() -> None:
    store = ResultStore(max_entry_bytes=16)
    store.save("x", label="ok")

    with pytest.raises(ResultStoreError, match="per-entry limit"):
        store.save("x", label="y" * 100)


def test_ttl_expires_entries_and_frees_space() -> None:
    now = [0.0]
    store = ResultStore(ttl_seconds=10, max_entries=1, clock=lambda: now[0])
    handle = store.save({"v": 1})

    now[0] = 11.0

    with pytest.raises(ResultStoreError, match="unknown or expired"):
        store.load(handle)
    assert store.save({"v": 2}).startswith("res_")


# --- Facade behavior across execute_code runs ---


def test_two_run_save_load_on_monty_summary_plus_handle() -> None:
    runtime = Toolplane(ambient_cli=False)

    first = run(
        runtime.execute(
            """
issues = [{"id": i, "title": f"bug {i}"} for i in range(50)]
handle = await save_result(issues, label="issues")
return {"count": len(issues), "handle": handle}
""",
            backend="monty",
        )
    )

    assert first.error is None, first.error
    assert first.value["count"] == 50
    # the full value never appears in the first response
    assert set(first.value) == {"count", "handle"}

    second = run(
        runtime.execute(
            "prev = await load_result(h)\nreturn {'n': len(prev), 'first': prev[0]}",
            backend="monty",
            inputs={"h": first.value["handle"]},
        )
    )

    assert second.error is None, second.error
    assert second.value == {"n": 50, "first": {"id": 0, "title": "bug 0"}}


def test_canonical_call_tool_path_needs_no_sugar() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            'h = await call_tool("toolplane:results/save", {"value": [1, 2]})\n'
            'return await call_tool("toolplane:results/load", {"handle": h})',
            backend="monty",
        )
    )

    assert result.error is None, result.error
    assert result.value == [1, 2]


def test_live_object_rejected_at_save_on_local_unsafe() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            """
import io
try:
    await save_result(io.StringIO())
except Exception as exc:
    return str(exc)
""",
            backend="local_unsafe",
        )
    )

    assert result.error is None, result.error
    assert "not JSON-serializable" in result.value


def test_store_disabled_via_config_is_catchable_in_snippet() -> None:
    async def exercise():
        runtime = await Toolplane.from_config({"results": {"enabled": False}})
        return await runtime.execute(
            """
try:
    await save_result(1)
except Exception as exc:
    return str(exc)
""",
            backend="monty",
        )

    result = run(exercise())

    assert result.error is None, result.error
    assert "results store is disabled" in result.value


def test_handles_are_scoped_to_one_runtime() -> None:
    saver = Toolplane(ambient_cli=False)
    other = Toolplane(ambient_cli=False)

    handle = run(
        saver.execute("return await save_result({'v': 1})", backend="monty")
    ).value

    result = run(
        other.execute(
            "return await load_result(h)",
            backend="monty",
            inputs={"h": handle},
        )
    )

    assert result.error is not None
    assert "unknown or expired result handle" in result.error.message


def test_shared_registry_does_not_leak_handles_across_runtimes() -> None:
    registry = CapabilityRegistry()
    saver = Toolplane(registry=registry, ambient_cli=False)
    other = Toolplane(registry=registry, ambient_cli=False)

    handle = run(
        saver.execute("return await save_result({'v': 1})", backend="monty")
    ).value

    result = run(
        other.execute(
            "return await load_result(h)",
            backend="monty",
            inputs={"h": handle},
        )
    )

    assert result.error is not None
    assert "unknown or expired result handle" in result.error.message


def test_shared_registry_disabled_store_cannot_save_through_other_runtime() -> None:
    registry = CapabilityRegistry()
    Toolplane(registry=registry, ambient_cli=False)  # enabled store, same registry

    async def exercise():
        disabled = await Toolplane.from_config(
            {"results": {"enabled": False}},
            registry=registry,
        )
        return await disabled.execute(
            """
try:
    await save_result(1)
except Exception as exc:
    return str(exc)
""",
            backend="monty",
        )

    result = run(exercise())

    assert result.error is None, result.error
    assert "results store is disabled" in result.value


def test_inputs_shadow_result_sugar_bindings() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            "return save_result",
            backend="monty",
            inputs={"save_result": 5},
        )
    )

    assert result.error is None, result.error
    assert result.value == 5


def test_pyodide_gives_cli_alias_precedence_over_result_sugar() -> None:
    from toolplane.backends.pyodide_deno import _build_pyodide_code

    code = _build_pyodide_code(
        "return 1",
        inputs={},
        namespace={},
        scoped_namespace={},
        ambient_cli=True,
        ambient_cli_names=("save_result",),
        ambient_cli_allowed_binaries=None,
        callback_url="http://127.0.0.1:1/",
        callback_token="token",
    )

    # an allowlisted binary named save_result wins, matching monty/local
    assert "async def save_result" not in code
    assert "save_result = cli.save_result" in code
    assert "async def load_result" in code


def test_result_capabilities_hidden_from_discovery_but_schemas_resolve() -> None:
    runtime = Toolplane(ambient_cli=False)

    namespace = runtime.registry.callable_namespace()
    assert "save_result" not in namespace

    listing = run(runtime.list_tools())
    assert "toolplane:results/save" not in listing

    schemas = run(runtime.get_schema(["toolplane:results/save"]))
    assert "toolplane:results/save" in schemas


# --- Transport policy ---


def test_serve_config_disables_results_on_multi_client_transports() -> None:
    config = load_toolplane_config({})
    assert config.results.enabled

    http = resolve_serve_config(config, "http")
    assert not http.results.enabled
    # original config untouched
    assert config.results.enabled

    stdio = resolve_serve_config(config, "stdio")
    assert stdio.results.enabled


def test_local_unsafe_unawaited_call_fails_instead_of_returning_coroutine() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            "h = save_result({'v': 1})\nreturn h",
            backend="local_unsafe",
        )
    )

    assert result.error is not None
    assert result.error.type == "UnawaitedToolCallError"
    assert "await" in result.error.message


def test_pyodide_save_result_renders_non_json_guidance() -> None:
    from toolplane.results import render_pyodide_result_bindings

    code = render_pyodide_result_bindings(reserved=frozenset())

    # in-sandbox pre-check must mirror the store's admission rule and message:
    # on pyodide the value dies at RPC serialization before the store can speak
    assert "allow_nan=False" in code
    assert "save a JSON-shaped projection instead" in code


def test_store_and_pyodide_bindings_share_guidance_text() -> None:
    from toolplane.results import render_pyodide_result_bindings

    store = ResultStore()
    try:
        store.save(object())
    except ResultStoreError as exc:
        store_message = str(exc)

    rendered = render_pyodide_result_bindings(reserved=frozenset())
    guidance = "save a JSON-shaped projection instead"
    assert guidance in store_message
    assert guidance in rendered


def test_local_unsafe_unawaited_call_detected_as_mapping_key() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(
        runtime.execute(
            "return {save_result({'v': 1}): 'x'}",
            backend="local_unsafe",
        )
    )

    assert result.error is not None
    assert result.error.type == "UnawaitedToolCallError"


def test_pyodide_renders_unawaited_scan_and_call_tool_guidance() -> None:
    from toolplane.backends.pyodide_deno import _build_pyodide_code

    code = _build_pyodide_code(
        "return 1",
        inputs={},
        namespace={},
        scoped_namespace={},
        ambient_cli=False,
        ambient_cli_names=(),
        ambient_cli_allowed_binaries=None,
        callback_url="http://127.0.0.1:1/",
        callback_token="token",
    )

    # nested un-awaited results must fail loudly, not serialize to garbage;
    # the scan swaps in a sentinel the host maps to UnawaitedToolCallError
    assert "__toolplane_scan_unawaited__" in code
    assert "__toolplane_unawaited_call__" in code
    # canonical call_tool path shares the store's admission rule and guidance
    assert code.count("allow_nan=False") >= 2
    assert "save a JSON-shaped projection instead" in code
