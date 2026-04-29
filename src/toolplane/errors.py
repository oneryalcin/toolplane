"""Toolplane exception types."""

from __future__ import annotations


class ToolplaneError(Exception):
    """Base class for toolplane errors."""


class CapabilityNotFoundError(ToolplaneError):
    """Raised when a requested capability does not exist."""


class DuplicateCapabilityError(ToolplaneError):
    """Raised when registering a duplicate capability name."""


class BackendNotFoundError(ToolplaneError):
    """Raised when a requested execution backend does not exist."""


class BackendCapabilityError(ToolplaneError):
    """Raised when a backend cannot satisfy requested execution options."""
