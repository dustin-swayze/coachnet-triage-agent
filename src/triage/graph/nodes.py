"""Graph nodes. Each takes ``TriageState`` and returns a partial update.

Conventions
- Nodes are closures built by ``make_nodes(tools, llm, tracer, settings)`` so they are
  pure with respect to module globals and easy to test with fakes.
- Every tool invocation goes through ``_call_tool``: it enforces the step cap and the
  deadline, applies the per-tool timeout, writes one trace line, converts ``ToolError``
  into ``state.errors`` + ``Evidence``, and increments ``steps_used``.
- LLM calls pass ``config={"tags": [node_name]}`` so the mock ScriptedChatModel can
  pick the right canned reply (ADR 0003).
- Log text and case text are untrusted. Prompts wrap them in a clearly delimited
  "DATA" block and instruct the model to treat them as evidence only.

Part B fills in the bodies. Signatures and docstrings are the contract.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from triage.config import Settings
from triage.schemas.state import TriageState
from triage.tools.base import ToolSet

NodeFn = Callable[[TriageState], dict[str, Any]]


class Nodes:
    """Container for node functions. Built once per run via ``make_nodes``."""

    def __init__(self, tools: ToolSet, llm: Any, tracer: Any, settings: Settings) -> None:
        self.tools = tools
        self.llm = llm
        self.tracer = tracer
        self.settings = settings

    # ---- deterministic -------------------------------------------------------
    def health_check(self, state: TriageState) -> dict[str, Any]:
        """Call check_mule_app_status (no LLM). Store ``app_status`` + Evidence.

        On ToolError: app_status = AppStatus(status=UNKNOWN, ...), error recorded;
        the run continues (ADR 0002).
        """
        raise NotImplementedError

    # ---- LLM: structured extraction -------------------------------------------
    def parse_request(self, state: TriageState) -> dict[str, Any]:
        """LLM structured output -> ``ParsedRequest``.

        Extract intent, case number / external id / correlation id, time window
        (default: last 24h ending now), and the symptom text. A regex pre-pass pulls
        obvious 8-digit case numbers so the model cannot hallucinate one.
        """
        raise NotImplementedError

    # ---- tools (fixed order) ---------------------------------------------------
    def get_case(self, state: TriageState) -> dict[str, Any]:
        """get_sf_case(parsed.case_number | external_id). Store ``case`` + Evidence.

        ScopeError / NotFoundError -> recorded, run continues to find_transaction
        using whatever identifiers the user gave.
        """
        raise NotImplementedError

    def find_transaction(self, state: TriageState) -> dict[str, Any]:
        """search_mule_logs by correlation id (from case or request) else case number.

        Window: parsed window, else [case.last_sync_at - 1h, +1h], else last 24h.
        Store ``logs`` + Evidence (count, error count, error types).
        """
        raise NotImplementedError

    # ---- LLM + tool loop --------------------------------------------------------
    def diagnose(self, state: TriageState) -> dict[str, Any]:
        """LLM with the ``search_runbook`` tool bound. May emit tool calls.

        Loop: diagnose -> (tool calls?) -> diagnose_tools -> diagnose ... until the
        model returns a structured ``Diagnosis`` or the step cap / deadline is hit.
        Appends to ``messages``; when done, sets ``diagnosis``.
        """
        raise NotImplementedError

    def diagnose_tools(self, state: TriageState) -> dict[str, Any]:
        """Execute pending tool calls from the last AI message (search_runbook only).

        Each call goes through ``_call_tool`` (traced, counted). Appends ToolMessages
        and ``runbook_hits``.
        """
        raise NotImplementedError

    # ---- LLM: final answer -------------------------------------------------------
    def respond(self, state: TriageState) -> dict[str, Any]:
        """Write ``final_response``.

        Three shapes, chosen from state in code (not by the model):
          1. app unhealthy  -> alert: status summary, last deploy, restarts, next step
          2. no case ref / unclear -> ask for a case number or HubSpot ticket id
          3. diagnosis present -> diagnosis, evidence list, suggested fix, confidence,
             plus an "INCOMPLETE: <reason>" banner when ``incomplete`` is set
        The LLM is only used for shape 3's prose; shapes 1 and 2 are templates.
        """
        raise NotImplementedError

    # ---- shared plumbing -----------------------------------------------------------
    def _call_tool(self, state: TriageState, name: str, fn: Callable[[Any], Any], request: Any) -> Any:
        """Enforce limits, time the call, trace it, and normalise errors.

        Raises ``ToolError`` subclasses through to the caller after tracing, so the
        caller decides how to record them. Sets ``incomplete`` when a cap is hit.
        """
        raise NotImplementedError


# ---- routing predicates (used by conditional edges in build.py) -------------------
def route_after_health(state: TriageState) -> str:
    """'respond' if app unhealthy, else 'parse_request'."""
    app = state.get("app_status")
    return "parse_request" if app is None or app.is_healthy else "respond"


def route_after_parse(state: TriageState) -> str:
    """'get_case' if there is a case reference; 'respond' for health-only or unclear."""
    parsed = state.get("parsed")
    if parsed is None:
        return "respond"
    if parsed.has_case_reference:
        return "get_case"
    return "respond"


def route_after_diagnose(state: TriageState) -> str:
    """'diagnose_tools' if the last AI message has tool calls and limits allow,
    else 'respond'."""
    if state.get("incomplete"):
        return "respond"
    if state.get("steps_used", 0) >= state.get("step_cap", 0):
        return "respond"
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if last is not None and getattr(last, "tool_calls", None):
        return "diagnose_tools"
    return "respond"
