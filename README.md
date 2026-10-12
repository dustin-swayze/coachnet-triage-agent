# Partner Sync Triage Agent (Coach Net first)

A read-only AI agent that answers "why didn't this case sync?" for TrxNow's partner
integrations. First partner: **Coach Net**, whose HubSpot syncs with Salesforce through
a MuleSoft app on CloudHub 2.0.

> Ask: *"Case 00412345 never showed up in the priority queue, what happened?"*
> Get back, in about 30 seconds: app health, the case's sync fields, the Mule
> transaction and its errors, the matching runbook entry, a suggested fix, and a
> confidence score. Every tool call is traced to a JSONL file you can audit.

## Status
Session 1 complete: working agent in mock mode, 145 passing tests. Mock mode only. No real credentials, data, or log formats are in
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
- **Local audit trail, optional hosted tracing.** JSONL traces are always written;
  LangSmith is behind a flag, off by default. ([ADR 0006](docs/decisions/0006-observability-jsonl-plus-optional-langsmith.md))
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
  -> find_transaction   tool: search Mule logs by every known id
      '- no case AND no logs -> respond ("found nothing", no guessing)
  -> diagnose           LLM + search_runbook tool; loops within the step cap,
                        finishes by calling the Diagnosis tool
  -> respond            template: diagnosis, evidence, fix, confidence, INCOMPLETE banner
end
```

Every tool call goes through one choke point that enforces the step cap, the run
deadline, and the per-tool timeout, writes a redacted trace line, and turns tool
errors into evidence. Two deterministic guards check the LLM: extracted ids must
appear in the user's text, and cited runbook ids must have been retrieved this run.

| Tool | Live source | Read-only? |
|---|---|---|
| `check_mule_app_status` | Anypoint Runtime Manager / CloudHub 2.0 API | yes |
| `get_sf_case` | Salesforce REST (read-only integration user) | yes |
| `search_mule_logs` | Anypoint Runtime Manager logs API | yes |
| `search_runbook` | local Chroma index over `docs/runbook/*.md` | yes |

## Running the demo (Git Bash, from repo root)
```bash
uv sync
cp .env.example .env            # defaults are mock mode; no keys needed
uv run python -m triage index   # build runbook index (free local embeddings)
uv run python -m triage chat    # or: uv run python -m triage ask "Case 00412345 ..."
```

Mock fixtures you can ask about:

| Ask about | Scenario |
|---|---|
| case 00412345 | duplicate case without the redispatch flag (cnet-001) |
| case 00412400 | sent, but not in the priority queue (cnet-002) |
| ticket hs-55777 | payload does not match the API contract (cnet-003) |
| ticket hs-55810 | DataWeave transform failure (cnet-004) |
| ticket hs-55900 | expired Salesforce session (cnet-005) |
| case 00412500 | HubSpot rate limit on status updates (cnet-006) |
| `:app down` then any case | Mule app down: health-check short-circuit (cnet-007) |
| case 00412600 | healthy case; its log carries a prompt-injection string |
| case 00499999 | another partner's case: scoped out, "found nothing" |
| case 00477777 | slow lookup (set `TOOL_TIMEOUT_SECONDS=0.3` to see the timeout) |

```bash
MOCK_APP_STATE=down uv run python -m triage ask "Case 00412345 didn't sync"
uv run python -m triage health
```

## Observability
- **JSONL traces (always on).** `traces/<run_id>.jsonl`: every node, tool call, and
  model call with redacted inputs, output summary, latency, errors, and token usage.
  Local, and the audit record. ([ADR 0006](docs/decisions/0006-observability-jsonl-plus-optional-langsmith.md))
- **LangSmith (optional, off by default).** Hosted run trees, cost and latency
  dashboards, and eval tooling. Free tier covers this project. It uploads redacted
  prompts and outputs to LangChain's cloud, so enable it only where that is approved.

```bash
# Git Bash, repo root. Get a key at smith.langchain.com, then in .env:
#   LANGSMITH_ENABLED=true
#   LANGSMITH_API_KEY=lsv2_...
uv run python -m triage ask "Case 00412345 looks like a duplicate, what happened?"
# The run appears in project "partner-triage", tagged with the same run id as the JSONL file.
```

## What mock mode proves, and what it doesn't
Mock mode exercises the whole graph: routing, tool contracts, scoping, limits, tracing,
and runbook retrieval. The LLM responses are **scripted**, so a passing mock scenario
proves the *plumbing* produces the right diagnosis for the right evidence. It does not
prove a real model reasons correctly. That is what Phase 5 evals are for.

## Demo transcript
Mock mode, recorded 2026-10-11 with `uv run python -m triage ask "..."`. The last
question was run with `MOCK_APP_STATE=down`. Timestamps are relative to the run.

```text
you> Is the Coach Net app up?

The Coach Net Mule app looks healthy: coachnet-saas-api: RUNNING (2/2 replicas, 0 restarts in 60m).

Evidence:
  - [check_mule_app_status] coachnet-saas-api: RUNNING (2/2 replicas, 0 restarts in 60m)
  - [parse_request] intent=HEALTH_ONLY case=- ticket=- corr=-

Steps used: 1/8   Run: 0a2f6ce2ba62

(trace: traces/0a2f6ce2ba62.jsonl  |  0.01s)

--------------------------------------------------------------------------------

you> Case 00412345 looks like a duplicate, what happened?

Diagnosis: Duplicate Case created without the redispatch flag.

Likely cause: The inbound flow did not treat the second message as a retry/update. Usual reasons:
HubSpot re-fired the webhook (timeout on first ack), or the upsert key was missing or
blank on the second payload, so the flow fell back to insert. A legitimate re-send
should carry the redispatch flag; its absence means this is an unintended duplicate.

Suggested fix:
  1. (WRITE, human) Close the newer Case as duplicate and link it to the original.
  2. If the upsert key was blank: raise with Coach Net; the payload must include the
     ticket id in the agreed field (see API contract). Add to failure mode cnet-003 evidence.
  3. If HubSpot retried after an ack timeout: check Mule app response times around that
     time; consider idempotency on ticket id in the inbound flow (code change, PR to `test`).

Confidence: 61%   Runbook: cnet-001   (fix needs a human: it includes write actions)

Evidence:
  - [check_mule_app_status] coachnet-saas-api: RUNNING (2/2 replicas, 0 restarts in 60m)
  - [parse_request] intent=CASE_TRIAGE case=00412345 ticket=- corr=-
  - [get_sf_case] Case 00412345 [CNET] status=New sync=SYNCED external_id=hs-55501 last_sync=2026-10-12T00:56:21+00:00 redispatch=False DUPLICATE_OF=00412301
  - [search_mule_logs] 6 log entries (0 errors) for coachnet-saas-api; correlation ids: a1b2c3d4, e5f6a7b8
  - [search_runbook] 3 runbook hits: cnet-001 (0.32), cnet-002 (0.23), cnet-004 (0.16)

Steps used: 4/8   Run: 80519d400ced

(trace: traces/80519d400ced.jsonl  |  0.09s)

--------------------------------------------------------------------------------

you> HubSpot ticket hs-55777 failed on the send

Diagnosis: Send failed because the inbound HubSpot payload does not match the API contract. Key log line: APIKIT:BAD_REQUEST required property 'serviceType' is null

Likely cause: The payload violates the API contract: a required property is missing or null, an
enum value is outside the agreed set (e.g. `priority = "Urgent"` where the contract
says `High`), a date is in the wrong format, or a field was renamed on the HubSpot
side.

Suggested fix:
  1. Send Coach Net the exact property and expected value/format from the contract.
     They correct the ticket and re-send (with the redispatch flag if the Case already
     exists; see cnet-001).
  2. If the contract is wrong or the field is genuinely optional: update the RAML/schema
     and the DataWeave default (code change, PR to `test`).

Confidence: 65%   Runbook: cnet-003

Evidence:
  - [check_mule_app_status] coachnet-saas-api: RUNNING (2/2 replicas, 0 restarts in 60m)
  - [parse_request] intent=CASE_TRIAGE case=- ticket=hs-55777 corr=-
  - [get_sf_case] get_sf_case failed: NotFoundError: no CNET case found for hs-55777
  - [search_mule_logs] 4 log entries (2 errors) for coachnet-saas-api; correlation ids: bad0cafe
  - [search_runbook] 3 runbook hits: cnet-003 (0.39), cnet-005 (0.15), cnet-002 (0.14)

Steps used: 4/8   Run: 5e5dd6ab6b32

(trace: traces/5e5dd6ab6b32.jsonl  |  0.09s)

--------------------------------------------------------------------------------

you> Look at case 00499999

I found nothing for 00499999 in Coach Net (CNET) data:
  - get_sf_case: NotFoundError: no CNET case found for 00499999
  - search_mule_logs: NotFoundError: no log entries matched the given identifiers in the time window

No diagnosis is possible without a case or log transaction. Check the number, that
it is a Coach Net case, and the time window. If a lookup timed out or
failed upstream, try again shortly.

Evidence:
  - [check_mule_app_status] coachnet-saas-api: RUNNING (2/2 replicas, 0 restarts in 60m)
  - [parse_request] intent=CASE_TRIAGE case=00499999 ticket=- corr=-
  - [get_sf_case] get_sf_case failed: NotFoundError: no CNET case found for 00499999
  - [search_mule_logs] search_mule_logs failed: NotFoundError: no log entries matched the given identifiers in the time window

Steps used: 3/8   Run: c112805ccbab

(trace: traces/c112805ccbab.jsonl  |  0.01s)

--------------------------------------------------------------------------------

you> Case 00412400 didn't sync

ALERT: the Coach Net Mule app is not healthy.
  coachnet-saas-api: STOPPED (0/2 replicas, 0 restarts in 60m)
  Last deploy: 2026-10-11T00:56:28+00:00
  Detail: Application stopped; 0 of 2 replicas running

Any sync problem reported right now is most likely caused by this. Case lookup was
skipped. Next step (human, in Runtime Manager): check deployment logs, then
redeploy or restart. See runbook cnet-007.

Evidence:
  - [check_mule_app_status] coachnet-saas-api: STOPPED (0/2 replicas, 0 restarts in 60m)

Steps used: 1/8   Run: 6fe762df78f3

(trace: traces/6fe762df78f3.jsonl  |  0.01s)
```

### What the trace shows for the duplicate-case run
Tool calls and run boundaries from `traces/<run_id>.jsonl` (node and LLM events omitted
here; they are in the file). Inputs are redacted before they are written.

```json
{"run_id": "80519d400ced", "event": "run_start", "request": "Case 00412345 looks like a duplicate, what happened?", "mode": "mock", "partner": "CNET", "step_cap": 8, "run_budget_seconds": 60.0}
{"event": "tool_call", "tool": "check_mule_app_status", "inputs": {"restart_window_minutes": 60}, "output_summary": "coachnet-saas-api: RUNNING (2/2 replicas, 0 restarts in 60m)", "latency_ms": 0, "error": null}
{"event": "tool_call", "tool": "get_sf_case", "inputs": {"case_number": "00412345", "external_id": null}, "output_summary": "Case 00412345 [CNET] status=New sync=SYNCED external_id=hs-55501 last_sync=2026-10-12T00:56:21+00:00 redispatch=False DUPLICATE_OF=00412301", "latency_ms": 0, "error": null}
{"event": "tool_call", "tool": "search_mule_logs", "inputs": {"correlation_id": "e5f6a7b8", "case_number": "00412345", "external_id": "hs-55501", "start": "2026-10-11T02:56:21.661468Z", "end": "2026-10-12T02:56:21.661468Z", "max_entries": 200}, "output_summary": "6 log entries (0 errors) for coachnet-saas-api; correlation ids: a1b2c3d4, e5f6a7b8", "latency_ms": 0, "error": null}
{"event": "tool_call", "tool": "search_runbook", "inputs": {"query": "Case 00412345 looks like a duplicate, what happened? . sync status SYNCED . duplicate of 00412301, same HubSpot ticket created twice, redispatch False . no errors in logs, responded 200 to HubSpot", "k": 3}, "output_summary": "3 runbook hits: cnet-001 (0.32), cnet-002 (0.23), cnet-004 (0.16)", "latency_ms": 77, "error": null}
{"run_id": "80519d400ced", "event": "run_end", "elapsed_ms": 93, "steps_used": 4, "incomplete": false, "incomplete_reason": null, "matched_runbook_ids": ["cnet-001"], "confidence": 0.61}
```

## Tests
```bash
uv run pytest -q        # 145 tests, ~4s, mock mode, no network
```
Covers: health-check short-circuit, step cap and time budget, fail-closed scoping, tool
error handling, trace redaction, the LLM guards, runbook loading and retrieval, and one
scenario per seed runbook entry, plus the LangSmith flag (off wins, one trace per run,
redaction on upload).

## Docs
- [docs/decisions/](docs/decisions/) - ADRs
- [docs/runbook/](docs/runbook/) - known failure modes (RAG source)
- [docs/glossary.md](docs/glossary.md)
- [docs/roadmap.md](docs/roadmap.md)
- [coachnet-triage-agent-kickoff.md](coachnet-triage-agent-kickoff.md) - original brief
