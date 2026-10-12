"""Live tool backends. NOT built in Session 1. Phases 2-4 on the work laptop.

Security checklist before any live tool is enabled (repeat per phase):
  1. Credential is read-only / view-only. Verify by attempting a write in a sandbox
     and confirming it is refused.
  2. Credential is a dedicated integration user / connected app, not a person's login.
  3. Secrets come from settings (SecretStr) and are never logged or traced.
  4. Scoping filter is in the query AND re-checked on the result (ADR 0005).
  5. Per-tool timeout is applied at the HTTP client level (httpx timeout).
  6. Response bodies are truncated and redacted before entering state or trace.
"""

from __future__ import annotations

from triage.config import Settings
from triage.tools.base import ToolSet


def build_live_tools(settings: Settings) -> ToolSet:  # pragma: no cover - Phases 2-4
    settings.require_live_credentials()
    raise NotImplementedError("Live tools are built in Phases 2-4 on the work laptop")
