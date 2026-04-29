"""Programmable tool surfaces for Python code-mode agents."""

from .capabilities import Capability
from .errors import (
    BackendCapabilityError,
    BackendNotFoundError,
    CapabilityNotFoundError,
    DuplicateCapabilityError,
    ToolplaneError,
)
from .execution import BackendCapabilities, ExecutionError, ExecutionResult
from .registry import CapabilityRegistry
from .runtime import Toolplane

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "BackendCapabilities",
    "BackendCapabilityError",
    "BackendNotFoundError",
    "Capability",
    "CapabilityNotFoundError",
    "CapabilityRegistry",
    "DuplicateCapabilityError",
    "ExecutionError",
    "ExecutionResult",
    "Toolplane",
    "ToolplaneError",
]
