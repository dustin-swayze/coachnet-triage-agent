# ADR 0001: LangGraph `StateGraph` over `create_agent` or a raw loop

**Status:** accepted (2026-10-11)

## Context
We need an agent that checks Mule app health, fetches a case, finds the transaction in
logs, matches against a runbook, and explains. LangChain 1.x offers three ways to build
this:

1. `create_agent` (LangChain's prebuilt ReAct-style agent): model + tools + prompt, and
   the model decides every step.
2. A hand-written `while` loop around a chat model with tool calling.
3. LangGraph `StateGraph`: explicit nodes and edges over typed state.

## Decision
Use an explicit `StateGraph`. The first node is a deterministic health check that
never involves the model (see ADR 0002). Case lookup and log search run in a fixed
order after request parsing. Only the `diagnose` node runs a model-driven tool loop,
and that loop is bounded by a step cap held in state.

## Why
- **Deterministic steps first.** `create_agent` lets the model choose whether to check
  health at all. We want it unconditional and first, in code. A graph makes that a
  structural guarantee, not a prompt instruction.
- **Typed state is the audit trail.** The director wants to see exactly what the agent
  did. With state holding `app_status`, `case`, `logs`, `runbook_hits`, `evidence`,
  `errors`, and `steps_used`, the final answer and the trace come from the same
  source.
- **Limits live in state.** Step cap, deadline, and `incomplete` are state fields that
  the `respond` node reads, so hitting a limit still produces a useful answer. A raw
  loop or `create_agent` would surface limits as exceptions and lose partial work.
- **Branching is explicit.** "Is the app up?" has no case number. A conditional edge
  after `parse_request` routes it straight to `respond`. Health-down short-circuits.
  These are edges, not prompt hacks.
- **Portability.** Tools are plain functions with Pydantic schemas. If the
  orchestration moves to Agentforce, the graph is the part we throw away.

## What we give up
- More code than `create_agent`. For a simpler agent (one tool, no mandatory steps),
  `create_agent` is the right fast path and we say so here.
- The model cannot "decide" to re-check health or re-fetch the case. That is a feature
  for v1; revisit if a scenario genuinely needs it.

## Consequences
- `src/triage/graph/build.py` owns the node/edge layout. Nodes are small functions in
  `graph/nodes.py` that take `TriageState` and return partial updates.
- The `diagnose` node is the only place the model calls tools. It gets exactly one tool
  (`search_runbook`) in v1.
- We still use LangChain 1.x for building blocks: `init_chat_model` (provider swap),
  `@tool` with Pydantic args, and the vector store integration.
