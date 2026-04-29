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

from ..bridges.base import HostBridge
from ..bridges.rpc import HttpCallbackBridge
from ..execution import BackendCapabilities, ExecutionError, ExecutionResult
from ._python import wrap_async_main


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
    ) -> ExecutionResult:
        started = time.perf_counter()
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
                return _response_to_result(response, backend=self.name, started=started)
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


def _build_pyodide_code(
    code: str,
    *,
    inputs: Mapping[str, Any],
    namespace: Mapping[str, str],
    callback_url: str,
    callback_token: str,
) -> str:
    wrapped = wrap_async_main(code)
    inputs_json = json.dumps(dict(inputs))
    namespace_code = _render_callable_namespace(namespace)
    return f"""
import json
from js import Object, fetch
from pyodide.ffi import to_js

__toolplane_callback_url__ = {callback_url!r}
__toolplane_callback_token__ = {callback_token!r}

async def call_tool(name, params=None):
    payload = json.dumps({{"name": name, "params": params or {{}}}})
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

{namespace_code}

{wrapped}

await __toolplane_main__()
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

  const stdout = pyodide.runPython("sys.stdout.getvalue()");
  const stderr = pyodide.runPython("sys.stderr.getvalue()");
  return { result, stdout, stderr, error };
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
