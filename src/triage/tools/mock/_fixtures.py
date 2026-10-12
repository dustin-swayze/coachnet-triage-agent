"""Fixture loading shared by the mock tools.

Fixtures store time as ``{"minutes_ago": N}`` so a "last 24h" search window always
matches no matter when the demo runs. The loader resolves them to absolute UTC
datetimes once, when a mock tool is constructed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _resolve(value: Any, now: datetime) -> Any:
    """Recursively replace ``{"minutes_ago": N}`` with a datetime."""
    if isinstance(value, dict):
        if set(value) == {"minutes_ago"}:
            return now - timedelta(minutes=value["minutes_ago"])
        return {k: _resolve(v, now) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, now) for v in value]
    return value


def load_fixture(name: str, now: datetime | None = None) -> Any:
    """Load ``fixtures/<name>`` and resolve relative times against ``now`` (default: utcnow)."""
    now = now or datetime.now(UTC)
    raw = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return _resolve(raw, now)
