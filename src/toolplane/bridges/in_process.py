"""In-process bridge to the host capability registry."""

from __future__ import annotations

import base64
import traceback
from collections.abc import Mapping
from typing import Any

from ..adapters.ambient_cli import AMBIENT_CLI_CAPABILITY
from ..artifacts import (
    ARTIFACTS_LOAD_CAPABILITY,
    ARTIFACTS_SAVE_CAPABILITY,
    ArtifactStore,
    decode_artifact_b64,
)
from ..errors import CliPolicyError
from ..registry import CapabilityRegistry
from ..results import (
    RESULTS_LOAD_CAPABILITY,
    RESULTS_SAVE_CAPABILITY,
    ResultStore,
)
from .base import (
    ToolCallError,
    ToolCallRequest,
    ToolCallResponse,
    nearest_builtin_exception,
)


class InProcessBridge:
    """Dispatch capability calls in the current Python process."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        ambient_cli_allowed_binaries: set[str] | frozenset[str] | None = None,
        result_store: ResultStore | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.registry = registry
        self._ambient_cli_allowed_binaries = (
            frozenset(ambient_cli_allowed_binaries)
            if ambient_cli_allowed_binaries is not None
            else None
        )
        # The bridge is per-runtime, so it is the authority for store
        # dispatch: registries can be shared across runtimes, stores must not.
        self._result_store = result_store or ResultStore(enabled=False)
        self._artifact_store = artifact_store or ArtifactStore(enabled=False)

    async def call_tool(
        self,
        name: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        normalized_params = dict(params or {})
        if name == RESULTS_SAVE_CAPABILITY:
            return self._result_store.save(**normalized_params)
        if name == RESULTS_LOAD_CAPABILITY:
            return self._result_store.load(**normalized_params)
        if name == ARTIFACTS_SAVE_CAPABILITY:
            return self._artifact_store.save(
                decode_artifact_b64(normalized_params.get("data_b64")),
                filename=normalized_params.get("filename"),
                label=normalized_params.get("label"),
            )
        if name == ARTIFACTS_LOAD_CAPABILITY:
            return self._load_artifact_payload(normalized_params.get("handle"))
        self._enforce_ambient_cli_policy(name, normalized_params)
        return await self.registry.call(name, normalized_params)

    def _load_artifact_payload(self, handle: Any) -> dict[str, Any]:
        described = self._artifact_store.describe(handle)
        data = self._artifact_store.load(handle)
        return {
            "data_b64": base64.b64encode(data).decode("ascii"),
            "filename": described["filename"],
            "size_bytes": described["size_bytes"],
        }

    async def dispatch(self, request: ToolCallRequest) -> ToolCallResponse:
        try:
            return ToolCallResponse.success(
                await self.call_tool(request.name, request.params)
            )
        except Exception as exc:
            return ToolCallResponse.failure(
                ToolCallError(
                    type=type(exc).__name__,
                    message=str(exc),
                    traceback=traceback.format_exc(),
                    builtin=nearest_builtin_exception(exc),
                )
            )

    def _enforce_ambient_cli_policy(
        self,
        name: str,
        params: Mapping[str, Any],
    ) -> None:
        if (
            name != AMBIENT_CLI_CAPABILITY
            or self._ambient_cli_allowed_binaries is None
        ):
            return
        binary = str(params.get("binary", ""))
        if binary not in self._ambient_cli_allowed_binaries:
            allowed = ", ".join(sorted(self._ambient_cli_allowed_binaries)) or "none"
            raise CliPolicyError(
                f"CLI binary is not allowed by Toolplane policy: {binary}. "
                f"Allowed binaries: {allowed}."
            )
