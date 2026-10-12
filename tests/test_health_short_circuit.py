"""Health check runs first and short-circuits the graph when the app is unhealthy (ADR 0002)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from helpers import make_llm, run, tool_names

from triage.config import Settings
from triage.schemas.tools import AppStatusRequest, AppStatusValue
from triage.tools.base import UpstreamError
from triage.tools.mock import build_mock_tools

UNHEALTHY_STATES = ["down", "deploying", "restart_loop"]


@pytest.mark.parametrize("state", UNHEALTHY_STATES)
def test_unhealthy_app_alerts_without_touching_case_or_model(settings: Settings, state: str):
    """Unhealthy app -> ALERT after exactly one tool call, no case lookup, no LLM call."""
    s = settings.model_copy(update={"mock_app_state": state})
    llm = make_llm()
    result = run("Case 00412345 didn't sync", s, llm=llm)

    assert result.response.startswith("ALERT")
    assert result.state["steps_used"] == 1
    assert tool_names(result) == ["check_mule_app_status"]
    assert result.state["case"] is None
    assert result.state["diagnosis"] is None
    assert llm.calls == []


@pytest.mark.parametrize("state", UNHEALTHY_STATES)
def test_alert_points_at_the_outage_runbook(settings: Settings, state: str):
    """The alert names runbook cnet-007 so a human knows where to look next."""
    s = settings.model_copy(update={"mock_app_state": state})
    result = run("Case 00412345 didn't sync", s)
    assert "cnet-007" in result.response
    assert not result.state["incomplete"]


def test_status_error_degrades_to_unknown_and_triage_continues(settings: Settings):
    """A failing status API must not block triage: UNKNOWN status, error kept, case looked up."""
    s = settings.model_copy(update={"mock_app_state": "status_error"})
    tools = build_mock_tools(s)
    calls: list[str] = []
    real_get_case = tools.get_sf_case

    def spy(request):
        calls.append(request.case_number or "")
        return real_get_case(request)

    result = run("Case 00412345 didn't sync", s, tools=replace(tools, get_sf_case=spy))

    status = result.state["app_status"]
    assert status is not None and status.status is AppStatusValue.UNKNOWN
    errors = result.state["errors"]
    assert any(
        e.tool == "check_mule_app_status" and e.error_type == "UpstreamError" for e in errors
    )
    assert calls == ["00412345"]  # get_sf_case WAS called
    assert not result.response.startswith("ALERT")
    assert "ALERT" not in result.response
    assert "Diagnosis:" in result.response
    assert result.state["diagnosis"] is not None


def test_status_error_tool_really_raises(settings: Settings):
    """Guards the fixture assumption behind the previous test: the mock raises UpstreamError."""
    s = settings.model_copy(update={"mock_app_state": "status_error"})
    with pytest.raises(UpstreamError):
        build_mock_tools(s).check_mule_app_status(AppStatusRequest())


def test_health_only_question_answers_from_one_step(settings: Settings):
    """Healthy app + 'is it up?' -> healthy answer from one tool call, no case lookup."""
    result = run("Is the Coach Net app up?", settings)

    assert "healthy" in result.response
    assert result.state["steps_used"] == 1
    assert tool_names(result) == ["check_mule_app_status"]
    assert result.state["case"] is None
    assert result.state["diagnosis"] is None
    assert not result.state["incomplete"]


def test_unknown_status_health_question_says_unconfirmed(settings: Settings):
    """If the status check itself fails, a health-only answer must say it is not confirmed."""
    s = settings.model_copy(update={"mock_app_state": "status_error"})
    result = run("Is the Coach Net app up?", s)
    assert "not confirmed" in result.response
