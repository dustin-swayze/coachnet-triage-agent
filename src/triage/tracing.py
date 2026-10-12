"""JSONL run tracer with redaction (constraint 6).

One file per run: ``traces/<run_id>.jsonl``. One line per event:

    {"ts": ..., "run_id": ..., "event": "tool_call", "tool": "get_sf_case",
     "inputs": {...redacted...}, "output_summary": "...", "latency_ms": 123,
     "error": null | {"type": "ScopeError", "message": "..."}}

Also emitted: ``run_start``, ``node_enter``, ``node_exit``, ``llm_call`` (model,
node, token counts if available, never the prompt body in v1), ``run_end``.

Redaction rules (applied to inputs and output summaries, recursively):
- keys matching /token|secret|password|api[_-]?key|authorization|cookie/i -> "***"
- values matching common secret shapes (sk-..., Bearer ..., 00D... session ids) -> "***"
- config-listed PII field names (TODO(dustin): confirm which Case fields are PII) -> "***"
- strings truncated to 500 chars
Pydantic ``SecretStr`` values are never rendered.
"""

from __future__ import annotations

from typing import Any


class Tracer:
    def __init__(self, trace_dir: str, run_id: str) -> None:
        self.trace_dir = trace_dir
        self.run_id = run_id

    def event(self, kind: str, **fields: Any) -> None:
        """Append one redacted JSON line."""
        raise NotImplementedError("Session 1 Part B")


def redact(value: Any) -> Any:
    """Pure function; unit-tested in tests/test_trace_redaction.py."""
    raise NotImplementedError("Session 1 Part B")
