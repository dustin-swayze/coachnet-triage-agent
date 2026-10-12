"""StateGraph wiring (ADR 0001). This file is the whole control flow.

    START -> health_check --(unhealthy)--> respond -> END
                 |
              (healthy)
                 v
            parse_request --(no case ref / health-only)--> respond
                 |
            (case ref)
                 v
              get_case -> find_transaction -> diagnose --(tool calls)--> diagnose_tools
                                                   ^                          |
                                                   +--------------------------+
                                                   |
                                             (done / cap)
                                                   v
                                                respond -> END

Limits: ``steps_used`` vs ``step_cap`` and ``deadline`` are checked inside
``Nodes._call_tool`` and in ``route_after_diagnose``. LangGraph's ``recursion_limit``
is set as a backstop only (step_cap * 2 + 8) and should never be the thing that fires.
"""

from __future__ import annotations

from typing import Any

from triage.config import Settings
from triage.graph.nodes import (
    Nodes,
    route_after_diagnose,
    route_after_health,
    route_after_parse,
)
from triage.schemas.state import TriageState
from triage.tools.base import ToolSet


def build_graph(tools: ToolSet, llm: Any, tracer: Any, settings: Settings) -> Any:
    """Return a compiled LangGraph app.

    Part B implementation sketch::

        from langgraph.graph import END, START, StateGraph
        nodes = Nodes(tools, llm, tracer, settings)
        g = StateGraph(TriageState)
        g.add_node("health_check", nodes.health_check)
        g.add_node("parse_request", nodes.parse_request)
        g.add_node("get_case", nodes.get_case)
        g.add_node("find_transaction", nodes.find_transaction)
        g.add_node("diagnose", nodes.diagnose)
        g.add_node("diagnose_tools", nodes.diagnose_tools)
        g.add_node("respond", nodes.respond)
        g.add_edge(START, "health_check")
        g.add_conditional_edges("health_check", route_after_health,
                                {"parse_request": "parse_request", "respond": "respond"})
        g.add_conditional_edges("parse_request", route_after_parse,
                                {"get_case": "get_case", "respond": "respond"})
        g.add_edge("get_case", "find_transaction")
        g.add_edge("find_transaction", "diagnose")
        g.add_conditional_edges("diagnose", route_after_diagnose,
                                {"diagnose_tools": "diagnose_tools", "respond": "respond"})
        g.add_edge("diagnose_tools", "diagnose")
        g.add_edge("respond", END)
        return g.compile()
    """
    _ = (Nodes, route_after_diagnose, route_after_health, route_after_parse, TriageState)
    raise NotImplementedError("Session 1 Part B wires the graph")


def run_once(app: Any, user_request: str, settings: Settings) -> TriageState:
    """Create initial state, invoke with a recursion backstop, return final state."""
    raise NotImplementedError
