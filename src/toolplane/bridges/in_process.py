"""In-process bridge to the host capability registry."""

from __future__ import annotations

import base64
import traceback
from collections.abc import Mapping
from typing import Any

from ..adapters.ambient_cli import (
    AMBIENT_CLI_CAPABILITY,
    CLI_SHAPE_GUIDANCE,
    AmbientCliPolicy,
)
from ..audit import AuditLog
from ..errors import CapabilityNotFoundError
from ..artifacts import (
    ARTIFACTS_LOAD_CAPABILITY,
    ARTIFACTS_SAVE_CAPABILITY,
    ArtifactStore,
    decode_artifact_b64,
)
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
        cli_policy: AmbientCliPolicy | None = None,
        result_store: ResultStore | None = None,
        artifact_store: ArtifactStore | None = None,
        audit_log: AuditLog | None = None,
    ) -> None:
        self.registry = registry
        # the policy object is shared with the runtime so escalation grants
        # made mid-dispatch are visible to later runs and to the manifest
        self._cli_policy = cli_policy or AmbientCliPolicy(
            ambient_cli_allowed_binaries
        )
        self._audit_log = audit_log or AuditLog()
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
        if not self._audit_log.enabled:
            return await self._dispatch_tool(name, normalized_params)
        started = self._audit_log.timer()
        # metadata only: the binary name is policy-relevant, the args and
        # results are not logged — payloads can carry secrets
        fields: dict[str, Any] = {"capability": name}
        if name == AMBIENT_CLI_CAPABILITY:
            fields["binary"] = str(normalized_params.get("binary", ""))
        try:
            value = await self._dispatch_tool(name, normalized_params)
        except BaseException as exc:
            self._audit_log.emit(
                "dispatch",
                duration_ms=self._audit_log.elapsed_ms(started),
                ok=False,
                error_type=type(exc).__name__,
                **fields,
            )
            raise
        self._audit_log.emit(
            "dispatch",
            duration_ms=self._audit_log.elapsed_ms(started),
            ok=True,
            **fields,
        )
        return value

    async def _dispatch_tool(
        self,
        name: str,
        normalized_params: dict[str, Any],
    ) -> Any:
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
        if name == AMBIENT_CLI_CAPABILITY:
            # async: an installed escalation handler may pause here to ask
            # the human before the policy refuses
            await self._cli_policy.ensure_allowed(
                str(normalized_params.get("binary", ""))
            )
        try:
            return await self.registry.call(name, normalized_params)
        except CapabilityNotFoundError:
            # CLI binaries are not registry capabilities; when the missed
            # name is an allowed binary the dead end must teach the flat
            # call (live #80 finding: a model tried call_tool('git', ...)
            # twice and concluded git was unavailable)
            effective = self._cli_policy.effective_allowlist()
            if effective and name in effective:
                raise CapabilityNotFoundError(
                    f"'{name}' is a CLI binary, not a capability — call it "
                    f"as a flat async function. {CLI_SHAPE_GUIDANCE}"
                ) from None
            raise

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

