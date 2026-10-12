"""Mock ``check_mule_app_status``: one canned snapshot per ``settings.mock_app_state``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from triage.config import Settings
from triage.schemas.tools import AppStatus, AppStatusRequest, AppStatusValue
from triage.tools.base import ToolError, UpstreamError
from triage.tools.mock._fixtures import load_fixture


class MockCheckMuleAppStatus:
    """Returns the fixture for the configured state. The app name always comes from
    settings, mirroring the live tool where callers cannot choose the app (ADR 0005)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._states: dict[str, dict[str, Any]] = load_fixture("app_status.json")

    def __call__(self, request: AppStatusRequest) -> AppStatus:
        state = self._settings.mock_app_state
        spec = self._states.get(state)
        if spec is None:
            known = ", ".join(sorted(self._states))
            raise ToolError(f"unknown MOCK_APP_STATE {state!r}; expected one of: {known}")
        if "raises" in spec:  # simulates the status API itself failing (graph must degrade)
            err = spec["raises"]
            raise UpstreamError(
                err["message"], status_code=err["status_code"], body_excerpt=err["message"]
            )

        now = datetime.now(UTC)
        return AppStatus(
            app_name=self._settings.mule_app_name,
            status=AppStatusValue(spec["status"]),
            replicas_desired=spec["replicas_desired"],
            replicas_running=spec["replicas_running"],
            last_deployed_at=now - timedelta(minutes=spec["last_deployed_minutes_ago"]),
            recent_restarts=spec["recent_restarts"],
            restart_window_minutes=request.restart_window_minutes,
            checked_at=now,
            source="mock",
            detail=spec.get("detail"),
        )
