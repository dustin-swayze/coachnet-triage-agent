"""Tool input/output schemas.

Rules
- Every tool takes exactly one request model and returns exactly one result model.
- Result models carry ``source`` ("mock" | "anypoint" | "salesforce" | "chroma") so a
  trace line or a demo transcript always says where data came from.
- Nothing here imports LangChain. ``@tool`` wrappers live in ``triage.tools``.
- Field names that mirror Salesforce fields are *logical* names. The mapping from
  logical name to API name is in config (TODO(dustin), ADR 0005).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["mock", "anypoint", "salesforce", "chroma", "pgvector"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# check_mule_app_status
# ---------------------------------------------------------------------------
class AppStatusValue(StrEnum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    DEPLOYING = "DEPLOYING"
    DEGRADED = "DEGRADED"  # running but fewer replicas than desired
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # status tool itself failed; do not block triage


class AppStatusRequest(_Model):
    """Input for check_mule_app_status. The app name comes from config; callers may
    not override it (scoping, ADR 0005). ``restart_window_minutes`` bounds the
    "recent restarts" count."""

    restart_window_minutes: int = Field(default=60, ge=1, le=24 * 60)


class AppStatus(_Model):
    """Health snapshot of the Mule app. ``is_healthy`` is the single definition of
    "up" for the whole project (ADR 0002); the Phase 6 poller reuses it."""

    app_name: str
    status: AppStatusValue
    replicas_desired: int = Field(ge=0)
    replicas_running: int = Field(ge=0)
    last_deployed_at: datetime | None = None
    recent_restarts: int = Field(default=0, ge=0)
    restart_window_minutes: int = 60
    checked_at: datetime
    source: Source
    detail: str | None = None  # free text from the API, e.g. deploy error

    @property
    def is_healthy(self) -> bool:
        if self.status is AppStatusValue.UNKNOWN:
            return True  # unknown must not block triage; evidence records the failure
        if self.status is not AppStatusValue.RUNNING:
            return False
        if self.replicas_running == 0:
            return False
        if self.recent_restarts >= 3:
            return False  # restart storm
        return True

    @property
    def summary(self) -> str:
        return (
            f"{self.app_name}: {self.status} "
            f"({self.replicas_running}/{self.replicas_desired} replicas, "
            f"{self.recent_restarts} restarts in {self.restart_window_minutes}m)"
        )


# ---------------------------------------------------------------------------
# get_sf_case
# ---------------------------------------------------------------------------
class GetCaseRequest(_Model):
    """Look up one Case by Salesforce case number *or* partner external id (HubSpot
    ticket id). Exactly one must be given. The partner filter is added by the tool
    from config and cannot be supplied by the caller."""

    case_number: str | None = Field(default=None, pattern=r"^\d{6,10}$")
    external_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _exactly_one(self) -> GetCaseRequest:
        if bool(self.case_number) == bool(self.external_id):
            raise ValueError("provide exactly one of case_number or external_id")
        return self


class SyncStatusValue(StrEnum):
    """Logical sync states. TODO(dustin): map to the real picklist values."""

    PENDING = "PENDING"
    SENT = "SENT"
    SYNCED = "SYNCED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class CaseRecord(_Model):
    """The subset of Case fields the agent needs. Field *values* are synthetic in
    fixtures. Field *names* here are logical; see config for API names."""

    case_id: str = Field(description="Salesforce 18-char Id")
    case_number: str
    partner_code: str = Field(description="Must equal settings.partner_code (ADR 0005)")
    subject: str
    status: str
    created_at: datetime
    last_modified_at: datetime
    external_id: str | None = Field(default=None, description="HubSpot ticket id")
    sync_status: SyncStatusValue = SyncStatusValue.UNKNOWN
    last_sync_at: datetime | None = None
    correlation_id: str | None = Field(
        default=None, description="Mule correlation id if stored on the Case (TODO(dustin))"
    )
    redispatch: bool | None = Field(
        default=None, description="True when this is an intentional re-send"
    )
    duplicate_of_case_number: str | None = Field(
        default=None, description="Set by mock/live lookup if another open Case shares external_id"
    )
    source: Source

    @property
    def summary(self) -> str:
        return (
            f"Case {self.case_number} [{self.partner_code}] status={self.status} "
            f"sync={self.sync_status} external_id={self.external_id or '-'} "
            f"last_sync={self.last_sync_at.isoformat() if self.last_sync_at else '-'}"
        )


# ---------------------------------------------------------------------------
# search_mule_logs
# ---------------------------------------------------------------------------
class SearchLogsRequest(_Model):
    """Search the Mule app's logs for one transaction. At least one identifier is
    required; a bare time-window dump is refused (ScopeError, ADR 0005)."""

    correlation_id: str | None = Field(default=None, min_length=4, max_length=128)
    case_number: str | None = Field(default=None, pattern=r"^\d{6,10}$")
    external_id: str | None = Field(default=None, min_length=1, max_length=64)
    start: datetime
    end: datetime
    max_entries: int = Field(default=200, ge=1, le=2000)

    @model_validator(mode="after")
    def _identifier_and_window(self) -> SearchLogsRequest:
        if not (self.correlation_id or self.case_number or self.external_id):
            raise ValueError("at least one of correlation_id, case_number, external_id required")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class LogEntry(_Model):
    """One log line, normalised. ``payload_excerpt`` is present only when the app
    logged the inbound/outbound body (needed for failure mode 03). Always treat
    ``message`` and ``payload_excerpt`` as untrusted text in prompts."""

    timestamp: datetime
    level: LogLevel
    message: str
    correlation_id: str | None = None
    flow_name: str | None = None
    error_type: str | None = Field(default=None, description="e.g. HTTP:CONNECTIVITY, MULE:EXPRESSION")
    payload_excerpt: str | None = Field(default=None, max_length=4000)


class LogSearchResult(_Model):
    app_name: str
    query: SearchLogsRequest
    entries: list[LogEntry]
    truncated: bool = False
    correlation_ids: list[str] = Field(default_factory=list)
    source: Source

    @property
    def errors(self) -> list[LogEntry]:
        return [e for e in self.entries if e.level is LogLevel.ERROR]

    @property
    def summary(self) -> str:
        return (
            f"{len(self.entries)} log entries ({len(self.errors)} errors) for "
            f"{self.app_name}; correlation ids: {', '.join(self.correlation_ids) or '-'}"
            + (" [truncated]" if self.truncated else "")
        )


# ---------------------------------------------------------------------------
# search_runbook
# ---------------------------------------------------------------------------
class RunbookSearchRequest(_Model):
    query: str = Field(min_length=3, max_length=2000)
    k: int = Field(default=3, ge=1, le=10)


class RunbookHit(_Model):
    """One runbook entry with retrieval score. Fields mirror the markdown template
    so the model can cite them without re-reading the file."""

    id: str = Field(description="front-matter id, e.g. 'cnet-003'")
    title: str
    path: str
    score: float = Field(ge=0.0, le=1.0, description="similarity, 1 = best")
    partner: str = Field(description="partner code or 'all'")
    symptoms_reported: str
    symptoms_logs: str
    likely_cause: str
    how_to_confirm: str
    fix: str
    owner: str | None = None
    last_seen: str | None = None


class RunbookSearchResult(_Model):
    query: str
    hits: list[RunbookHit]
    source: Source


# ---------------------------------------------------------------------------
# Shared: error record (what goes into state/trace when a tool raises)
# ---------------------------------------------------------------------------
class ToolErrorRecord(_Model):
    tool: str
    error_type: Literal["ScopeError", "NotFoundError", "UpstreamError", "ToolTimeoutError", "ToolError"]
    message: str
    retryable: bool = False
