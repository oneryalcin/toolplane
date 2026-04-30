"""Capability adapters for external tool sources."""

from .cli_to_py import CliCommandResult, register_cli
from .mcp import register_mcp_config, register_mcp_server
from .python import register_python_namespace

__all__ = [
    "CliCommandResult",
    "register_cli",
    "register_mcp_config",
    "register_mcp_server",
    "register_python_namespace",
]
