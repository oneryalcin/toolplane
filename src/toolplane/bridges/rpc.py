"""HTTP callback bridge for sandboxed backends."""

from __future__ import annotations

import asyncio
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from .base import HostBridge, ToolCallError, ToolCallRequest, ToolCallResponse


class HttpCallbackBridge:
    """Expose a host bridge over a localhost JSON RPC callback endpoint."""

    def __init__(
        self,
        *,
        bridge: HostBridge,
        loop: asyncio.AbstractEventLoop,
        token: str | None = None,
        call_timeout_seconds: float = 60.0,
    ) -> None:
        self.bridge = bridge
        self.loop = loop
        self.token = token or secrets.token_urlsafe(24)
        self.call_timeout_seconds = call_timeout_seconds
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = int(self.httpd.server_address[1])
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        callback = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_POST(self) -> None:
                if self.headers.get("Authorization") != f"Bearer {callback.token}":
                    self._write_response(
                        ToolCallResponse.failure(
                            ToolCallError(
                                type="Unauthorized",
                                message="Invalid callback token",
                            )
                        )
                    )
                    return
                try:
                    request = self._read_request()
                except ValueError as exc:
                    self._write_response(
                        ToolCallResponse.failure(
                            ToolCallError(
                                type="InvalidRequest",
                                message=str(exc),
                            )
                        )
                    )
                    return
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        callback.bridge.dispatch(request), callback.loop
                    )
                    response = future.result(timeout=callback.call_timeout_seconds)
                except Exception as exc:
                    response = ToolCallResponse.failure(
                        ToolCallError(
                            type=type(exc).__name__,
                            message=str(exc),
                        )
                    )
                self._write_response(response)

            def _read_request(self) -> ToolCallRequest:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                try:
                    return ToolCallRequest.model_validate_json(body)
                except ValueError as exc:
                    raise ValueError(f"Invalid callback request: {exc}") from exc

            def _write_response(self, response: ToolCallResponse) -> None:
                raw = _encode_response(response)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        return Handler


def _encode_response(response: ToolCallResponse) -> bytes:
    try:
        return response.model_dump_json().encode("utf-8")
    except Exception as exc:
        fallback = ToolCallResponse.failure(
            ToolCallError(
                type="SerializationError",
                message=f"Tool result is not JSON serializable: {exc}",
            )
        )
        return fallback.model_dump_json().encode("utf-8")
