"""Toolplane exception types."""

from __future__ import annotations


class ToolplaneError(Exception):
    """Base class for toolplane errors."""


class CapabilityNotFoundError(ToolplaneError):
    """Raised when a requested capability does not exist."""


class DuplicateCapabilityError(ToolplaneError):
    """Raised when registering a duplicate capability name."""


class NamespaceCollisionError(ToolplaneError):
    """Raised when execution namespace construction would shadow a binding."""


class BackendNotFoundError(ToolplaneError):
    """Raised when a requested execution backend does not exist."""


class BackendCapabilityError(ToolplaneError):
    """Raised when a backend cannot satisfy requested execution options."""


class CliPolicyError(ToolplaneError):
    """Raised when code tries to use a CLI binary disallowed by policy."""
