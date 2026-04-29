"""Capability adapters for external tool sources."""

from .cli_to_py import CliCommandResult, register_cli

__all__ = ["CliCommandResult", "register_cli"]
