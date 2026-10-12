# ADR 0003: Mock/live tool interface with a fake LLM in mock mode

**Status:** accepted (2026-10-11)

## Context
Tonight's work happens on a home PC with no credentials and no real data. The project
must run end-to-end there at zero cost, and the same code must later run against real
Salesforce and Anypoint on the work laptop. We also need deterministic tests.

## Decision
### Tools
Each tool is defined once as an interface (a `Protocol` in `tools/base.py`) with a
Pydantic input model and a Pydantic output model. Two implementations exist per tool:

- `tools/mock/` reads synthetic fixtures (JSON) shaped like the real responses.
- `tools/live/` calls the real API with the read-only credential.

A factory selects the implementation from `TRIAGE_MODE`. Graph nodes receive tools by
injection and never import a concrete implementation.

### Error contract
Tools raise typed exceptions from one hierarchy:

```
ToolError
  ScopeError         scoping could not be proven (fail-closed)
  NotFoundError      no record / no log lines
  UpstreamError      the remote API failed (status, body excerpt, retryable flag)
  ToolTimeoutError   per-tool timeout hit
```

The graph layer catches `ToolError`, records it in the trace and in `state.errors`,
and continues. Tools never return `None` or an empty object to signal failure.

### LLM
`llm.get_chat_model()` returns:
- in `mock` mode: a `ScriptedChatModel` (subclass of LangChain's `BaseChatModel`)
  that picks its reply by the **node name** passed in `config["tags"]`, from a
  scenario script. It can emit tool calls (for `diagnose`) and plain text.
- in `live` mode: `init_chat_model(f"{provider}:{model}")`.

Embeddings follow the same pattern: `FakeEmbeddings` (deterministic hash-based) in
mock, a real embedding model in live.

## Why
- **One interface, two backends** is the standard way to keep tests fast and
  hermetic. It also forces the output schema to be designed before the live code
  exists, which is the schema we would port to Agentforce.
- **Typed exceptions over error-shaped returns.** A tool that returns `{"error": ...}`
  invites the model to ignore it. An exception forces the orchestration layer to make
  an explicit decision, which is where the decision belongs.
- **Node-keyed scripting over call-order scripting.** LangChain's list-based fake
  model returns replies in sequence. The moment a branch is skipped (health-only
  question, no case number), the sequence desyncs and every later reply is wrong.
  Keying on node name makes scenario files readable and robust.
- **Fixtures carry the failure modes.** Each seed runbook entry has a matching fixture
  set (case + logs + status). The mock-mode scenario tests are "given this evidence,
  does the graph produce this diagnosis", which is exactly what we can honestly claim
  about mock mode.

## What mock mode does not prove
That a real model reasons correctly over real logs. That requires Phase 5 evals with
`TRIAGE_MODE=live`. The README says this in plain words so the pitch does not
oversell.

## Consequences
- `tests/` never needs network or keys. CI can run them.
- Adding a tool means: schema -> interface -> mock + fixture -> live -> test.
- Fixture data is synthetic. Realistic *shapes*, invented *values*. Reviewed before
  any commit (constraint 3).
