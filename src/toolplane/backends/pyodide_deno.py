"""Pyodide running inside Deno."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from string import Template
from typing import Any

from ..adapters.ambient_cli import (
    is_safe_cli_name,
    render_pyodide_cli_namespace,
)
from ..bridges.base import HostBridge
from ..bridges.rpc import HttpCallbackBridge
from ..errors import NamespaceCollisionError
from ..execution import BackendCapabilities, ExecutionError, ExecutionResult
from ..results import _NON_JSON_GUIDANCE, render_pyodide_result_bindings
from ._python import (
    UNAWAITED_CALL_ERROR_TYPE,
    UNAWAITED_CALL_MESSAGE,
    find_unawaited_calls,
    stderr_reports_unawaited,
    wrap_async_main,
)


class PyodideDenoBackend:
    """Run Python in a Pyodide WebAssembly interpreter hosted by Deno."""

    name = "pyodide-deno"
    capabilities = BackendCapabilities(
        imports=True,
        third_party_packages=True,
        package_install=True,
        filesystem="none",
        network="restricted",
        resource_limits=frozenset({"timeout", "deno-permissions"}),
        persistence="none",
        startup_latency="high",
    )

    def __init__(self, *, deno_path: str = "deno", timeout_seconds: float = 60.0):
        self.deno_path = deno_path
        self.timeout_seconds = timeout_seconds

    async def run(
        self,
        code: str,
        *,
        bridge: HostBridge,
        inputs: Mapping[str, Any] | None = None,
        packages: Sequence[str] = (),
        namespace: Mapping[str, str] | None = None,
        scoped_namespace: Mapping[str, Mapping[str, str]] | None = None,
        ambient_cli: bool = False,
        ambient_cli_names: Sequence[str] = (),
        ambient_cli_allowed_binaries: Sequence[str] | None = None,
    ) -> ExecutionResult:
        started = time.perf_counter()
        binding_names = _async_binding_names(
            inputs=inputs or {},
            namespace=namespace or {},
            scoped_namespace=scoped_namespace or {},
            ambient_cli=ambient_cli,
            ambient_cli_names=ambient_cli_names,
        )
        preflight = find_unawaited_calls(code, binding_names)
        if preflight:
            return _error_result(
                backend=self.name,
                started=started,
                error_type=UNAWAITED_CALL_ERROR_TYPE,
                message="; ".join(preflight),
            )
        if shutil.which(self.deno_path) is None:
            return _error_result(
                backend=self.name,
                started=started,
                error_type="DenoNotFoundError",
                message=(
                    f"Deno executable not found: {self.deno_path!r}. "
                    "Install Deno to use the pyodide-deno backend."
                ),
            )

        loop = asyncio.get_running_loop()
        callback_bridge = HttpCallbackBridge(
            bridge=bridge,
            loop=loop,
            call_timeout_seconds=self.timeout_seconds,
        )
        callback_bridge.start()

        with tempfile.TemporaryDirectory(prefix="toolplane-pyodide-deno-") as temp_dir:
            process: asyncio.subprocess.Process | None = None
            try:
                runner_dir = Path(temp_dir)
                deno_cache_dir = runner_dir / "deno-cache"
                deno_cache_dir.mkdir()
                deno_cache_dir = deno_cache_dir.resolve()
                server_port = _free_port()
                deno_token = secrets.token_urlsafe(24)
                runner_path = runner_dir / "runner.js"
                runner_path.write_text(
                    _render_runner(
                        host="127.0.0.1",
                        port=server_port,
                        auth_token=deno_token,
                    ),
                    encoding="utf-8",
                )

                process = await self._start_deno(
                    runner_path=runner_path,
                    deno_cache_dir=deno_cache_dir,
                    server_port=server_port,
                    callback_port=callback_bridge.port,
                )
                server_url = f"http://127.0.0.1:{server_port}"
                await _wait_for_server(
                    server_url,
                    process=process,
                    timeout=self.timeout_seconds,
                )

                payload = {
                    "code": _build_pyodide_code(
                        code,
                        inputs=inputs or {},
                        namespace=namespace or {},
                        scoped_namespace=scoped_namespace or {},
                        ambient_cli=ambient_cli,
                        ambient_cli_names=ambient_cli_names,
                        ambient_cli_allowed_binaries=ambient_cli_allowed_binaries,
                        callback_url=callback_bridge.url,
                        callback_token=callback_bridge.token,
                    ),
                    "packages": list(packages),
                }
                response = await asyncio.to_thread(
                    _post_json,
                    server_url,
                    payload,
                    self.timeout_seconds,
                    deno_token,
                )
                return _response_to_result(
                    response,
                    backend=self.name,
                    started=started,
                    binding_names=binding_names,
                )
            except Exception as exc:
                return ExecutionResult(
                    duration_ms=_elapsed_ms(started),
                    backend=self.name,
                    error=ExecutionError(
                        type=type(exc).__name__,
                        message=str(exc),
                        traceback=traceback.format_exc(),
                    ),
                )
            finally:
                callback_bridge.close()
                if process is not None:
                    await _terminate_process(process)

    async def _start_deno(
        self,
        *,
        runner_path: Path,
        deno_cache_dir: Path,
        server_port: int,
        callback_port: int,
    ) -> asyncio.subprocess.Process:
        cmd = [
            self.deno_path,
            "run",
            f"--allow-net=127.0.0.1:{server_port},127.0.0.1:{callback_port},cdn.jsdelivr.net:443,pypi.org:443,files.pythonhosted.org:443",
            f"--allow-read={deno_cache_dir}",
            f"--allow-write={deno_cache_dir}",
            str(runner_path),
        ]
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "DENO_DIR": str(deno_cache_dir)},
        )


def _async_binding_names(
    *,
    inputs: Mapping[str, Any],
    namespace: Mapping[str, str],
    scoped_namespace: Mapping[str, Mapping[str, str]],
    ambient_cli: bool,
    ambient_cli_names: Sequence[str],
) -> set[str]:
    """Names bound to async callables in the rendered sandbox.

    Mirrors the render precedence in _build_pyodide_code closely enough for
    preflight: inputs shadow everything, so subtracting them can only cause
    false negatives, never false positives.
    """
    names = {"call_tool"}
    names |= {name for name in namespace if name.isidentifier()}
    reserved = set(inputs) | set(namespace) | set(scoped_namespace)
    cli_names: set[str] = set()
    if ambient_cli:
        cli_names = {
            name
            for name in ambient_cli_names
            if name not in reserved and is_safe_cli_name(name)
        }
        names |= cli_names
    for result_name in ("save_result", "load_result"):
        if result_name not in reserved | cli_names:
            names.add(result_name)
    return names - set(inputs)


def _build_pyodide_code(
    code: str,
    *,
    inputs: Mapping[str, Any],
    namespace: Mapping[str, str],
    scoped_namespace: Mapping[str, Mapping[str, str]],
    ambient_cli: bool,
    ambient_cli_names: Sequence[str],
    ambient_cli_allowed_binaries: Sequence[str] | None,
    callback_url: str,
    callback_token: str,
) -> str:
    wrapped = wrap_async_main(code)
    inputs_json = json.dumps(dict(inputs))
    reserved_names = set(inputs) | set(namespace) | set(scoped_namespace)
    _ensure_no_input_collisions(
        inputs,
        {"call_tool", "cli"} | set(namespace) | set(scoped_namespace),
    )
    cli_namespace_code = (
        render_pyodide_cli_namespace(
            ambient_cli_names,
            reserved=reserved_names,
            allowed_binaries=(
                set(ambient_cli_allowed_binaries)
                if ambient_cli_allowed_binaries is not None
                else None
            ),
        )
        if ambient_cli
        else ""
    )
    # CLI aliases render before the result bindings, so reserve them too —
    # monty/local give CLI names precedence and pyodide must match
    results_reserved = set(reserved_names)
    if ambient_cli:
        results_reserved.add("cli")
        results_reserved.update(
            name
            for name in ambient_cli_names
            if name not in reserved_names and is_safe_cli_name(name)
        )
    results_code = render_pyodide_result_bindings(reserved=results_reserved)
    namespace_code = _render_callable_namespace(namespace)
    scoped_namespace_code = _render_scoped_namespace(scoped_namespace)
    non_json_guidance = _NON_JSON_GUIDANCE
    return f"""
import inspect as __toolplane_inspect__
import json
from js import Object, fetch
from pyodide.ffi import to_js

__toolplane_callback_url__ = {callback_url!r}
__toolplane_callback_token__ = {callback_token!r}
__toolplane_unawaited_flag__ = False

def __toolplane_scan_unawaited__(value):
    if __toolplane_inspect__.iscoroutine(value):
        value.close()
        return True
    if __toolplane_inspect__.isawaitable(value):
        return True
    if isinstance(value, dict):
        found = [
            __toolplane_scan_unawaited__(item)
            for pair in value.items()
            for item in pair
        ]
        return any(found)
    if isinstance(value, (list, tuple, set, frozenset)):
        return any([__toolplane_scan_unawaited__(item) for item in value])
    return False

async def call_tool(name, params=None):
    try:
        payload = json.dumps(
            {{"name": name, "params": params or {{}}}}, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise Exception(
            "arguments for " + repr(name) + " are not JSON-serializable ("
            + str(exc) + "); only JSON-shaped values can cross the sandbox "
            "bridge; " + {non_json_guidance!r}
        )
    response = await fetch(
        __toolplane_callback_url__,
        to_js({{
            "method": "POST",
            "headers": {{
                "Authorization": "Bearer " + __toolplane_callback_token__,
                "Content-Type": "application/json",
            }},
            "body": payload,
        }}, dict_converter=Object.fromEntries),
    )
    data = json.loads(await response.text())
    if data.get("ok"):
        return data.get("value")
    error = data.get("error") or {{}}
    raise RuntimeError(f"{{error.get('type', 'ToolError')}}: {{error.get('message', '')}}")

globals().update(json.loads({inputs_json!r}))

{cli_namespace_code}

{results_code}

{namespace_code}

{scoped_namespace_code}

{wrapped}

__toolplane_result__ = await __toolplane_main__()
if __toolplane_scan_unawaited__(__toolplane_result__):
    __toolplane_unawaited_flag__ = True
    __toolplane_result__ = None
__toolplane_result__
"""


def _render_callable_namespace(namespace: Mapping[str, str]) -> str:
    lines: list[str] = []
    for callable_name, capability_name in namespace.items():
        if not callable_name.isidentifier():
            continue
        lines.extend(
            [
                f"async def {callable_name}(**params):",
                f"    return await call_tool({capability_name!r}, params)",
                "",
            ]
        )
    return "\n".join(lines)


def _render_scoped_namespace(
    scoped_namespace: Mapping[str, Mapping[str, str]],
) -> str:
    if not scoped_namespace:
        return ""

    lines: list[str] = [
        "class _ToolplaneCapabilityNamespace:",
        "    def __init__(self, bindings):",
        "        self._bindings = dict(bindings)",
        "",
        "    def __getattr__(self, member):",
        "        if member.startswith('_') or member not in self._bindings:",
        "            raise AttributeError(member)",
        "        capability_name = self._bindings[member]",
        "        async def dispatch(**params):",
        "            return await call_tool(capability_name, params)",
        "        dispatch.__name__ = member",
        "        return dispatch",
        "",
    ]
    for namespace, members in scoped_namespace.items():
        members_json = json.dumps(dict(members), sort_keys=True)
        lines.append(
            f"{namespace} = _ToolplaneCapabilityNamespace(json.loads({members_json!r}))"
        )
    return "\n".join(lines)


def _ensure_no_input_collisions(
    inputs: Mapping[str, Any],
    reserved_names: set[str],
) -> None:
    collisions = sorted(set(inputs) & reserved_names)
    if collisions:
        joined = ", ".join(collisions)
        raise NamespaceCollisionError(
            f"Input names collide with Toolplane namespace bindings: {joined}"
        )


def _render_runner(*, host: str, port: int, auth_token: str) -> str:
    return _RUNNER_TEMPLATE.safe_substitute(
        host=host,
        port=str(port),
        auth_token=auth_token,
    )


async def _wait_for_server(
    url: str,
    *,
    process: asyncio.subprocess.Process,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.returncode is not None:
            stderr = await _read_stream(process.stderr)
            raise RuntimeError(f"Deno Pyodide server exited early: {stderr}")
        try:
            await asyncio.to_thread(_get, url, 1.0)
            return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.2)
    raise TimeoutError(f"Deno Pyodide server did not start: {last_error}")


async def _read_stream(stream: asyncio.StreamReader | None) -> str:
    if stream is None:
        return ""
    data = await stream.read()
    return data.decode("utf-8", errors="replace")


def _get(url: str, timeout: float) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    token: str,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Deno server returned {exc.code}: {detail}") from exc


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def _response_to_result(
    response: dict[str, Any],
    *,
    backend: str,
    started: float,
    binding_names: set[str] = frozenset(),
) -> ExecutionResult:
    error = response.get("error")
    if error:
        return ExecutionResult(
            value=None,
            stdout=response.get("stdout") or "",
            stderr=response.get("stderr") or "",
            duration_ms=_elapsed_ms(started),
            backend=backend,
            error=ExecutionError(
                type=error.get("type") or error.get("name") or "ExecutionError",
                message=error.get("message") or "",
                traceback=error.get("traceback") or error.get("stack") or "",
            ),
        )
    # out-of-band signal from the runner: raising in-sandbox surfaces as an
    # opaque PythonError through the Deno layer, and a marker inside the
    # result would reserve a user-visible JSON shape. The stderr warning scan
    # covers assign-then-inspect misuse the result scan cannot see.
    if response.get("unawaited") or stderr_reports_unawaited(
        response.get("stderr") or "", binding_names
    ):
        return ExecutionResult(
            stdout=response.get("stdout") or "",
            stderr=response.get("stderr") or "",
            duration_ms=_elapsed_ms(started),
            backend=backend,
            error=ExecutionError(
                type=UNAWAITED_CALL_ERROR_TYPE,
                message=UNAWAITED_CALL_MESSAGE,
            ),
        )
    return ExecutionResult(
        value=response.get("result"),
        stdout=response.get("stdout") or "",
        stderr=response.get("stderr") or "",
        duration_ms=_elapsed_ms(started),
        backend=backend,
    )


def _error_result(
    *,
    backend: str,
    started: float,
    error_type: str,
    message: str,
) -> ExecutionResult:
    return ExecutionResult(
        duration_ms=_elapsed_ms(started),
        backend=backend,
        error=ExecutionError(type=error_type, message=message, traceback=""),
    )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_RUNNER_TEMPLATE = Template(
    r"""
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { loadPyodide } from "npm:pyodide";

const AUTH_TOKEN = "$auth_token";
const pyodidePromise = loadPyodide();

function toJsonable(value) {
  if (value && typeof value.toJs === "function") {
    const converted = value.toJs({ dict_converter: Object.fromEntries });
    if (typeof value.destroy === "function") {
      value.destroy();
    }
    return converted;
  }
  return value;
}

async function loadPackages(pyodide, packages) {
  for (const pkg of packages || []) {
    try {
      await pyodide.loadPackage(pkg);
    } catch (_err) {
      await pyodide.loadPackage("micropip");
      const micropip = pyodide.pyimport("micropip");
      await micropip.install(pkg);
    }
  }
}

async function executePython(code, packages) {
  const pyodide = await pyodidePromise;
  await loadPackages(pyodide, packages);
  pyodide.runPython(`
import sys
import io
sys.stdout = io.StringIO()
sys.stderr = io.StringIO()
`);

  let result = null;
  let error = null;
  try {
    result = toJsonable(await pyodide.runPythonAsync(code));
  } catch (err) {
    const message = String(err.message || err);
    const matches = [...message.matchAll(/\n([A-Za-z_][\w.]*): /g)];
    const pythonType = matches.length
      ? matches[matches.length - 1][1].split(".").pop()
      : err.constructor.name;
    error = {
      type: pythonType,
      message,
      traceback: message,
      stack: String(err.stack || ""),
    };
  }

  // out-of-band: the unawaited-call signal must never live inside the user
  // result payload, where it would reserve a JSON shape
  let unawaited = false;
  try {
    unawaited = !!pyodide.runPython(
      "globals().get('__toolplane_unawaited_flag__', False)",
    );
  } catch (_err) {
    unawaited = false;
  }

  const stdout = pyodide.runPython("sys.stdout.getvalue()");
  const stderr = pyodide.runPython("sys.stderr.getvalue()");
  return { result, stdout, stderr, error, unawaited };
}

serve(async (request) => {
  if (request.method === "GET") {
    return new Response("ok", { status: 200 });
  }
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }
  if (request.headers.get("Authorization") !== `Bearer ${AUTH_TOKEN}`) {
    return new Response("Unauthorized", { status: 401 });
  }
  try {
    const body = await request.json();
    const result = await executePython(body.code || "", body.packages || []);
    return new Response(JSON.stringify(result), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(
      JSON.stringify({
        error: {
          type: err.constructor.name,
          message: String(err.message || err),
          traceback: String(err.stack || ""),
        },
      }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      },
    );
  }
}, { hostname: "$host", port: $port });
"""
)
