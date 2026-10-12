"""Failure handling: tool and model errors become evidence, never exceptions (ADR 0001)."""

from __future__ import annotations

from dataclasses import replace

from helpers import make_llm, run, tool_names
from langchain_core.messages import ToolMessage

from triage.config import Settings
from triage.llm import ai_tool_call
from triage.schemas.state import RequestIntent
from triage.tools.mock import build_mock_tools


def _errors_for(result, tool: str):
    return [e for e in result.state["errors"] if e.tool == tool]


# ---------------------------------------------------------------------------
# tool failures
# ---------------------------------------------------------------------------


def test_upstream_error_is_recorded_as_retryable_evidence(settings: Settings):
    """Salesforce 503 -> retryable UpstreamError in state and evidence; run ends cleanly."""
    result = run("Case 00466666 didn't sync", settings)

    (err,) = _errors_for(result, "get_sf_case")
    assert err.error_type == "UpstreamError"
    assert err.retryable is True
    assert "503" in err.message
    assert any(
        e.source_tool == "get_sf_case" and "UpstreamError" in e.summary
        for e in result.state["evidence"]
    )
    assert result.state["case"] is None
    assert result.response  # a response was produced, no exception escaped
    assert "UpstreamError" in result.response  # the failure is visible to the reader


def test_slow_tool_is_cut_off_by_tool_timeout(settings: Settings):
    """A hanging lookup is abandoned at tool_timeout_seconds and recorded as ToolTimeoutError."""
    s = settings.model_copy(update={"tool_timeout_seconds": 0.3})
    result = run("Case 00477777 didn't sync", s)

    (err,) = _errors_for(result, "get_sf_case")
    assert err.error_type == "ToolTimeoutError"
    assert err.retryable is True
    assert result.elapsed_s < 3
    assert result.response


def test_unexpected_exception_in_a_tool_is_recorded_as_tool_error(settings: Settings):
    """A bug (plain RuntimeError) in a tool must not crash the run."""
    tools = build_mock_tools(settings)

    def buggy(request):
        raise RuntimeError("kaboom")

    result = run("Case 00412345 didn't sync", settings, tools=replace(tools, get_sf_case=buggy))

    (err,) = _errors_for(result, "get_sf_case")
    assert err.error_type == "ToolError"
    assert "kaboom" in err.message
    assert err.retryable is False
    # The run carried on: logs were still searched using the identifiers from the user.
    assert "search_mule_logs" in tool_names(result)
    assert result.response


def test_tool_error_is_traced_in_the_tool_call_event(settings: Settings):
    """The trace line for a failed tool carries the error type and message."""
    result = run("Case 00466666 didn't sync", settings)
    call = next(e for e in result.tracer.tool_calls() if e["tool"] == "get_sf_case")
    assert call["error"]["error_type"] == "UpstreamError"


def test_missing_runbook_index_is_reported_not_raised(settings: Settings, tmp_path):
    """No index on disk -> search_runbook UpstreamError recorded; a response still comes back."""
    s = settings.model_copy(update={"index_dir": str(tmp_path / "does-not-exist")})
    result = run("Case 00412345 looks like a duplicate", s)

    (err,) = _errors_for(result, "search_runbook")
    assert err.error_type == "UpstreamError"
    assert "index not built" in err.message
    assert result.response
    diag = result.state["diagnosis"]
    assert diag is not None and diag.matched_runbook_ids == []
    assert any(
        e.source_tool == "search_runbook" and "failed" in e.summary
        for e in result.state["evidence"]
    )


# ---------------------------------------------------------------------------
# model failures
# ---------------------------------------------------------------------------


def test_parse_failure_falls_back_to_regex(settings: Settings):
    """If the parse model errors, regex extraction takes over and triage still succeeds."""

    def boom(messages, kwargs):
        raise RuntimeError("provider down")

    result = run("Case 00412345 looks like a duplicate", settings, llm=make_llm(parse_request=boom))

    (err,) = _errors_for(result, "llm:parse_request")
    assert "provider down" in err.message
    assert result.state["parsed"].case_number == "00412345"
    diag = result.state["diagnosis"]
    assert diag is not None
    assert diag.matched_runbook_ids[0] == "cnet-001"


def test_diagnose_model_failure_returns_incomplete_partial_result(settings: Settings):
    """If the diagnose model errors, the run ends INCOMPLETE and still shows gathered evidence."""

    def boom(messages, kwargs):
        raise RuntimeError("provider down")

    result = run("Case 00412345 didn't sync", settings, llm=make_llm(diagnose=boom))

    assert result.state["incomplete"] is True
    assert "diagnosis step failed" in result.state["incomplete_reason"]
    assert result.state["diagnosis"] is None
    assert "INCOMPLETE" in result.response
    assert "[get_sf_case]" in result.response


def test_invalid_diagnosis_from_model_is_rejected(settings: Settings):
    """A Diagnosis that fails schema validation (confidence 7) is not trusted."""
    bad = ai_tool_call(
        "Diagnosis",
        {
            "summary": "s",
            "likely_cause": "c",
            "suggested_fix": "f",
            "confidence": 7,
        },
    )
    result = run("Case 00412345 didn't sync", settings, llm=make_llm(diagnose=bad))

    assert result.state["diagnosis"] is None
    assert result.state["incomplete"] is True
    assert "invalid diagnosis" in result.state["incomplete_reason"]
    assert _errors_for(result, "llm:diagnose")


# ---------------------------------------------------------------------------
# hallucination guards
# ---------------------------------------------------------------------------


def test_parse_guard_drops_invented_identifier_and_recovers_real_one(settings: Settings):
    """A case number the model invented (absent from the text) is never looked up. The
    real number in the text is recovered by regex, so the run still triages it."""
    invented = ai_tool_call(
        "ParsedRequest",
        {"intent": "CASE_TRIAGE", "case_number": "00412999", "symptom": "case 00412345"},
    )
    result = run("case 00412345", settings, llm=make_llm(parse_request=invented))

    parsed = result.state["parsed"]
    assert parsed.case_number == "00412345"
    assert parsed.intent is RequestIntent.CASE_TRIAGE
    assert result.state["case"].case_number == "00412345"
    assert "00412999" not in str([e.get("inputs") for e in result.tracer.tool_calls()])
    assert any(e["event"] == "guard" for e in result.tracer.events)


def test_parse_guard_drops_invented_identifier_with_no_real_one(settings: Settings):
    """With no identifier in the text at all, an invented one leaves nothing to look up."""
    invented = ai_tool_call(
        "ParsedRequest",
        {"intent": "CASE_TRIAGE", "case_number": "00412999", "symptom": "it broke"},
    )
    result = run("it broke", settings, llm=make_llm(parse_request=invented))

    assert result.state["parsed"].case_number is None
    assert result.state["parsed"].intent is RequestIntent.UNCLEAR
    assert "get_sf_case" not in tool_names(result)
    assert "I need a case to look at" in result.response


def test_parse_guard_keeps_identifier_present_in_user_text(settings: Settings):
    """Control: an identifier that does appear verbatim survives the guard."""
    ok = ai_tool_call(
        "ParsedRequest",
        {"intent": "UNCLEAR", "case_number": "00412345", "symptom": "case 00412345"},
    )
    result = run("case 00412345", settings, llm=make_llm(parse_request=ok))
    parsed = result.state["parsed"]
    assert parsed.case_number == "00412345"
    assert parsed.intent is RequestIntent.CASE_TRIAGE  # a reference upgrades the intent


def test_diagnosis_guard_drops_runbook_ids_never_retrieved(settings: Settings):
    """Citing an unretrieved runbook id drops that id and caps confidence at 0.5."""

    def cites_unretrieved(messages, kwargs):
        if not any(isinstance(m, ToolMessage) for m in messages):
            return ai_tool_call(
                "search_runbook",
                {"query": "duplicate case same HubSpot ticket redispatch", "k": 3},
            )
        return ai_tool_call(
            "Diagnosis",
            {
                "summary": "Duplicate case.",
                "likely_cause": "Re-sent ticket.",
                "matched_runbook_ids": ["cnet-001", "cnet-999"],
                "suggested_fix": "Close the duplicate.",
                "confidence": 0.9,
                "evidence_refs": ["get_sf_case"],
            },
        )

    result = run(
        "Case 00412345 looks like a duplicate",
        settings,
        llm=make_llm(diagnose=cites_unretrieved),
    )

    diag = result.state["diagnosis"]
    assert diag is not None
    assert diag.matched_runbook_ids == ["cnet-001"]
    assert "cnet-999" not in diag.matched_runbook_ids
    assert diag.confidence <= 0.5
    assert "cnet-999" not in result.response
    guard = [e for e in result.tracer.events if e["event"] == "guard"]
    assert guard and guard[0]["dropped_runbook_ids"] == ["cnet-999"]


def test_diagnosis_guard_leaves_honest_citations_alone(settings: Settings):
    """Control: citing only retrieved ids keeps both the ids and the model's confidence."""

    def honest(messages, kwargs):
        if not any(isinstance(m, ToolMessage) for m in messages):
            return ai_tool_call(
                "search_runbook",
                {"query": "duplicate case same HubSpot ticket redispatch", "k": 3},
            )
        return ai_tool_call(
            "Diagnosis",
            {
                "summary": "Duplicate case.",
                "likely_cause": "Re-sent ticket.",
                "matched_runbook_ids": ["cnet-001"],
                "suggested_fix": "Close the duplicate.",
                "confidence": 0.9,
            },
        )

    result = run("Case 00412345 looks like a duplicate", settings, llm=make_llm(diagnose=honest))
    diag = result.state["diagnosis"]
    assert diag.matched_runbook_ids == ["cnet-001"]
    assert diag.confidence == 0.9
