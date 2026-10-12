"""Bounded agent (constraint 5): step cap and time budget end the run with partial results."""

from __future__ import annotations

import time
from dataclasses import replace

from helpers import make_llm, run, tool_names

from triage.config import Settings
from triage.llm import ai_tool_call
from triage.tools.base import ToolSet
from triage.tools.mock import build_mock_tools


def _always_search(messages, kwargs):
    """A model that never converges: asks for another runbook search every time."""
    return ai_tool_call("search_runbook", {"query": "rate limit 429", "k": 3})


def test_runaway_model_is_stopped_by_step_cap(settings: Settings):
    """A looping model cannot exceed step_cap tool calls; run ends INCOMPLETE, no diagnosis."""
    s = settings.model_copy(update={"step_cap": 5})
    llm = make_llm(diagnose=_always_search)

    result = run("Case 00412345 didn't sync", s, llm=llm)

    state = result.state
    assert state["incomplete"] is True
    assert "step cap" in state["incomplete_reason"]
    assert "INCOMPLETE" in result.response
    assert state["steps_used"] == 5
    assert len(result.tracer.tool_calls()) == 5
    # health + case + logs, then exactly two runbook searches fit under the cap
    assert tool_names(result) == [
        "check_mule_app_status",
        "get_sf_case",
        "search_mule_logs",
        "search_runbook",
        "search_runbook",
    ]
    assert any(e["event"] == "limit_hit" for e in result.tracer.events)
    assert state["diagnosis"] is None
    assert result.elapsed_s < 5


def test_step_cap_of_two_stops_before_log_search(settings: Settings):
    """With cap=2 the case lookup uses the last step; the log search is never executed."""
    s = settings.model_copy(update={"step_cap": 2})
    tools = build_mock_tools(s)
    log_calls: list[object] = []
    real_logs = tools.search_mule_logs

    def spy(request):
        log_calls.append(request)
        return real_logs(request)

    result = run("Case 00412345 didn't sync", s, tools=replace(tools, search_mule_logs=spy))

    assert log_calls == []
    assert tool_names(result) == ["check_mule_app_status", "get_sf_case"]
    assert result.state["incomplete"] is True
    assert "step cap" in result.state["incomplete_reason"]
    assert result.state["steps_used"] == 2
    assert result.state["case"] is not None  # partial result is kept
    assert "INCOMPLETE" in result.response
    assert result.state["diagnosis"] is None


def test_limit_hit_event_names_the_limit(settings: Settings):
    """The trace records which limit fired and what it blocked (auditability)."""
    s = settings.model_copy(update={"step_cap": 2})
    result = run("Case 00412345 didn't sync", s)
    hits = [e for e in result.tracer.events if e["event"] == "limit_hit"]
    assert len(hits) == 1
    assert hits[0]["limit"] == "step_cap"
    assert hits[0]["before"] == "search_mule_logs"


def test_time_budget_turns_a_slow_tool_into_a_timeout(settings: Settings):
    """A tool slower than the remaining run budget is abandoned with ToolTimeoutError."""
    s = settings.model_copy(update={"run_budget_seconds": 0.5, "tool_timeout_seconds": 5.0})
    tools = build_mock_tools(s)
    real_get_case = tools.get_sf_case

    def slow_get_case(request):
        time.sleep(0.6)
        return real_get_case(request)

    slow_tools: ToolSet = replace(tools, get_sf_case=slow_get_case)
    result = run("Case 00412345 didn't sync", s, tools=slow_tools)

    case_errors = [e for e in result.state["errors"] if e.tool == "get_sf_case"]
    assert [e.error_type for e in case_errors] == ["ToolTimeoutError"]
    assert case_errors[0].retryable is True
    # Either flagged incomplete (budget gone before logs) or the nothing-found answer.
    assert result.state["incomplete"] or "found nothing" in result.response
    assert result.state["diagnosis"] is None
    assert result.elapsed_s < 3


def test_time_budget_exhaustion_is_reported_as_limit(settings: Settings):
    """After the budget is spent the next tool is not called and the reason says time budget."""
    s = settings.model_copy(update={"run_budget_seconds": 0.5, "tool_timeout_seconds": 5.0})
    tools = build_mock_tools(s)
    real_get_case = tools.get_sf_case

    def slow_get_case(request):
        time.sleep(0.6)
        return real_get_case(request)

    result = run("Case 00412345 didn't sync", s, tools=replace(tools, get_sf_case=slow_get_case))
    assert result.state["incomplete"] is True
    assert "time budget" in result.state["incomplete_reason"]
    assert "search_mule_logs" not in tool_names(result)
