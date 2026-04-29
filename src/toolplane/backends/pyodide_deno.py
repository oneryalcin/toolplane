"""Pyodide running inside Deno."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from threading import Thread
from typing import Any

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
        namespace: Mapping[str, Callable[..., Any]],
        inputs: Mapping[str, Any] | None = None,
        packages: Sequence[str] = (),
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
        callback_token = secrets.token_urlsafe(24)
        callback_server = _CallbackServer(
            namespace=namespace,
            token=callback_token,
            loop=loop,
        )
        callback_server.start()

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
                    callback_port=callback_server.port,
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
                        callback_url=f"http://127.0.0.1:{callback_server.port}",
                        callback_token=callback_token,
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
                callback_server.close()
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


class _CallbackServer:
    def __init__(
        self,
        *,
        namespace: Mapping[str, Callable[..., Any]],
        token: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.namespace = namespace
        self.token = token
        self.loop = loop
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = int(self.httpd.server_address[1])
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_POST(self) -> None:
                if self.headers.get("Authorization") != f"Bearer {server.token}":
                    self._write_json({"ok": False, "error": {"type": "Unauthorized"}})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length).decode("utf-8")
                    request = json.loads(body)
                    name = request["name"]
                    params = request.get("params") or {}
                    call_tool = server.namespace["call_tool"]
                    future = asyncio.run_coroutine_threadsafe(
                        call_tool(name, params), server.loop
                    )
                    value = future.result(timeout=60)
                    self._write_json({"ok": True, "value": value})
                except Exception as exc:
                    self._write_json(
                        {
                            "ok": False,
                            "error": {
                                "type": type(exc).__name__,
                                "message": str(exc),
                                "traceback": traceback.format_exc(),
                            },
                        }
                    )

            def _write_json(self, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        return Handler


def _build_pyodide_code(
    code: str,
    *,
    inputs: Mapping[str, Any],
    callback_url: str,
    callback_token: str,
) -> str:
    wrapped = wrap_async_main(code)
    inputs_json = json.dumps(dict(inputs))
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

{wrapped}

await __toolplane_main__()
"""


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
