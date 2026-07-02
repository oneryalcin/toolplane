"""Local environment checks for a configured Toolplane runtime."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Literal

from .backends import LocalUnsafeBackend, MontyBackend, PyodideDenoBackend
from .config import ToolplaneConfig

CheckStatus = Literal["ok", "warn", "fail"]

KNOWN_BACKENDS = frozenset(
    backend.name for backend in (LocalUnsafeBackend, MontyBackend, PyodideDenoBackend)
)


@dataclass(frozen=True)
class DoctorCheck:
    """One doctor check outcome."""

    name: str
    status: CheckStatus
    detail: str = ""


def run_doctor_checks(config: ToolplaneConfig) -> tuple[DoctorCheck, ...]:
    """Check local prerequisites for a validated config, without network calls."""
    checks: list[DoctorCheck] = [DoctorCheck(name="config", status="ok")]
    checks.append(_check_backend(config))
    checks.extend(_check_cli(config))
    checks.extend(_check_mcp_servers(config))
    return tuple(checks)


def format_doctor_checks(checks: tuple[DoctorCheck, ...]) -> str:
    lines = []
    for check in checks:
        line = f"{check.name}: {check.status}"
        if check.detail:
            line += f" ({check.detail})"
        lines.append(line)
    return "\n".join(lines) + "\n"


def doctor_exit_code(checks: tuple[DoctorCheck, ...]) -> int:
    return 1 if any(check.status == "fail" for check in checks) else 0


def _check_backend(config: ToolplaneConfig) -> DoctorCheck:
    backend = config.toolplane.default_backend
    if backend not in KNOWN_BACKENDS:
        return DoctorCheck(
            name=f"backend {backend}",
            status="fail",
            detail=f"unknown backend; known: {', '.join(sorted(KNOWN_BACKENDS))}",
        )
    if backend == PyodideDenoBackend.name and shutil.which("deno") is None:
        return DoctorCheck(
            name=f"backend {backend}",
            status="fail",
            detail="deno not found on PATH; install Deno to use pyodide-deno",
        )
    if backend == LocalUnsafeBackend.name:
        return DoctorCheck(
            name=f"backend {backend}",
            status="warn",
            detail="development only; serve mcp will require --unsafe",
        )
    return DoctorCheck(name=f"backend {backend}", status="ok")


def _check_cli(config: ToolplaneConfig) -> list[DoctorCheck]:
    mode = config.cli.mode
    if mode == "disabled":
        return [DoctorCheck(name="cli disabled", status="ok")]

    checks: list[DoctorCheck] = []
    if mode == "ambient":
        checks.append(
            DoctorCheck(
                name="cli ambient",
                status="warn",
                detail="development only; serve mcp will require --unsafe",
            )
        )
    else:
        for binary in config.cli.allow:
            if shutil.which(binary) is None:
                checks.append(
                    DoctorCheck(
                        name=f"cli allow {binary}",
                        status="fail",
                        detail="not found on PATH",
                    )
                )
            else:
                checks.append(DoctorCheck(name=f"cli allow {binary}", status="ok"))
    if config.toolplane.default_backend == MontyBackend.name:
        checks.append(
            DoctorCheck(
                name="cli namespace",
                status="warn",
                detail=(
                    "the monty backend does not expose the cli namespace; "
                    "CLI calls need local_unsafe or pyodide-deno"
                ),
            )
        )
    return checks


def _check_mcp_servers(config: ToolplaneConfig) -> list[DoctorCheck]:
    servers = config.mcp.servers
    if not servers:
        return [DoctorCheck(name="mcp servers", status="ok", detail="none configured")]
    names = ", ".join(sorted(servers))
    return [
        DoctorCheck(
            name="mcp servers",
            status="ok",
            detail=f"{names}; probe with: toolplane mcp status",
        )
    ]
