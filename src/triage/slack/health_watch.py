"""Health alerts for Slack: an edge-triggered watcher plus the polling loop (Phase 6).

Why edge-triggered: a poller that posts "app is down" every 30 seconds trains people to
mute the channel. The watcher posts once when the app becomes unhealthy and once when
it recovers (with how long it was down). Failures of the *status check itself* are a
different signal ("we cannot see the app"), so they alert once after a few consecutive
failures instead of on the first blip.

The unhealthy predicate is ``AppStatus.is_healthy``, the same one the agent uses
(ADR 0002), so the poller and the agent can never disagree about "down".
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from triage.config import Settings
from triage.schemas.tools import AppStatus, AppStatusRequest
from triage.tools.base import ToolError, ToolSet

EventKind = Literal["alert", "recovered", "check_failed"]


@dataclass(frozen=True)
class HealthEvent:
    kind: EventKind
    status: AppStatus | None = None
    detail: str | None = None
    down_for_seconds: float | None = None
    consecutive_failures: int = 0


class HealthWatch:
    """Turns a stream of health observations into at most one message per change."""

    def __init__(self, *, failure_threshold: int = 3, clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self._clock = clock
        self.state: Literal["unknown", "healthy", "unhealthy"] = "unknown"
        self.unhealthy_since: float | None = None
        self.consecutive_failures = 0

    def observe(self, status: AppStatus | None, error: str | None = None) -> HealthEvent | None:
        """Feed one poll result. Pass ``status`` on success, or ``error`` if the check failed."""
        if status is None:
            self.consecutive_failures += 1
            if self.consecutive_failures == self.failure_threshold:
                return HealthEvent(
                    "check_failed",
                    detail=error or "unknown error",
                    consecutive_failures=self.consecutive_failures,
                )
            return None

        self.consecutive_failures = 0
        if not status.is_healthy:
            if self.state != "unhealthy":
                self.state = "unhealthy"
                self.unhealthy_since = self._clock()
                return HealthEvent("alert", status=status)
            return None

        if self.state == "unhealthy":
            down_for = self._clock() - self.unhealthy_since if self.unhealthy_since else None
            self.state, self.unhealthy_since = "healthy", None
            return HealthEvent("recovered", status=status, down_for_seconds=down_for)
        self.state = "healthy"  # first observation, or still healthy: nothing to say
        return None


def check_once(tools: ToolSet) -> tuple[AppStatus | None, str | None]:
    """One status check. Never raises: errors come back as text for the watcher."""
    try:
        return tools.check_mule_app_status(AppStatusRequest()), None
    except ToolError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 - a poller must survive a buggy tool
        return None, f"unexpected {type(exc).__name__}: {exc}"


def run_poller(
    *,
    get_settings: Callable[[], Settings],
    get_tools: Callable[[Settings], ToolSet],
    on_event: Callable[[HealthEvent], None],
    stop: threading.Event,
    watch: HealthWatch | None = None,
) -> None:
    """Poll until ``stop`` is set. Settings are re-read every tick so the mock demo's
    ``:app down`` switch (and any interval change) takes effect without a restart."""
    watch = watch or HealthWatch()
    while not stop.is_set():
        settings = get_settings()
        status, error = check_once(get_tools(settings))
        event = watch.observe(status, error)
        if event is not None:
            try:
                on_event(event)
            except Exception:  # noqa: BLE001 - a failed post must not kill the poller
                pass
        stop.wait(settings.health_poll_seconds)
