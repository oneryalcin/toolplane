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
from .config import (
    CliSettings,
    McpSettings,
    ResultsSettings,
    ToolplaneConfig,
    ToolplaneSettings,
    load_toolplane_config,
)
from .errors import (
    BackendCapabilityError,
    BackendNotFoundError,
    CapabilityNotFoundError,
    CliPolicyError,
    DuplicateCapabilityError,
    NamespaceCollisionError,
    ResultStoreError,
    ToolplaneError,
    UnsafeFacadeConfigError,
)
from .execution import BackendCapabilities, ExecutionError, ExecutionResult
from .mcp_facade import build_mcp_facade, build_mcp_facade_from_config
from .registry import CapabilityRegistry
from .results import ResultStore
from .runtime import Toolplane

__version__ = "0.4.0"

__all__ = [
    "__version__",
    "BackendCapabilities",
    "BackendCapabilityError",
    "BackendNotFoundError",
    "build_mcp_facade",
    "build_mcp_facade_from_config",
    "Capability",
    "CapabilityNotFoundError",
    "CapabilityRegistry",
    "CliPolicyError",
    "CliSettings",
    "DuplicateCapabilityError",
    "ExecutionError",
    "ExecutionResult",
    "HttpCallbackBridge",
    "InProcessBridge",
    "McpSettings",
    "NamespaceCollisionError",
    "PyodideDenoBackend",
    "ResultsSettings",
    "ResultStore",
    "ResultStoreError",
    "ToolCallError",
    "ToolCallRequest",
    "ToolCallResponse",
    "Toolplane",
    "ToolplaneConfig",
    "ToolplaneError",
    "ToolplaneSettings",
    "UnsafeFacadeConfigError",
    "load_toolplane_config",
]
