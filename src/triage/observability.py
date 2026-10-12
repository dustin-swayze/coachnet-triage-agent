"""Optional LangSmith tracing, off by default (ADR 0006).

Two layers of observability, on purpose:
- **JSONL traces** (``tracing.py``) are the system of record. Always on, local,
  redacted at write time, and the audit artifact for the director.
- **LangSmith** is an optional hosted view: run trees, token/cost/latency dashboards,
  datasets and evals. It sends (redacted) prompts and outputs to LangChain's cloud, so
  it is opt-in per environment via ``LANGSMITH_ENABLED=true``.

"Off" is authoritative. LangChain and LangSmith also read the library's own switch
(``LANGSMITH_TRACING``) from the process environment, and a stray value there would
start uploading even with our flag off. When our flag is off, this module forces the
library switches to "false" in-process *and* wraps the run in a disabled tracing
context. Our flag is the only way tracing turns on.

When on, every outbound payload passes through ``mask`` (the same redaction as the
JSONL traces) before it leaves the process, and the run is tagged with our JSONL
``run_id`` so a LangSmith run and its local trace file can be matched.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langsmith import Client
from langsmith import utils as ls_utils
from langsmith.run_helpers import tracing_context

from triage.config import Settings
from triage.tracing import redact

LIBRARY_SWITCHES = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_TRACING",
)
OUTBOUND_MAX_STRING = 4000  # payload excerpts are useful in the UI; secrets are still masked


def mask(data: Any) -> Any:
    """Redact a run's inputs/outputs before LangSmith uploads them."""
    return redact(data, max_string=OUTBOUND_MAX_STRING)


def force_library_tracing_off() -> None:
    """Make sure nothing in this process traces to LangSmith unless our flag says so."""
    for name in LIBRARY_SWITCHES:
        os.environ[name] = "false"
    clear = getattr(ls_utils.get_env_var, "cache_clear", None)
    if clear is not None:
        clear()  # langsmith caches env lookups


def build_client(settings: Settings) -> Client:
    assert settings.langsmith_api_key is not None  # enforced by Settings validation
    return Client(
        api_key=settings.langsmith_api_key.get_secret_value(),
        api_url=settings.langsmith_endpoint,
        hide_inputs=mask,
        hide_outputs=mask,
    )


@contextmanager
def langsmith_run(settings: Settings, *, run_id: str) -> Iterator[str | None]:
    """Context for one triage run. Yields the LangSmith project name, or None if off."""
    if not settings.langsmith_enabled:
        force_library_tracing_off()
        with tracing_context(enabled=False):
            yield None
        return

    client = build_client(settings)
    try:
        with tracing_context(
            enabled=True,
            client=client,
            project_name=settings.langsmith_project,
            tags=["triage", str(settings.triage_mode), settings.partner_code],
            metadata={
                "triage_run_id": run_id,
                "partner": settings.partner_code,
                "mode": str(settings.triage_mode),
            },
        ):
            yield settings.langsmith_project
    finally:
        # Short-lived CLI processes can exit before the background uploader drains.
        try:
            client.flush()
        except Exception:  # noqa: BLE001 - observability must never break a triage run
            pass
