# Partner Sync Triage Agent (Coach Net first)

A read-only AI agent that answers "why didn't this case sync?" for TrxNow's partner
integrations. First partner: **Coach Net**, whose HubSpot syncs with Salesforce through
a MuleSoft app on CloudHub 2.0.

> Ask: *"Case 00412345 never showed up in the priority queue, what happened?"*
> Get back, in about 30 seconds: app health, the case's sync fields, the Mule
> transaction and its errors, the matching runbook entry, a suggested fix, and a
> confidence score. Every tool call is traced to a JSONL file you can audit.

## Status
Session 1 scaffold. Mock mode only. No real credentials, data, or log formats are in
this repo. See [docs/roadmap.md](docs/roadmap.md).

## Why it's built this way (short version)
- **Deterministic first, LLM second.** The health check runs in plain code before any
  model call. If the Mule app is down, that *is* the answer.
  ([ADR 0002](docs/decisions/0002-deterministic-health-check-first.md))
- **Explicit graph, not a free-running agent.** LangGraph `StateGraph` with typed
  state, a step cap, per-tool timeouts, and a total time budget.
  ([ADR 0001](docs/decisions/0001-langgraph-stategraph-over-create-agent.md))
- **Mock and live behind one interface.** Every tool has a fixture-backed mock and a
  real implementation, chosen by `TRIAGE_MODE`. Mock mode runs the full graph with no
  API key. ([ADR 0003](docs/decisions/0003-mock-live-tools-and-fake-llm.md))
- **Markdown runbook is the source of truth.** The vector index is derived and
  rebuildable. ([ADR 0004](docs/decisions/0004-markdown-runbook-source-of-truth.md))
- **Partner-generic, fail-closed scoping.** Tools filter on a configured partner code
  and refuse to run unscoped. ([ADR 0005](docs/decisions/0005-partner-scoping-fail-closed.md))

## Architecture
```
start
  -> health_check       deterministic; app down -> respond (alert) -> end
  -> parse_request      LLM: extract case ref / time window / symptom / intent
      |- health-only question -> respond
      |- no case reference    -> respond (ask for one)
      '- case reference       v
  -> get_case           tool (scoped to PARTNER_CODE)
  -> find_transaction   tool: search Mule logs by correlation id / case id
  -> diagnose           LLM + search_runbook tool; loops within step cap
  -> respond            LLM: diagnosis, evidence, suggested fix, confidence
end
```

| Tool | Live source | Read-only? |
|---|---|---|
| `check_mule_app_status` | Anypoint Runtime Manager / CloudHub 2.0 API | yes |
| `get_sf_case` | Salesforce REST (read-only integration user) | yes |
| `search_mule_logs` | Anypoint Runtime Manager logs API | yes |
| `search_runbook` | local vector index over `docs/runbook/*.md` | yes |

## Running the demo (Git Bash, from repo root)
```bash
uv sync
cp .env.example .env            # defaults are mock mode; no keys needed
uv run python -m triage index   # build runbook index (fake embeddings in mock mode)
uv run python -m triage chat
```

## What mock mode proves, and what it doesn't
Mock mode exercises the whole graph: routing, tool contracts, scoping, limits, tracing,
and runbook retrieval. The LLM responses are **scripted**, so a passing mock scenario
proves the *plumbing* produces the right diagnosis for the right evidence. It does not
prove a real model reasons correctly. That is what Phase 5 evals are for.

## Demo transcript
_(Pasted here at the end of Session 1 Part B.)_

## Tests
```bash
uv run pytest
```
Covers: health-check short-circuit, step-cap behaviour, fail-closed scoping, tool error
handling, trace redaction, and one scenario per seed runbook entry.

## Docs
- [docs/decisions/](docs/decisions/) - ADRs
- [docs/runbook/](docs/runbook/) - known failure modes (RAG source)
- [docs/glossary.md](docs/glossary.md)
- [docs/roadmap.md](docs/roadmap.md)
- [coachnet-triage-agent-kickoff.md](coachnet-triage-agent-kickoff.md) - original brief
