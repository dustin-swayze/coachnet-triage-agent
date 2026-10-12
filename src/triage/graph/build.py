"""StateGraph wiring (ADR 0001). This file is the whole control flow.

    START -> health_check --(unhealthy)--> respond -> END
                 |
              (healthy / unknown)
                 v
            parse_request --(no case ref / health-only)--> respond
                 |
            (case ref)
                 v
              get_case -> find_transaction --(no case AND no logs)--> respond
                                 |
                                 v
                              diagnose --(runbook search)--> diagnose_tools
                                 ^                                |
                                 +-------------(under cap)--------+
                                 |
                         (Diagnosis / limit)
                                 v
                              respond -> END

Limits: ``steps_used`` vs ``step_cap`` and ``deadline`` are enforced inside the nodes
so the run ends in ``respond`` with partial results. LangGraph's ``recursion_limit``
is only a backstop (it raises, which would lose partial work) and should never fire.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from triage.config import Settings
from triage.graph.nodes import (
    Nodes,
    route_after_diagnose,
    route_after_diagnose_tools,
    route_after_find_transaction,
    route_after_health,
    route_after_parse,
)
from triage.schemas.state import TriageState, initial_state
from triage.tools.base import ToolSet
from triage.tracing import Tracer


def _traced(
    name: str, fn: Callable[[TriageState], dict[str, Any]], tracer: Tracer
) -> Callable[[TriageState], dict[str, Any]]:
    def wrapper(state: TriageState) -> dict[str, Any]:
        tracer.event("node_enter", node=name, steps_used=state.get("steps_used", 0))
        started = time.perf_counter()
        update = fn(state)
        tracer.event(
            "node_exit",
            node=name,
            latency_ms=round((time.perf_counter() - started) * 1000),
            updated=sorted(update.keys()),
        )
        return update

    wrapper.__name__ = name
    return wrapper


def build_graph(tools: ToolSet, llm: Any, tracer: Tracer, settings: Settings) -> Any:
    """Return a compiled LangGraph app for one run (cheap to build; keeps the tracer
    run-scoped without threading it through LangGraph config)."""
    nodes = Nodes(tools, llm, tracer, settings)
    g = StateGraph(TriageState)
    for name in (
        "health_check",
        "parse_request",
        "get_case",
        "find_transaction",
        "diagnose",
        "diagnose_tools",
        "respond",
    ):
        g.add_node(name, _traced(name, getattr(nodes, name), tracer))

    g.add_edge(START, "health_check")
    g.add_conditional_edges(
        "health_check", route_after_health, {"parse_request": "parse_request", "respond": "respond"}
    )
    g.add_conditional_edges(
        "parse_request", route_after_parse, {"get_case": "get_case", "respond": "respond"}
    )
    g.add_edge("get_case", "find_transaction")
    g.add_conditional_edges(
        "find_transaction",
        route_after_find_transaction,
        {"diagnose": "diagnose", "respond": "respond"},
    )
    g.add_conditional_edges(
        "diagnose", route_after_diagnose, {"diagnose_tools": "diagnose_tools", "respond": "respond"}
    )
    g.add_conditional_edges(
        "diagnose_tools", route_after_diagnose_tools, {"diagnose": "diagnose", "respond": "respond"}
    )
    g.add_edge("respond", END)
    return g.compile()


@dataclass
class RunResult:
    state: TriageState
    tracer: Tracer
    elapsed_s: float

    @property
    def response(self) -> str:
        return self.state.get("final_response") or ""


def run_triage(
    user_request: str,
    settings: Settings,
    *,
    tools: ToolSet,
    llm: Any,
    trace_dir: str | None = None,
) -> RunResult:
    """One end-to-end run: fresh state, run-scoped tracer, compiled graph, invoke."""
    state = initial_state(
        user_request, step_cap=settings.step_cap, run_budget_seconds=settings.run_budget_seconds
    )
    tracer = Tracer(trace_dir or settings.trace_dir, state["run_id"])
    tracer.event(
        "run_start",
        request=user_request,
        mode=settings.triage_mode,
        partner=settings.partner_code,
        step_cap=settings.step_cap,
        run_budget_seconds=settings.run_budget_seconds,
    )
    app = build_graph(tools, llm, tracer, settings)
    started = time.perf_counter()
    final: TriageState = app.invoke(state, config={"recursion_limit": settings.step_cap * 3 + 10})
    elapsed = time.perf_counter() - started
    diag = final.get("diagnosis")
    tracer.event(
        "run_end",
        elapsed_ms=round(elapsed * 1000),
        steps_used=final.get("steps_used"),
        incomplete=final.get("incomplete"),
        incomplete_reason=final.get("incomplete_reason"),
        matched_runbook_ids=diag.matched_runbook_ids if diag else [],
        confidence=diag.confidence if diag else None,
    )
    return RunResult(state=final, tracer=tracer, elapsed_s=elapsed)
