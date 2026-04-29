"""Programmable tool surfaces for Python code-mode agents."""

from .backends import PyodideDenoBackend
from .bridges import (
    HttpCallbackBridge,
    InProcessBridge,
    ToolCallError,
    ToolCallRequest,
    ToolCallResponse,
)
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
    "HttpCallbackBridge",
    "InProcessBridge",
    "PyodideDenoBackend",
    "ToolCallError",
    "ToolCallRequest",
    "ToolCallResponse",
    "Toolplane",
    "ToolplaneError",
]
