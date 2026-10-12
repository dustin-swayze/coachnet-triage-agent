"""Plain helpers shared by the test modules (fixtures live in conftest.py)."""

from __future__ import annotations

from typing import Any

from triage.config import Settings
from triage.graph.build import RunResult, run_triage
from triage.llm import ScriptedChatModel, default_mock_responders
from triage.tools.base import ToolSet
from triage.tools.mock import build_mock_tools


def make_llm(**overrides: Any) -> ScriptedChatModel:
    """Scripted model with the default responders, optionally overriding some nodes."""
    return ScriptedChatModel(responders={**default_mock_responders(), **overrides})


def run(
    question: str,
    settings: Settings,
    tools: ToolSet | None = None,
    llm: ScriptedChatModel | None = None,
) -> RunResult:
    """One end-to-end triage run with mock tools and the default scripted model."""
    return run_triage(
        question,
        settings,
        tools=tools if tools is not None else build_mock_tools(settings),
        llm=llm if llm is not None else make_llm(),
    )


def tool_names(result: RunResult) -> list[str]:
    """Names of the tools called, in order, as recorded in the trace."""
    return [e["tool"] for e in result.tracer.tool_calls()]


def event_kinds(result: RunResult) -> list[str]:
    return [e["event"] for e in result.tracer.events]
