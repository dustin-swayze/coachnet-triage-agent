"""Tools: interfaces + typed errors in ``base.py``; ``mock/`` and ``live/`` backends.

``get_tools(settings)`` is the only factory. Graph nodes receive a ``ToolSet`` and
never import a concrete backend (ADR 0003).
"""

from triage.tools.base import (
    NotFoundError,
    ScopeError,
    ToolError,
    ToolSet,
    ToolTimeoutError,
    UpstreamError,
    get_tools,
)

__all__ = [
    "NotFoundError",
    "ScopeError",
    "ToolError",
    "ToolSet",
    "ToolTimeoutError",
    "UpstreamError",
    "get_tools",
]
