"""Mock tool backends: fixture-driven, zero network, zero cost (ADR 0003).

Each class reads synthetic JSON from ``fixtures/`` and honours the same scoping rules
as live (ADR 0005): the partner filter is built by the shared ``tools.scoping`` helpers
and the returned record is re-checked, so a mock lookup can raise ScopeError exactly
like the real one.

Fixture scenarios (ids match docs/runbook front matter):
  cnet-001  duplicate case without redispatch flag     00412301 / 00412345, hs-55501
  cnet-002  sent but not in priority queue             00412400, hs-55620
  cnet-003  payload vs contract mismatch (400)         hs-55777 (no Case)
  cnet-004  DataWeave transform failure (500)          hs-55810 (no Case)
  cnet-005  expired / invalid auth                     hs-55900..55902 (no Case)
  cnet-006  HubSpot rate limit (429)                   00412500, hs-55950
  cnet-007  Mule app down / restarting                 settings.mock_app_state
"""

from __future__ import annotations

from triage.config import Settings
from triage.tools.base import ToolSet
from triage.tools.mock.app_status import MockCheckMuleAppStatus
from triage.tools.mock.logs import MockSearchMuleLogs
from triage.tools.mock.sf_case import MockGetSfCase

__all__ = [
    "MockCheckMuleAppStatus",
    "MockGetSfCase",
    "MockSearchMuleLogs",
    "build_mock_tools",
]


def build_mock_tools(settings: Settings) -> ToolSet:
    """Assemble the four mock tools. The runbook search is the real retriever (it only
    reads local markdown), so it is imported lazily to keep this module importable on its own."""
    from triage.runbook.tool import RunbookSearch  # noqa: PLC0415

    return ToolSet(
        check_mule_app_status=MockCheckMuleAppStatus(settings),
        get_sf_case=MockGetSfCase(settings),
        search_mule_logs=MockSearchMuleLogs(settings),
        search_runbook=RunbookSearch(settings),
    )
