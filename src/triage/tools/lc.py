"""LangChain ``@tool`` adapters over the plain tools (constraint 8).

The plain callables in ``ToolSet`` are the portable asset. These wrappers only exist
so a chat model can be told the tool's name, description, and argument schema via
``bind_tools``. The graph executes tool calls itself through ``Nodes._call_tool`` so
that every call is traced, counted against the step cap, and timed out; it never calls
``.invoke`` on these wrappers.

An Agentforce or MCP port would wrap the same ``ToolSet`` callables with that
platform's tool declaration instead.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from triage.schemas.tools import RunbookSearchRequest
from triage.tools.base import ToolSet


def make_runbook_tool(tools: ToolSet) -> BaseTool:
    @tool("search_runbook", args_schema=RunbookSearchRequest)
    def search_runbook(query: str, k: int = 3) -> str:
        """Search the runbook of known integration failure modes. Pass a query built
        from the user's symptom and the most telling log errors (error types and
        messages). Returns the top-k entries with symptoms, likely cause, how to
        confirm, and fix."""
        result = tools.search_runbook(RunbookSearchRequest(query=query, k=k))
        return result.model_dump_json()

    return search_runbook
