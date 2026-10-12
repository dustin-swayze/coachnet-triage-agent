"""JSONL run tracer with redaction (constraint 6).

One file per run: ``traces/<run_id>.jsonl``. One line per event, for example::

    {"ts": "...", "run_id": "...", "event": "tool_call", "tool": "get_sf_case",
     "inputs": {...redacted...}, "output_summary": "...", "latency_ms": 123,
     "error": null | {"type": "ScopeError", "message": "..."}}

Events: ``run_start``, ``node_enter``, ``node_exit``, ``tool_call``, ``llm_call``,
``limit_hit``, ``run_end``. Prompt bodies are never written in v1: the trace answers
"what did the agent do", not "what did it read", which keeps log text and case data
out of a file that is easy to share.

Why redact at write time instead of at read time: a trace file is the artifact most
likely to be pasted into Slack or attached to a ticket. Anything that reaches disk
must already be safe.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretStr

REDACTED = "***"
MAX_STRING = 500

# Keys whose values are always secret, whatever they contain.
_SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|cookie|session[_-]?id|"
    r"client[_-]?secret|private[_-]?key)",
    re.IGNORECASE,
)

# Values that look like secrets even under an innocent key (e.g. inside a log message).
_SECRET_VALUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),  # Anthropic / OpenAI style keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),  # Authorization: Bearer ...
    re.compile(r"\b00D[A-Za-z0-9]{12,15}![A-Za-z0-9._]{20,}"),  # Salesforce session id
    re.compile(
        r"(?i)(access_token|refresh_token|client_secret|password)[\"']?\s*[:=]\s*[\"']?[^\s,\"'}]+"
    ),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
]

# Case fields treated as PII. TODO(dustin): confirm the real list from the API contract
# (customer name, phone, email, location are the usual suspects on a dispatch case).
PII_KEYS: frozenset[str] = frozenset(
    {
        "contact_name",
        "contact_phone",
        "contact_email",
        "customer_name",
        "phone",
        "email",
        "address",
        "location",
        "vin",
    }
)


def _redact_str(s: str, max_string: int = MAX_STRING) -> str:
    for pat in _SECRET_VALUE_PATTERNS:
        s = pat.sub(REDACTED, s)
    if len(s) > max_string:
        s = s[:max_string] + f"...[+{len(s) - max_string} chars]"
    return s


def redact(value: Any, *, max_string: int = MAX_STRING, _key: str | None = None) -> Any:
    """Return a JSON-safe, redacted copy of ``value``. Pure; never mutates input.

    Rules: secret-looking keys and PII keys -> ``***``; secret-looking substrings in
    any string -> ``***``; strings truncated to ``max_string`` chars; ``SecretStr`` never
    rendered; Pydantic models are dumped first, then redacted.
    """
    if _key is not None and (_SECRET_KEY.search(_key) or _key.lower() in PII_KEYS):
        return REDACTED if value not in (None, "") else value
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, BaseModel):
        return redact(value.model_dump(mode="json"), max_string=max_string, _key=_key)
    if isinstance(value, dict):
        return {str(k): redact(v, max_string=max_string, _key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(v, max_string=max_string) for v in value]
    if isinstance(value, str):
        return _redact_str(value, max_string)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_str(repr(value), max_string)


class Tracer:
    """Append-only JSONL writer for one run. Thread-safe (tool calls run in a worker
    thread so the per-tool timeout can be enforced)."""

    def __init__(self, trace_dir: str | Path, run_id: str) -> None:
        self.run_id = run_id
        self.path = Path(trace_dir) / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.events: list[dict[str, Any]] = []  # in-memory copy for tests and the CLI

    def event(self, kind: str, **fields: Any) -> dict[str, Any]:
        """Redact and append one line. Returns the redacted record."""
        record = {"ts": datetime.now(UTC).isoformat(), "run_id": self.run_id, "event": kind}
        record.update(redact(fields))
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self.events.append(record)
        return record

    def tool_calls(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event"] == "tool_call"]


class NullTracer(Tracer):
    """Tracer that keeps events in memory but writes nothing. For unit tests only."""

    def __init__(self, run_id: str = "test") -> None:  # noqa: D107
        self.run_id = run_id
        self.path = Path("/dev/null")
        self._lock = threading.Lock()
        self.events = []

    def event(self, kind: str, **fields: Any) -> dict[str, Any]:
        record = {"ts": datetime.now(UTC).isoformat(), "run_id": self.run_id, "event": kind}
        record.update(redact(fields))
        with self._lock:
            self.events.append(record)
        return record
