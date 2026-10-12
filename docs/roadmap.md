# Roadmap

Status key: `[ ]` not started, `[~]` in progress, `[x]` done, `[-]` deferred.

## Phase 1 - Scaffold and mock-mode agent (Session 1, home PC)
- [x] Part A: kickoff, open questions, critical review, knowledge base, ADRs, schemas, graph layout, seed runbook (Fable 5.1)
- [x] Part B: mock tools + fixtures, graph wired with limits and tracing, scripted LLM + hashing embeddings, indexer, CLI, 138 tests, demo transcript (Opus 5.5, with Sonnet/Haiku subagents)
- [x] Part C: HANDOFF.md, roadmap/CLAUDE.md updates, bridge-repo push commands (Opus 5.5)

## Observability (added 2026-10-11)
- [x] Optional LangSmith tracing behind `LANGSMITH_ENABLED` (off by default, ADR 0006)
- [ ] Local trace viewer / `triage stats` over the JSONL files (cost, latency, outcomes)
- [ ] Work dashboard: ship JSONL run summaries to Azure Monitor / Log Analytics (with Phase 6 hosting)

## Phase 2 - Live `get_sf_case` against a sandbox (work laptop)  <- NEXT
- [ ] Read the API contract and XMLs in the work repo; replace placeholder field names
- [ ] Read-only integration user + permission set (Case read, specific fields only)
- [ ] Connected app with least-privilege scopes (prefer JWT bearer flow)
- [ ] Scoping test against sandbox: non-partner case must return `ScopeError`
- Acceptance: a real sandbox case number returns a `CaseRecord`; a non-partner case is refused; no write scopes on the credential.

## Phase 3 - Live `check_mule_app_status`
- [ ] Anypoint connected app with view-only scopes (Runtime Manager read)
- [ ] CloudHub 2.0 deployments API: status, replicas, last deploy
- Acceptance: `uv run python -m triage health` matches what Runtime Manager shows; credential cannot stop/start/deploy.

## Phase 4 - Live `search_mule_logs`
- [ ] Confirm the CloudHub 2.0 logs endpoint, retention window, and filter capabilities
- [ ] Confirm how the correlation id appears in log lines (MDC key / message prefix)
- [ ] Confirm whether inbound HubSpot payloads are logged (needed for failure mode 03)
- Acceptance: given a known failed case in sandbox, the tool returns the matching transaction lines and the error.

## Phase 5 - Evals
- [ ] 10-20 scripted scenarios (known failure -> expected diagnosis)
- [ ] Run on every change; track pass rate
- [ ] Use results to pick Sonnet 5.5 vs Haiku 5.5 at runtime

## Phase 6 - Interfaces
- [ ] Slack bot (phone access)
- [ ] Scheduled poller reusing `check_mule_app_status` to alert before anyone files a bug
- [ ] Likely host: TrxNow Azure

## Phase 7 - Email intake
- [ ] Allowlisted bug emails -> classify -> Asana ticket -> run diagnosis
- [ ] Fix drafts as PRs to `test`, behind approval
- [ ] Reader/actor privilege split (email content is untrusted; prompt injection)

## Later / candidates
- **Payload-vs-contract validator tool** (deterministic). Failure mode 03 needs the
  exact inbound HubSpot payload compared with the API contract. That is a code job,
  not an LLM job. Add once the contract is available in the work repo.
- **Priority Queue cross-check tool.** Failure mode 02 ("sent but not in the priority
  queue") needs a read-only lookup against the Priority Queue. Reuse the scoping
  pattern from that project.
- **Hybrid retrieval** (BM25 + vector) and pgvector backend for the runbook.
- **Backburner:** conversational dispatch agent (slot-filling, confirm, invoke
  existing Flows).

## Open items: every `TODO(dustin)`
| # | Where | What is needed |
|---|---|---|
| 1 | `.env.example`, `config.py` | Confirm Case field API names for partner code, HubSpot external id, sync status, last sync, correlation id, redispatch flag. Source: API contract + XMLs in the work repo. |
| 2 | `.env.example`, `config.py` | Confirm correlation id location: Case field, log MDC key, HTTP header, or all three. |
| 3 | `.env.example` | Exact Mule deployment name in Runtime Manager and the Private Space / environment ids. |
| 4 | `.env.example`, Phase 4 | CloudHub 2.0 logs endpoint, retention, filters, and whether inbound payloads are logged. |
| 5 | `.env.example` | Salesforce auth flow for the integration user (JWT bearer preferred). |
| 6 | `docs/runbook/*` | Each seed entry has placeholder log text and fix steps. Replace with real wording (synthetic values, real shapes) once the log format is known. |
| 7 | `docs/runbook/02-*` | How the Priority Queue is checked today (object, field, or report). |
| 8 | `config.py` | Confirm Anthropic as the live provider under TrxNow policy (Dustin says yes; confirm with team lead). |
| 9 | `config.py`, ADR 0004 | Live embeddings provider: keep local hashing embeddings, or Voyage / OpenAI per policy. Decide with Phase 5 eval results. |
| 10 | `tracing.py` `PII_KEYS` | Confirm which Case fields are PII (contact name, phone, email, location, VIN?) so traces and prompts redact them. |
| 11 | `llm.py` `TICKET_RE` | Real HubSpot ticket id format (fixtures use a synthetic `hs-NNNNN`). Update the regex and the `GetCaseRequest` validation. |
| 12 | `schemas/tools.py` `SyncStatusValue` | Map logical sync states to the real picklist values on Case. |
| 13 | `.env` `LANGSMITH_ENABLED` | Approval to send redacted prompts/outputs to LangSmith (LangChain cloud, US or EU) with real case data. If not approved, keep it off or evaluate self-hosted Langfuse. |
