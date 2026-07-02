from __future__ import annotations

import asyncio

import pytest

from toolplane import Toolplane
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
