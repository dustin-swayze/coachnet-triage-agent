"""Graph state for the triage run.

Why a TypedDict for the state and Pydantic for the values
- LangGraph merges node outputs into state using per-key reducers declared with
  ``Annotated``. That idiom is TypedDict-first. The *values* are Pydantic models, so
  each piece of evidence is still validated and serialisable.
- Limits (``steps_used``, ``deadline``, ``incomplete``) live in state so the
  ``respond`` node can report partial results instead of the graph raising (ADR 0001).
"""

from __future__ import annotations

import operator
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from triage.schemas.tools import (
    AppStatus,
    CaseRecord,
    LogSearchResult,
    RunbookHit,
    ToolErrorRecord,
)


class RequestIntent(StrEnum):
    CASE_TRIAGE = "CASE_TRIAGE"  # "case 123 didn't sync"
    HEALTH_ONLY = "HEALTH_ONLY"  # "is the mule app up?"
    UNCLEAR = "UNCLEAR"  # no case ref and not a health question -> ask


class ParsedRequest(BaseModel):
    """Output of the parse_request node (LLM structured output)."""

    model_config = ConfigDict(extra="forbid")

    intent: RequestIntent
    case_number: str | None = Field(default=None, pattern=r"^\d{6,10}$")
    external_id: str | None = None
    correlation_id: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    symptom: str = Field(default="", description="user's description, verbatim-ish")

    @property
    def has_case_reference(self) -> bool:
        return bool(self.case_number or self.external_id or self.correlation_id)


class Evidence(BaseModel):
    """One fact the agent gathered, with provenance. The respond node lists these so
    the answer is auditable against the trace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_tool: str
    summary: str
    at: datetime


class Diagnosis(BaseModel):
    """Output of the diagnose node (LLM structured output)."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="one or two sentences, plain English")
    likely_cause: str
    matched_runbook_ids: list[str] = Field(default_factory=list)
    suggested_fix: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(
        default_factory=list, description="source_tool names the diagnosis relies on"
    )
    needs_human: bool = Field(default=False, description="true when the fix is a write action")


class TriageState(TypedDict, total=False):
    # --- input / identity ---
    run_id: str
    user_request: str
    started_at: datetime
    deadline: datetime
    step_cap: int

    # --- gathered along the way ---
    app_status: AppStatus | None
    parsed: ParsedRequest | None
    case: CaseRecord | None
    logs: LogSearchResult | None
    runbook_hits: Annotated[list[RunbookHit], operator.add]
    evidence: Annotated[list[Evidence], operator.add]
    errors: Annotated[list[ToolErrorRecord], operator.add]

    # --- diagnose loop (LangChain messages; reducer added in graph/build.py) ---
    messages: Annotated[list[Any], operator.add]

    # --- limits ---
    steps_used: int
    incomplete: bool
    incomplete_reason: str | None

    # --- output ---
    diagnosis: Diagnosis | None
    final_response: str | None


def initial_state(user_request: str, *, step_cap: int, run_budget_seconds: float) -> TriageState:
    now = datetime.now(UTC)
    return TriageState(
        run_id=uuid4().hex[:12],
        user_request=user_request,
        started_at=now,
        deadline=now + timedelta(seconds=run_budget_seconds),
        step_cap=step_cap,
        app_status=None,
        parsed=None,
        case=None,
        logs=None,
        runbook_hits=[],
        evidence=[],
        errors=[],
        messages=[],
        steps_used=0,
        incomplete=False,
        incomplete_reason=None,
        diagnosis=None,
        final_response=None,
    )
