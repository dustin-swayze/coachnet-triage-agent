"""Graph nodes. Each takes ``TriageState`` and returns a partial state update.

Conventions
- Every tool invocation goes through ``Nodes._call_tool``. It enforces the step cap
  and the run deadline, applies the per-tool timeout, writes one trace line, and turns
  a ``ToolError`` into a ``ToolErrorRecord`` instead of letting it escape. A failed
  tool is *evidence* ("case not found for CNET"), not a crash.
- Every LLM invocation goes through ``Nodes._invoke_llm``: same deadline, traced,
  failures recorded. The graph never hangs on a slow provider.
- LLM calls carry ``config={"tags": [node]}``. The mock model routes on that tag.
- Deterministic guards sit after each LLM step: identifiers the model extracts must
  appear verbatim in the user's text, and runbook ids it cites must have been
  returned by ``search_runbook`` in this run. Cheap, and it removes two whole classes
  of hallucination.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from triage.config import Settings
from triage.graph import prompts
from triage.llm import model_label, regex_parse
from triage.schemas.state import Diagnosis, Evidence, ParsedRequest, RequestIntent, TriageState
from triage.schemas.tools import (
    AppStatus,
    AppStatusRequest,
    AppStatusValue,
    GetCaseRequest,
    RunbookSearchRequest,
    SearchLogsRequest,
    ToolErrorRecord,
)
from triage.tools.base import ToolError, ToolSet, ToolTimeoutError
from triage.tools.lc import make_runbook_tool
from triage.tracing import Tracer

RUNBOOK_TOOL = "search_runbook"
DIAGNOSIS_TOOL = "Diagnosis"
DEFAULT_WINDOW = timedelta(hours=24)
MAX_WINDOW = timedelta(days=7)

# One shared worker pool. Python cannot kill a thread, so a timed-out tool keeps
# running in the background until it returns; the graph simply stops waiting for it.
# Live tools also set HTTP-level timeouts so those threads end promptly.
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="triage-tool")


def _now() -> datetime:
    return datetime.now(UTC)


_ERROR_TYPES = {"ScopeError", "NotFoundError", "UpstreamError", "ToolTimeoutError"}


def _error_record(tool: str, exc: Exception) -> ToolErrorRecord:
    name = type(exc).__name__
    return ToolErrorRecord(
        tool=tool,
        error_type=name if name in _ERROR_TYPES else "ToolError",  # type: ignore[arg-type]
        message=str(exc)[:500],
        retryable=bool(getattr(exc, "retryable", False)),
    )


def _summarize(result: Any) -> str | None:
    if result is None:
        return None
    summary = getattr(result, "summary", None)
    if isinstance(summary, str):
        return summary
    hits = getattr(result, "hits", None)
    if hits is not None:
        return f"{len(hits)} runbook hits: " + ", ".join(f"{h.id} ({h.score:.2f})" for h in hits)
    return type(result).__name__


@dataclass
class ToolOutcome:
    result: Any = None
    error: ToolErrorRecord | None = None
    limit_reason: str | None = None  # set when the call was NOT made because of a limit

    @property
    def called(self) -> bool:
        return self.limit_reason is None


class Nodes:
    """Node functions bound to one run's tools, model, tracer, and settings."""

    def __init__(self, tools: ToolSet, llm: Any, tracer: Tracer, settings: Settings) -> None:
        self.tools = tools
        self.llm = llm
        self.tracer = tracer
        self.settings = settings
        self.runbook_lc_tool = make_runbook_tool(tools)

    # ------------------------------------------------------------------ plumbing
    def _limit_check(self, state: TriageState, steps_so_far: int, what: str) -> str | None:
        if steps_so_far >= state["step_cap"]:
            reason = f"step cap reached ({state['step_cap']} tool calls) before {what}"
            self.tracer.event("limit_hit", limit="step_cap", before=what, detail=reason)
            return reason
        if _now() >= state["deadline"]:
            reason = f"time budget exhausted ({self.settings.run_budget_seconds:g}s) before {what}"
            self.tracer.event("limit_hit", limit="time_budget", before=what, detail=reason)
            return reason
        return None

    def _call_tool(
        self,
        state: TriageState,
        steps_so_far: int,
        name: str,
        fn: Callable[[Any], Any],
        request: Any,
    ) -> ToolOutcome:
        """Run one tool call under the limits; trace it; never raise."""
        reason = self._limit_check(state, steps_so_far, name)
        if reason:
            return ToolOutcome(limit_reason=reason)
        remaining = (state["deadline"] - _now()).total_seconds()
        timeout = max(0.01, min(self.settings.tool_timeout_seconds, remaining))
        started = time.perf_counter()
        result, error = None, None
        future = _POOL.submit(fn, request)
        try:
            result = future.result(timeout=timeout)
        except FutureTimeout:
            future.cancel()
            error = _error_record(
                name, ToolTimeoutError(f"{name} did not return within {timeout:.1f}s")
            )
        except ToolError as exc:
            error = _error_record(name, exc)
        except Exception as exc:  # a bug in a tool must not crash the run
            error = _error_record(name, RuntimeError(f"unexpected {type(exc).__name__}: {exc}"))
        self.tracer.event(
            "tool_call",
            tool=name,
            inputs=request,
            output_summary=_summarize(result),
            latency_ms=round((time.perf_counter() - started) * 1000),
            error=error,
        )
        return ToolOutcome(result=result, error=error)

    def _invoke_llm(
        self, state: TriageState, node: str, call: Callable[[], Any]
    ) -> tuple[Any, ToolErrorRecord | None]:
        """Run one model call under the run deadline; trace it; never raise."""
        remaining = (state["deadline"] - _now()).total_seconds()
        if remaining <= 0:
            err = ToolErrorRecord(
                tool=f"llm:{node}", error_type="ToolTimeoutError", message="time budget exhausted"
            )
            self.tracer.event("limit_hit", limit="time_budget", before=f"llm:{node}")
            return None, err
        started = time.perf_counter()
        future = _POOL.submit(call)
        out, err = None, None
        try:
            out = future.result(timeout=remaining)
        except FutureTimeout:
            err = ToolErrorRecord(
                tool=f"llm:{node}",
                error_type="ToolTimeoutError",
                message=f"model call exceeded {remaining:.1f}s",
            )
        except Exception as exc:  # provider errors, validation errors from structured output
            err = ToolErrorRecord(
                tool=f"llm:{node}",
                error_type="ToolError",
                message=f"{type(exc).__name__}: {exc}"[:500],
            )
        usage = getattr(out, "usage_metadata", None) if out is not None else None
        self.tracer.event(
            "llm_call",
            node=node,
            model=model_label(self.llm),
            latency_ms=round((time.perf_counter() - started) * 1000),
            usage=usage,
            output_kind=type(out).__name__ if out is not None else None,
            error=err,
        )
        return out, err

    def _from_outcome(
        self, state: TriageState, outcome: ToolOutcome, key: str, label: str
    ) -> dict[str, Any]:
        """Standard state update for a fixed-order tool node."""
        update: dict[str, Any] = {}
        if not outcome.called:
            update.update(incomplete=True, incomplete_reason=outcome.limit_reason)
            return update
        update["steps_used"] = state["steps_used"] + 1
        if outcome.error:
            update["errors"] = [outcome.error]
            update["evidence"] = [
                Evidence(
                    source_tool=outcome.error.tool,
                    at=_now(),
                    summary=f"{label} failed: {outcome.error.error_type}: {outcome.error.message}",
                )
            ]
        else:
            update[key] = outcome.result
            update["evidence"] = [
                Evidence(source_tool=label, at=_now(), summary=_summarize(outcome.result) or "")
            ]
        return update

    # ------------------------------------------------------------------ nodes
    def health_check(self, state: TriageState) -> dict[str, Any]:
        """Deterministic, no LLM (ADR 0002). A failing status tool yields UNKNOWN and the
        run continues: a monitoring outage must not block case triage."""
        outcome = self._call_tool(
            state,
            state["steps_used"],
            "check_mule_app_status",
            self.tools.check_mule_app_status,
            AppStatusRequest(),
        )
        update = self._from_outcome(state, outcome, "app_status", "check_mule_app_status")
        if outcome.called and outcome.error:
            update["app_status"] = AppStatus(
                app_name=self.settings.mule_app_name,
                status=AppStatusValue.UNKNOWN,
                replicas_desired=0,
                replicas_running=0,
                checked_at=_now(),
                source="mock" if self.settings.is_mock else "anypoint",
                detail=f"status check failed: {outcome.error.message}",
            )
        return update

    def parse_request(self, state: TriageState) -> dict[str, Any]:
        """LLM structured extraction, then deterministic guards."""
        text = state["user_request"]
        msgs = prompts.parse_messages(text, self.settings.partner_name)
        structured = self.llm.with_structured_output(ParsedRequest)
        parsed, err = self._invoke_llm(
            state,
            "parse_request",
            lambda: structured.invoke(msgs, config={"tags": ["parse_request"]}),
        )
        update: dict[str, Any] = {}
        if err or not isinstance(parsed, ParsedRequest):
            # Fall back to deterministic extraction rather than failing the run.
            parsed = ParsedRequest.model_validate(regex_parse(text))
            if err:
                update["errors"] = [err]
        parsed = self._guard_parsed(parsed, text)
        update["parsed"] = parsed
        update["evidence"] = [
            Evidence(
                source_tool="parse_request",
                at=_now(),
                summary=f"intent={parsed.intent} case={parsed.case_number or '-'} "
                f"ticket={parsed.external_id or '-'} corr={parsed.correlation_id or '-'}",
            )
        ]
        return update

    @staticmethod
    def _guard_parsed(parsed: ParsedRequest, text: str) -> ParsedRequest:
        """Drop identifiers that do not appear verbatim in the user's text."""
        lowered = text.lower()
        data = parsed.model_dump()
        for field in ("case_number", "external_id", "correlation_id"):
            value = data.get(field)
            if value and str(value).lower() not in lowered:
                data[field] = None
        fixed = ParsedRequest.model_validate(data)
        if fixed.has_case_reference:
            fixed.intent = RequestIntent.CASE_TRIAGE
        elif fixed.intent is RequestIntent.CASE_TRIAGE:
            fixed.intent = RequestIntent.UNCLEAR
        return fixed

    def get_case(self, state: TriageState) -> dict[str, Any]:
        """Scoped case lookup. NotFound / ScopeError become evidence; the run continues
        to the log search with whatever identifiers the user gave."""
        parsed = state["parsed"]
        assert parsed is not None
        if parsed.case_number:
            request = GetCaseRequest(case_number=parsed.case_number)
        elif parsed.external_id:
            request = GetCaseRequest(external_id=parsed.external_id)
        else:
            return {
                "evidence": [
                    Evidence(
                        source_tool="get_sf_case",
                        at=_now(),
                        summary="skipped: only a correlation id was given",
                    )
                ]
            }
        outcome = self._call_tool(
            state, state["steps_used"], "get_sf_case", self.tools.get_sf_case, request
        )
        return self._from_outcome(state, outcome, "case", "get_sf_case")

    def find_transaction(self, state: TriageState) -> dict[str, Any]:
        """Search logs by every identifier we know (OR'd), so retries and duplicates for
        the same ticket show up together."""
        if state.get("incomplete"):
            return {}
        parsed, case = state["parsed"], state.get("case")
        assert parsed is not None
        now = _now()
        start = parsed.window_start or now - DEFAULT_WINDOW
        end = parsed.window_end or now
        if case and not parsed.window_start:
            start = min(start, case.created_at - timedelta(hours=1))
        start = max(start, end - MAX_WINDOW)
        try:
            request = SearchLogsRequest(
                correlation_id=(case.correlation_id if case else None) or parsed.correlation_id,
                case_number=(case.case_number if case else None) or parsed.case_number,
                external_id=(case.external_id if case else None) or parsed.external_id,
                start=start,
                end=end,
            )
        except ValidationError as exc:
            err = ToolErrorRecord(
                tool="search_mule_logs",
                error_type="ScopeError",
                message=f"refused: {exc.errors()[0]['msg']}",
            )
            return {
                "errors": [err],
                "evidence": [
                    Evidence(
                        source_tool="search_mule_logs",
                        at=now,
                        summary=f"log search refused: {err.message}",
                    )
                ],
            }
        outcome = self._call_tool(
            state, state["steps_used"], "search_mule_logs", self.tools.search_mule_logs, request
        )
        return self._from_outcome(state, outcome, "logs", "search_mule_logs")

    def diagnose(self, state: TriageState) -> dict[str, Any]:
        """Model with ``search_runbook`` and ``Diagnosis`` bound as tools.

        Pattern: "final answer as a tool". The model loops on search_runbook until it
        calls Diagnosis. If it answers in plain text instead, one structured-output
        call converts that into a Diagnosis.
        """
        existing = state.get("messages") or []
        new_msgs: list[Any] = []
        if not existing:
            new_msgs = prompts.diagnose_messages(
                state, self.settings.partner_code, self.settings.partner_name
            )
        convo = existing + new_msgs
        bound = self.llm.bind_tools([self.runbook_lc_tool, Diagnosis])
        ai, err = self._invoke_llm(
            state, "diagnose", lambda: bound.invoke(convo, config={"tags": ["diagnose"]})
        )
        if err or not isinstance(ai, AIMessage):
            return {
                "messages": new_msgs,
                "errors": [err] if err else [],
                "incomplete": True,
                "incomplete_reason": f"diagnosis step failed: {err.message if err else 'no reply'}",
            }
        new_msgs.append(ai)
        update: dict[str, Any] = {"messages": new_msgs}

        diag_call = next((tc for tc in ai.tool_calls if tc["name"] == DIAGNOSIS_TOOL), None)
        if diag_call:
            try:
                update["diagnosis"] = self._guard_diagnosis(
                    Diagnosis.model_validate(diag_call["args"]), state
                )
            except ValidationError as exc:
                update["errors"] = [
                    ToolErrorRecord(
                        tool="llm:diagnose",
                        error_type="ToolError",
                        message=f"invalid Diagnosis: {exc}"[:500],
                    )
                ]
                update.update(
                    incomplete=True, incomplete_reason="model returned an invalid diagnosis"
                )
            return update
        if any(tc["name"] == RUNBOOK_TOOL for tc in ai.tool_calls):
            return update  # route_after_diagnose -> diagnose_tools

        # Plain-text answer: convert once with structured output.
        structured = self.llm.with_structured_output(Diagnosis)
        follow = convo + [ai, HumanMessage("Return your final answer using the Diagnosis schema.")]
        diag, err2 = self._invoke_llm(
            state,
            "diagnose_final",
            lambda: structured.invoke(follow, config={"tags": ["diagnose_final", "diagnose"]}),
        )
        if isinstance(diag, Diagnosis):
            update["diagnosis"] = self._guard_diagnosis(diag, state)
        else:
            update["errors"] = [err2] if err2 else []
            update.update(incomplete=True, incomplete_reason="model did not produce a diagnosis")
        return update

    def diagnose_tools(self, state: TriageState) -> dict[str, Any]:
        """Execute the runbook searches the model asked for, under the step cap. Every
        tool call gets a ToolMessage reply (providers require it), even when skipped."""
        last = state["messages"][-1]
        steps = state["steps_used"]
        tool_msgs: list[ToolMessage] = []
        hits: list[Any] = []
        errors: list[ToolErrorRecord] = []
        evidence: list[Evidence] = []
        update: dict[str, Any] = {}
        for tc in last.tool_calls:
            if tc["name"] != RUNBOOK_TOOL:
                tool_msgs.append(
                    ToolMessage(f"unknown tool {tc['name']}", tool_call_id=tc["id"], status="error")
                )
                continue
            try:
                request = RunbookSearchRequest.model_validate(tc["args"])
            except ValidationError as exc:
                tool_msgs.append(
                    ToolMessage(f"invalid arguments: {exc}", tool_call_id=tc["id"], status="error")
                )
                continue
            outcome = self._call_tool(
                state, steps, RUNBOOK_TOOL, self.tools.search_runbook, request
            )
            if not outcome.called:
                update.update(incomplete=True, incomplete_reason=outcome.limit_reason)
                tool_msgs.append(
                    ToolMessage(
                        f"not executed: {outcome.limit_reason}",
                        tool_call_id=tc["id"],
                        status="error",
                    )
                )
                continue
            steps += 1
            if outcome.error:
                errors.append(outcome.error)
                tool_msgs.append(
                    ToolMessage(
                        f"{outcome.error.error_type}: {outcome.error.message}",
                        tool_call_id=tc["id"],
                        status="error",
                    )
                )
                evidence.append(
                    Evidence(
                        source_tool=RUNBOOK_TOOL,
                        at=_now(),
                        summary=f"runbook search failed: {outcome.error.message}",
                    )
                )
            else:
                hits.extend(outcome.result.hits)
                tool_msgs.append(
                    ToolMessage(outcome.result.model_dump_json(), tool_call_id=tc["id"])
                )
                evidence.append(
                    Evidence(
                        source_tool=RUNBOOK_TOOL,
                        at=_now(),
                        summary=_summarize(outcome.result) or "",
                    )
                )
        update.update(
            messages=tool_msgs,
            runbook_hits=hits,
            errors=errors,
            evidence=evidence,
            steps_used=steps,
        )
        return update

    def _guard_diagnosis(self, diag: Diagnosis, state: TriageState) -> Diagnosis:
        """Keep only runbook ids actually returned in this run; cap confidence when the
        agent had no case and no logs to look at."""
        seen = {h.id for h in state.get("runbook_hits") or []}
        kept = [i for i in diag.matched_runbook_ids if i in seen]
        confidence = diag.confidence
        if len(kept) < len(diag.matched_runbook_ids):
            dropped = sorted(set(diag.matched_runbook_ids) - set(kept))
            self.tracer.event("guard", node="diagnose", dropped_runbook_ids=dropped)
            confidence = min(confidence, 0.5)
        if not state.get("case") and not state.get("logs"):
            confidence = min(confidence, 0.4)
        return diag.model_copy(
            update={"matched_runbook_ids": kept, "confidence": round(confidence, 2)}
        )

    def respond(self, state: TriageState) -> dict[str, Any]:
        """Render the final answer from state with a template.

        Deliberate change from the brief's sketch (where respond was an LLM node): the
        model's prose already lives in the structured ``Diagnosis``. Rendering it with
        code saves one model call (latency, cost) and guarantees the evidence list and
        the INCOMPLETE banner are always present and accurate.
        """
        incomplete = bool(state.get("incomplete"))
        reason = state.get("incomplete_reason")
        if not incomplete and _now() > state["deadline"]:
            incomplete, reason = True, "time budget exhausted"
        text = render_response(state, self.settings, incomplete=incomplete, reason=reason)
        return {"final_response": text, "incomplete": incomplete, "incomplete_reason": reason}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_response(
    state: TriageState, settings: Settings, *, incomplete: bool, reason: str | None
) -> str:
    app, parsed, diag = state.get("app_status"), state.get("parsed"), state.get("diagnosis")
    app_line = app.summary if app else "unknown"
    lines: list[str] = []
    if incomplete:
        lines.append(f"INCOMPLETE: {reason}. Showing what was found so far.\n")

    if app is not None and not app.is_healthy:
        lines += [
            f"ALERT: the {settings.partner_name} Mule app is not healthy.",
            f"  {app.summary}",
            "  Last deploy: "
            + (
                app.last_deployed_at.isoformat(timespec="seconds")
                if app.last_deployed_at
                else "unknown"
            ),
        ]
        if app.detail:
            lines.append(f"  Detail: {app.detail}")
        lines += [
            "",
            "Any sync problem reported right now is most likely caused by this. Case lookup was",
            "skipped. Next step (human, in Runtime Manager): check deployment logs, then",
            "redeploy or restart. See runbook cnet-007.",
        ]
    elif parsed is not None and parsed.intent is RequestIntent.HEALTH_ONLY:
        lines.append(f"The {settings.partner_name} Mule app looks healthy: {app_line}.")
        if app and app.status is AppStatusValue.UNKNOWN:
            lines.append("Note: the status check itself failed, so this is not confirmed.")
    elif parsed is not None and not parsed.has_case_reference:
        lines += [
            "I need a case to look at. Give me a Salesforce case number (8 digits) or a",
            "HubSpot ticket id, and optionally when it happened.",
            f"(App health right now: {app.summary if app else 'unknown'}.)",
        ]
    elif (
        diag is None and state.get("case") is None and state.get("logs") is None and not incomplete
    ):
        ref = parsed.case_number or parsed.external_id or parsed.correlation_id
        lines += [
            f"I found nothing for {ref} in {settings.partner_name} ({settings.partner_code}) data:",
            *[f"  - {e.tool}: {e.error_type}: {e.message}" for e in state.get("errors") or []],
            "",
            "No diagnosis is possible without a case or log transaction. Check the number, that",
            f"it is a {settings.partner_name} case, and the time window. If a lookup timed out or",
            "failed upstream, try again shortly.",
        ]
    elif diag is not None:
        lines += [
            f"Diagnosis: {diag.summary}",
            "",
            f"Likely cause: {diag.likely_cause.strip()}",
            "",
            "Suggested fix:",
            *[f"  {ln}" for ln in diag.suggested_fix.strip().splitlines()],
            "",
            f"Confidence: {diag.confidence:.0%}"
            + (
                f"   Runbook: {', '.join(diag.matched_runbook_ids)}"
                if diag.matched_runbook_ids
                else ""
            )
            + ("   (fix needs a human: it includes write actions)" if diag.needs_human else ""),
        ]
    else:
        lines.append("No diagnosis was produced.")

    evidence = state.get("evidence") or []
    if evidence:
        lines += ["", "Evidence:"]
        lines += [f"  - [{e.source_tool}] {e.summary}" for e in evidence]
    lines += [
        "",
        f"Steps used: {state.get('steps_used', 0)}/{state.get('step_cap')}"
        f"   Run: {state.get('run_id')}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Routing predicates (conditional edges in build.py)
# ---------------------------------------------------------------------------
def route_after_health(state: TriageState) -> str:
    """'respond' if the app is unhealthy, else 'parse_request'."""
    app = state.get("app_status")
    return "parse_request" if app is None or app.is_healthy else "respond"


def route_after_parse(state: TriageState) -> str:
    """'get_case' when there is a case reference; 'respond' for health-only/unclear."""
    parsed = state.get("parsed")
    if parsed is not None and parsed.has_case_reference and not state.get("incomplete"):
        return "get_case"
    return "respond"


def route_after_find_transaction(state: TriageState) -> str:
    """'respond' when there is nothing to diagnose (no case AND no logs, or a limit was
    hit). Deterministic: with zero evidence any runbook match would be a guess."""
    if state.get("incomplete"):
        return "respond"
    if state.get("case") is None and state.get("logs") is None:
        return "respond"
    return "diagnose"


def route_after_diagnose(state: TriageState) -> str:
    """'diagnose_tools' if the model asked for runbook searches and nothing is final."""
    if state.get("diagnosis") is not None or state.get("incomplete"):
        return "respond"
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and any(tc["name"] == RUNBOOK_TOOL for tc in last.tool_calls):
        return "diagnose_tools"
    return "respond"


def route_after_diagnose_tools(state: TriageState) -> str:
    """Back to 'diagnose' unless a limit was hit while executing tools."""
    return "respond" if state.get("incomplete") else "diagnose"
