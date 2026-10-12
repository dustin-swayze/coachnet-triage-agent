"""Tool interfaces and the typed error hierarchy (ADR 0003, ADR 0005).

Each tool is a ``Protocol`` with one method. Mock and live classes implement the
same Protocol. ``ToolSet`` bundles the four v1 tools and is what the graph receives.

Read-only is a *property of the implementations*: no method here can express a write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from triage.config import Settings, TriageMode
from triage.schemas.tools import (
    AppStatus,
    AppStatusRequest,
    CaseRecord,
    GetCaseRequest,
    LogSearchResult,
    RunbookSearchRequest,
    RunbookSearchResult,
    SearchLogsRequest,
)


# ---------------------------------------------------------------------------
# Errors. Tools raise; the graph layer catches, traces, and continues.
# ---------------------------------------------------------------------------
class ToolError(Exception):
    """Base for all tool failures. ``retryable`` hints the graph; v1 does not retry."""

    retryable: bool = False

    def __init__(self, message: str, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable


class ScopeError(ToolError):
    """Scoping to the configured partner / app could not be proven. Fail closed."""


class NotFoundError(ToolError):
    """No record or no log lines matched. Not an upstream failure."""


class UpstreamError(ToolError):
    """The remote API failed. Carry an HTTP status and a short, redacted body excerpt."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body_excerpt: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message, retryable=retryable)
        self.status_code = status_code
        self.body_excerpt = (body_excerpt or "")[:500]


class ToolTimeoutError(ToolError):
    """Per-tool timeout (settings.tool_timeout_seconds) was hit."""

    retryable = True


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------
@runtime_checkable
class CheckMuleAppStatus(Protocol):
    """Health of the configured Mule app (ADR 0002).

    Scoping: app name is taken from settings; request cannot override it.
    Raises: UpstreamError, ToolTimeoutError. Never raises for "app is down" -- that is
    a normal result with ``is_healthy == False``.
    """

    def __call__(self, request: AppStatusRequest) -> AppStatus: ...


@runtime_checkable
class GetSfCase(Protocol):
    """Read one Case scoped to the configured partner (ADR 0005).

    The implementation MUST add ``<partner_field> = :partner_code`` to the query and
    MUST re-check the returned record's partner field. Either failing -> ScopeError.
    Raises: ScopeError, NotFoundError, UpstreamError, ToolTimeoutError.
    """

    def __call__(self, request: GetCaseRequest) -> CaseRecord: ...


@runtime_checkable
class SearchMuleLogs(Protocol):
    """Search the configured Mule app's logs for one transaction.

    Scoping: app name from settings; at least one identifier is enforced by the
    request schema. A bare window dump is impossible by construction.
    Raises: ScopeError, NotFoundError, UpstreamError, ToolTimeoutError.
    """

    def __call__(self, request: SearchLogsRequest) -> LogSearchResult: ...


@runtime_checkable
class SearchRunbook(Protocol):
    """Top-k runbook entries for a symptom/error description (ADR 0004).

    Scoping: hits filtered to ``partner in {settings.partner_code, "all"}``.
    Raises: UpstreamError (index missing/corrupt). An empty hit list is a normal result.
    """

    def __call__(self, request: RunbookSearchRequest) -> RunbookSearchResult: ...


@dataclass(frozen=True)
class ToolSet:
    """The four v1 tools, injected into graph nodes."""

    check_mule_app_status: CheckMuleAppStatus
    get_sf_case: GetSfCase
    search_mule_logs: SearchMuleLogs
    search_runbook: SearchRunbook


def get_tools(settings: Settings) -> ToolSet:
    """Factory: pick the backend from ``settings.triage_mode``.

    Imports are local so the mock path never loads live dependencies (and vice versa).
    """
    if settings.triage_mode is TriageMode.MOCK:
        from triage.tools.mock import build_mock_tools  # noqa: PLC0415

        return build_mock_tools(settings)
    from triage.tools.live import build_live_tools  # noqa: PLC0415

    return build_live_tools(settings)
