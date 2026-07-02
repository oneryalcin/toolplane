"""Toolplane exception types."""

from __future__ import annotations


class ToolplaneError(Exception):
    """Base class for toolplane errors."""


# The bridge-crossing errors below also subclass a builtin so snippets can
# catch them by a type that exists inside every sandbox: monty maps external
# exceptions to their nearest builtin base, local raises the real instance
# (caught via inheritance), and the pyodide bridge re-raises the builtin
# named in the RPC error. The same catch pattern works on all three
# backends; the toolplane-specific type only exists host-side.


class CapabilityNotFoundError(ToolplaneError, LookupError):
    """Raised when a requested capability does not exist."""


class DuplicateCapabilityError(ToolplaneError):
    """Raised when registering a duplicate capability name."""


class NamespaceCollisionError(ToolplaneError):
    """Raised when execution namespace construction would shadow a binding."""


class BackendNotFoundError(ToolplaneError):
    """Raised when a requested execution backend does not exist."""


class BackendCapabilityError(ToolplaneError):
    """Raised when a backend cannot satisfy requested execution options."""


class CliPolicyError(ToolplaneError, PermissionError):
    """Raised when code tries to use a CLI binary disallowed by policy."""


class ResultStoreError(ToolplaneError, ValueError):
    """Raised when the result store rejects a save or load."""


class UnsafeFacadeConfigError(ToolplaneError):
    """Raised when an MCP facade config exposes unsafe defaults."""
