from __future__ import annotations

import asyncio
import shutil

import pytest

from toolplane import Toolplane
from toolplane.artifacts import ArtifactStore
from toolplane.errors import ArtifactStoreError


def run(coro):
    return asyncio.run(coro)


def test_save_load_round_trip_and_describe() -> None:
    store = ArtifactStore()
    data = bytes([0, 1, 2, 255]) * 10

    handle = store.save(data, filename="demo.bin", label="probe")

    assert handle.startswith("art_")
    assert store.load(handle) == data
    described = store.describe(handle)
    assert described["filename"] == "demo.bin"
    assert described["size_bytes"] == len(data)
    assert described["label"] == "probe"
    store.close()


def test_non_bytes_save_points_at_result_store() -> None:
    store = ArtifactStore()

    with pytest.raises(ArtifactStoreError) as excinfo:
        store.save({"json": "shaped"})  # type: ignore[arg-type]

    assert "belong in the result store" in str(excinfo.value)


def test_filename_rejects_path_traversal() -> None:
    store = ArtifactStore()

    for bad in ("../escape.bin", "a/b.bin", ".hidden", ""):
        with pytest.raises(ArtifactStoreError):
            store.save(b"x", filename=bad)
    store.close()


def test_caps_are_loud() -> None:
    store = ArtifactStore(max_entries=1, max_entry_bytes=8, max_total_bytes=8)

    with pytest.raises(ArtifactStoreError, match="per-artifact limit"):
        store.save(b"123456789")
    store.save(b"1234")
    with pytest.raises(ArtifactStoreError, match="full"):
        store.save(b"1")
    store.close()


def test_ttl_expiry_removes_bytes_from_disk() -> None:
    now = [0.0]
    store = ArtifactStore(ttl_seconds=10, clock=lambda: now[0])

    handle = store.save(b"payload")
    path = store._entry_dir(handle)
    assert path.exists()
    now[0] = 11.0

    with pytest.raises(ArtifactStoreError, match="unknown or expired"):
        store.load(handle)
    assert not path.exists()
    store.close()


def test_close_removes_the_scratch_directory() -> None:
    store = ArtifactStore()
    store.save(b"payload")
    root = store._root
    assert root is not None and root.exists()

    store.close()

    assert not root.exists()


def test_disabled_store_raises_its_own_message() -> None:
    store = ArtifactStore(enabled=False)

    with pytest.raises(ArtifactStoreError, match="disabled"):
        store.save(b"x")


_ROUND_TRIP = """
data = b"\\x00\\x01\\xffbinary"
handle = await save_artifact(data, filename="demo.bin")
back = await load_artifact(handle)
return {"roundtrip": back == data, "handle_prefix": handle[:4]}
"""

_CATCH = """
try:
    await save_artifact("not bytes")
except ValueError as exc:
    return {"caught": "ValueError", "msg": str(exc)}
"""


@pytest.mark.parametrize("backend", ["monty", "local_unsafe"])
def test_artifact_round_trip_in_sandbox(backend: str) -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(runtime.execute(_ROUND_TRIP, backend=backend))

    assert result.error is None, result.error
    assert result.value == {"roundtrip": True, "handle_prefix": "art_"}
    # the response must carry the handle and resource URI: agents never
    # enumerate resources unaided
    (artifact,) = result.artifacts
    assert artifact["filename"] == "demo.bin"
    assert artifact["uri"] == f"toolplane://artifacts/{artifact['handle']}"


@pytest.mark.parametrize("backend", ["monty", "local_unsafe"])
def test_artifact_type_error_catchable_as_valueerror(backend: str) -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(runtime.execute(_CATCH, backend=backend))

    assert result.error is None, result.error
    assert result.value["caught"] == "ValueError"
    assert "belong in the result store" in result.value["msg"]


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_artifact_round_trip_on_pyodide() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(runtime.execute(_ROUND_TRIP, backend="pyodide-deno"))

    assert result.error is None, result.error
    assert result.value == {"roundtrip": True, "handle_prefix": "art_"}
    (artifact,) = result.artifacts
    assert artifact["uri"] == f"toolplane://artifacts/{artifact['handle']}"


@pytest.mark.skipif(shutil.which("deno") is None, reason="Deno is not installed")
def test_artifact_type_error_catchable_as_valueerror_on_pyodide() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(runtime.execute(_CATCH, backend="pyodide-deno"))

    assert result.error is None, result.error
    assert result.value["caught"] == "ValueError"


def test_artifacts_field_empty_when_nothing_saved() -> None:
    runtime = Toolplane(ambient_cli=False)

    result = run(runtime.execute("return 1", backend="monty"))

    assert result.error is None
    assert result.artifacts == ()
