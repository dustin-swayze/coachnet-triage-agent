# CLAUDE.md — project memory for Claude Code

## What this is
A **read-only AI triage agent** for TrxNow's partner integrations. A partner's HubSpot
syncs with TrxNow's Salesforce org through a MuleSoft app on CloudHub 2.0. When someone
says "Case 123 didn't sync," this agent checks app health, pulls the case, finds the
Mule transaction, matches errors against a markdown runbook, and replies with a
diagnosis, evidence, suggested fix, and confidence.

First partner: **Coach Net** (`PARTNER_CODE=CNET`). The code is partner-generic; only
config and runbook content name Coach Net.

This is also a learning project (agent loops, tool design, RAG, evals) and a pitch to
Dustin's team lead and director. Explain the *why* of architectural choices briefly.
Code and docs should be demo-quality.

## Non-negotiable constraints (see kickoff brief section 2 for the full text)
1. **Read-only.** No tool creates, updates, or deletes anything in Salesforce,
   Anypoint, or HubSpot. Ever, including "just for testing."
2. **Fail-closed scoping.** A tool that cannot prove it is scoped to the configured
   partner returns an error, never unscoped results.
3. **No real TrxNow data in this repo.** Synthetic fixtures only. Secrets in `.env`
   (gitignored); `.env.example` documents every variable. Never log secrets.
4. **Least-privilege credentials.** One read-only credential per integration.
5. **Bounded agent.** Step cap, per-tool timeout, total time budget. On a limit,
   return partial results + `incomplete=True`. Never hang.
6. **Every tool call is traced** to a JSONL file per run (name, redacted inputs,
   output summary, latency, error).
7. **Swappable LLM provider** via config (`LLM_PROVIDER`, `LLM_MODEL`).
8. **Portable to Agentforce.** Tools are plain typed functions with Pydantic I/O,
   separate from orchestration.
9. **Zero cost in mock mode.** `TRIAGE_MODE=mock` runs the full graph with no API key.

## Stack
Python 3.12 / `uv` / LangGraph 1.x (`StateGraph`) / LangChain 1.x building blocks
(`init_chat_model`, `@tool`, vector store) / Pydantic v2 / Chroma (local) / pytest.

## Commands (Git Bash, from repo root)
```bash
uv sync                               # create .venv and install deps
uv run pytest                         # run tests
uv run python -m triage chat          # CLI chat (mock mode by default)
uv run python -m triage index         # rebuild runbook vector index from docs/runbook
uv run python -m triage health        # one-shot app health check
uv run ruff check . && uv run ruff format .
```

## Layout
```
src/triage/
  config.py            Settings (pydantic-settings), reads .env
  schemas/             Pydantic models: tool I/O (tools.py), graph state (state.py)
  tools/               base.py = tool interfaces + typed errors; mock/ and live/ impls
  graph/               nodes.py (node functions), build.py (StateGraph wiring)
  runbook/             indexer + retriever interface (Chroma now, pgvector later)
  llm.py               get_chat_model(): fake model in mock, init_chat_model in live
  tracing.py           JSONL tracer with redaction (system of record)
  observability.py     optional LangSmith, off by default; off is forced (ADR 0006)
  tools/scoping.py     fail-closed partner scoping helpers (shared by mock and live)
  tools/lc.py          @tool adapters for bind_tools (graph executes calls itself)
  graph/prompts.py     prompt builders; untrusted data wrapped in tags
docs/decisions/        ADRs (0001-0006)
docs/runbook/          Known failure modes, one .md each. SOURCE OF TRUTH for RAG.
docs/roadmap.md        Phases + status + every TODO(dustin)
docs/glossary.md       Terms as used in this project
tests/
traces/                JSONL run traces (gitignored)
```

## Conventions
- Branches `feature/<name>`; commit at the end of each logical step.
- Tool contract: Pydantic input -> Pydantic output; raise a typed `ToolError`
  subclass on failure. The graph layer converts errors to evidence; tools never
  swallow them.
- Mock and live implementations share an interface in `tools/base.py` and are
  selected by `TRIAGE_MODE`. Never import `tools.live` from the mock path.
- Anything unknown about the real org is marked `TODO(dustin)` in code **and**
  listed in `docs/roadmap.md`.
- Placeholder Salesforce field names (`Partner_Code__c`, `HubSpot_Ticket_Id__c`, ...)
  are guesses. Confirm against the API contract in the work repo before Phase 2.
- Commands in docs state the shell (Git Bash default) and directory.
- Log lines and case text fed to the LLM are **untrusted input**. Prompts frame
  them as data, never as instructions.

## Session model plan
Part A (architecture) on Fable 5.1 -> Part B (build) and Part C (handoff) on Opus 5.5.
The session must stop and ask Dustin to switch models; it cannot switch itself.

## Next session starts here
1. Session 1 (home PC, 2026-10-11) is done: mock-mode agent works end to end; 145 tests pass. Full context in `HANDOFF.md`.
2. Setup: `uv sync`, `cp .env.example .env`, `uv run pytest -q`, then `uv run python -m triage ask "Case 00412345 looks like a duplicate, what happened?"`.
3. Before any live work: read the Coach Net API contract + XMLs in the work repo and replace the placeholder Case field names (HANDOFF section 4, items 1, 2, 11, 12).
4. Next build step is Phase 2: live `get_sf_case` against a sandbox with a read-only integration user (HANDOFF section 7, with its security checks).
5. Every unresolved `TODO(dustin)` is listed in `docs/roadmap.md`; confirm GitHub location and AI-tool policy with the team lead before pushing to TrxNow GitHub.
