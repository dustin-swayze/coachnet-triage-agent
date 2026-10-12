"""Mock ``search_mule_logs``: identifier match, then expand to whole transactions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from triage.config import Settings
from triage.schemas.tools import LogEntry, LogLevel, LogSearchResult, SearchLogsRequest
from triage.tools.base import NotFoundError
from triage.tools.mock._fixtures import load_fixture

# Keys that exist only to make matching possible; the real log API has no such fields.
_TAG_KEYS = {"case_number", "external_id", "app_name", "minutes_ago", "offset_seconds"}


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class MockSearchMuleLogs:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        now = datetime.now(UTC)
        self._rows: list[dict[str, Any]] = load_fixture("logs.json", now)
        for row in self._rows:
            row["timestamp"] = now - timedelta(
                minutes=row["minutes_ago"], seconds=-row.get("offset_seconds", 0)
            )

    def __call__(self, request: SearchLogsRequest) -> LogSearchResult:
        start, end = _utc(request.start), _utc(request.end)
        rows = [
            r
            for r in self._rows
            if r["app_name"] == self._settings.mule_app_name and start <= r["timestamp"] <= end
        ]

        hits = [
            r
            for r in rows
            if (request.correlation_id and r["correlation_id"] == request.correlation_id)
            or (request.case_number and r.get("case_number") == request.case_number)
            or (request.external_id and r.get("external_id") == request.external_id)
        ]
        if not hits:
            raise NotFoundError("no log entries matched the given identifiers in the time window")

        # A matching line usually names one hop; the diagnosis needs the whole transaction.
        corr_ids = {r["correlation_id"] for r in hits if r["correlation_id"]}
        picked = [r for r in rows if r in hits or r["correlation_id"] in corr_ids]
        picked.sort(key=lambda r: r["timestamp"])  # stable: file order breaks ties

        truncated = len(picked) > request.max_entries
        picked = picked[: request.max_entries]
        return LogSearchResult(
            app_name=self._settings.mule_app_name,
            query=request,
            entries=[self._entry(r) for r in picked],
            truncated=truncated,
            correlation_ids=sorted({r["correlation_id"] for r in picked if r["correlation_id"]}),
            source="mock",
        )

    @staticmethod
    def _entry(row: dict[str, Any]) -> LogEntry:
        fields = {k: v for k, v in row.items() if k not in _TAG_KEYS}
        return LogEntry(**{**fields, "level": LogLevel(row["level"])})
