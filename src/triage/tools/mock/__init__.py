"""Mock tool backends: fixture-driven, zero network, zero cost (ADR 0003).

Part B implements these. Each class reads synthetic JSON fixtures from
``src/triage/tools/mock/fixtures/`` and honours the same scoping rules as live:
a case whose partner_code != settings.partner_code raises ScopeError even in mock.

Fixture plan (one scenario per seed runbook entry, ids match docs/runbook front matter):
  cnet-001  duplicate case without redispatch flag
  cnet-002  sent but not in priority queue
  cnet-003  send failed: payload vs contract mismatch
  cnet-004  transform (DataWeave) failure
  cnet-005  expired / invalid auth
  cnet-006  HubSpot rate limit
  cnet-007  Mule app down / restarting   (health-check short-circuit)
"""

from __future__ import annotations

from triage.config import Settings
from triage.tools.base import ToolSet


def build_mock_tools(settings: Settings) -> ToolSet:  # pragma: no cover - Part B
    raise NotImplementedError("Session 1 Part B: implement mock tools + fixtures")
