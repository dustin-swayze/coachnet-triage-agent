# HANDOFF: Partner Sync Triage Agent, work-laptop session

This file is a complete, standalone kickoff for a **new** Claude Code session on
Dustin's **work laptop**. That session has never seen the home-PC conversation that
produced this repo. Everything it needs is here or linked from here.

## 1. Paste-ready kickoff line

Open the cloned repo folder in the Claude Desktop app (Code tab), set the model picker
to **Opus 5.5**, and paste:

> `Read HANDOFF.md and CLAUDE.md, then run the "Next session starts here" checklist in HANDOFF.md section 9. Before Phase 2, read the Coach Net API contract and XMLs in the work repo and propose the exact field mapping. Ask me before touching any credentials.`

---

## 2. Project summary

### Goal
A homegrown, **read-only** AI triage agent for TrxNow's partner integrations. A
partner's HubSpot syncs with TrxNow's Salesforce org through a MuleSoft app (the
"Coach Net API application", listed as CoachNET SaaS API in API Manager, on Anypoint
CloudHub 2.0 in a Private Space). When someone says "Case X didn't sync", the agent
checks app health, pulls the case, finds the Mule transaction and errors, matches
them against a markdown runbook of known issues, and replies with a diagnosis,
evidence, a suggested fix, and a confidence score in about 30 seconds.

First partner: **Coach Net**, identified by Partner Code **`CNET`** on the Case. The
code is partner-generic: partner identity comes from config (`PARTNER_CODE`), not code.

It is also a learning project (agent loops, tool design, RAG, evals) and a pitch to
Dustin's team lead and director, so code and docs are demo-quality.

### Constraints (copied verbatim from the kickoff brief, section 2)

1. **Read-only.** No tool may create, update, or delete anything in Salesforce, Anypoint, or HubSpot. Not in v1, not "just for testing."
2. **Fail-closed scoping.** If a tool can't confirm it is scoped to Coach Net data, it returns an error, never unscoped results. This is the same invariant as my Priority Queue project.
3. **No real TrxNow data in this repo.** Fixtures and mocks use synthetic data only. Secrets live in `.env` (gitignored) and are loaded via config, and `.env.example` documents every variable. Never print secrets in logs or traces.
4. **Least-privilege credentials.** Each real integration gets its own read-only credential (a Salesforce integration user with read-only perms, and an Anypoint connected app with view-only scopes).
5. **Bounded agent.** Hard cap on loop steps (e.g. 8), per-tool timeouts, and a total time budget. On hitting a limit, it returns what it found plus "incomplete," never hangs.
6. **Every tool call is traced.** Log tool name, inputs (redacted), output summary, latency, and error to a JSONL trace file per run, so I can show my director exactly what it did.
7. **Swappable LLM provider.** Provider and model come from config (see 3.1).
8. **Portable to Agentforce later.** Keep tools as plain, well-typed functions with clear input/output schemas, separate from orchestration. The tools and runbook are the reusable assets; the orchestration layer may get replaced.
9. **Zero cost in mock mode.** Mock mode must run the full graph with **no API key**, using LangChain's fake/scripted chat model with canned responses per node.

("Coach Net data" in constraint 2 is generalised in code to "the configured partner's
data"; see ADR 0005.)

### Architecture
Python 3.12, `uv`, LangGraph 1.x `StateGraph` for orchestration, LangChain 1.x for
building blocks (`init_chat_model`, `@tool`, Chroma vector store), Pydantic v2 for
every tool input/output and the graph state, pytest.

```
START -> health_check --(unhealthy)--> respond -> END          deterministic, no LLM
             | healthy / unknown
             v
        parse_request --(health-only / no case ref)--> respond  LLM structured output + guard
             | case ref
             v
          get_case -> find_transaction --(no case AND no logs)--> respond
                             |
                             v
                          diagnose <--> diagnose_tools          LLM loop: search_runbook,
                             |                                  ends with Diagnosis tool
                             v
                          respond -> END                        template, no LLM
```

| Tool | Live source | Mock |
|---|---|---|
| `check_mule_app_status` | Anypoint Runtime Manager / CloudHub 2.0 | `tools/mock/app_status.py` |
| `get_sf_case` | Salesforce REST, read-only integration user | `tools/mock/sf_case.py` |
| `search_mule_logs` | Runtime Manager logs (logs stay in Runtime Manager) | `tools/mock/logs.py` |
| `search_runbook` | local Chroma index over `docs/runbook/*.md` | same (local in both modes) |

Key files: `src/triage/graph/nodes.py` (all node logic, limits, guards),
`src/triage/graph/build.py` (the whole control flow), `src/triage/tools/base.py`
(tool interfaces and errors), `src/triage/tools/scoping.py` (fail-closed helpers),
`src/triage/schemas/` (all Pydantic models), `src/triage/tracing.py` (JSONL + redaction).

### Decisions (read these before changing architecture)
- [ADR 0001](docs/decisions/0001-langgraph-stategraph-over-create-agent.md): explicit `StateGraph` over `create_agent` or a raw loop (plus build-time amendments: template `respond`, no-evidence route, final-answer-as-a-tool, guards)
- [ADR 0002](docs/decisions/0002-deterministic-health-check-first.md): deterministic health check first
- [ADR 0003](docs/decisions/0003-mock-live-tools-and-fake-llm.md): mock/live tool interface, node-keyed scripted model, hashing embeddings
- [ADR 0004](docs/decisions/0004-markdown-runbook-source-of-truth.md): markdown runbook is the source of truth, vector index is derived
- [ADR 0005](docs/decisions/0005-partner-scoping-fail-closed.md): partner-generic, fail-closed scoping

---

## 3. Current state (end of Session 1, home PC)

### Built
- Full LangGraph agent running end to end in **mock mode** with no API key.
- Step cap, per-tool timeout, total time budget; limits end the run in `respond` with
  an `INCOMPLETE` banner and the partial evidence.
- JSONL trace per run in `traces/` with secret, token, and PII-key redaction.
- Mock tools with synthetic fixtures for all seven seed runbook scenarios, plus
  scoping, timeout, upstream-error, healthy, and prompt-injection fixtures.
- Runbook RAG: loader, hashing embedder, Chroma index, partner-filtered search.
- CLI: `chat`, `ask`, `index`, `health`.
- Seven seed runbook entries in `docs/runbook/` (synthetic log shapes; see open items).

### Tested
`uv run pytest -q`: **138 passed in ~4s** (Windows 11, Python 3.12.1, mock mode, no
network). `uv run ruff check src tests`: clean.

| Module | Tests | Protects |
|---|---|---|
| `test_health_short_circuit.py` | 10 | app down/deploying/restart-loop ends the run after one tool call, no LLM; a failing status check does not block triage |
| `test_step_cap.py` | 5 | step cap and time budget end the run with `INCOMPLETE` and partial evidence, never hang |
| `test_scoping.py` | 29 | fail-closed scoping: other-partner case is NotFound without leaking its partner; wrong field name is ScopeError; no bare log dumps |
| `test_tool_errors.py` | 13 | upstream errors, timeouts, tool bugs, missing index, LLM failure, and both hallucination guards |
| `test_trace_redaction.py` | 34 | secrets, tokens, JWTs, PII keys scrubbed; trace file structure; usage counts survive |
| `test_scenarios.py` | 19 | each seed scenario reaches the right runbook entry; prompt-injection framing present |
| `test_runbook.py` | 28 | loader validation, deterministic embeddings, each entry retrieves itself |

Two real bugs were found by the tests and fixed in Session 1: token-usage counts were
being redacted, and the parse guard lost a real case number when the model invented
another one.

### Mocked (nothing live exists yet)
- All three integration tools. `tools/live/__init__.py` raises `NotImplementedError`.
- The LLM: `ScriptedChatModel` with heuristic responders that read the real prompt.
  **Mock scenario tests prove plumbing and retrieval, not model reasoning.**
- Live mode has never been run. `init_chat_model` wiring exists in `src/triage/llm.py`
  but is untested against a real provider.

---

## 4. Open items: every `TODO(dustin)`

Search them with `grep -rn "TODO(dustin)" src docs .env.example`.

| # | Where | What is needed to resolve it |
|---|---|---|
| 1 | `.env.example`, `config.py` `sf_case_*_field` | Real Case field API names for partner code, HubSpot external id, sync status, last sync time, Mule correlation id, redispatch flag. **Source: the API contract and XMLs in the work repo.** |
| 2 | same | Where the correlation id lives: Case field, log MDC key, HTTP header, or several. Same source. |
| 3 | `.env.example` `MULE_APP_NAME`, `ANYPOINT_*` | Exact deployment name in Runtime Manager, org id, environment id, Private Space id. |
| 4 | Phase 4 | CloudHub 2.0 logs endpoint, retention window, filters, and whether inbound HubSpot payloads are logged (failure mode cnet-003 depends on it). |
| 5 | `.env.example` `SF_*` | Salesforce auth flow for the integration user. JWT bearer preferred over username/password. |
| 6 | `docs/runbook/*` | Seed entries use invented log wording. Replace with real shapes (still synthetic values) once real logs are visible. Fill `last_seen`. |
| 7 | `docs/runbook/02-*` | How the Priority Queue selects cases (object, fields, report), for the "sent but not in queue" check. |
| 8 | `config.py` `llm_provider` | Anthropic is the intended live provider; confirm with the team lead that it is within policy. |
| 9 | `config.py` `embeddings_provider` | Keep local hashing embeddings, or Voyage / OpenAI. Decide from Phase 5 eval results. |
| 10 | `tracing.py` `PII_KEYS` | Which Case fields are PII, so traces and prompts redact them. |
| 11 | `llm.py` `TICKET_RE`, `schemas/tools.py` | Real HubSpot ticket id format (fixtures use synthetic `hs-NNNNN`). |
| 12 | `schemas/tools.py` `SyncStatusValue` | Map logical sync states to the real picklist values. |

---

## 5. Work-laptop setup

Every step: **Shell: Git Bash** unless marked PowerShell. Directory stated per step.

1. **Clone** (Git Bash, any parent folder, e.g. `/c/Projects`):
   ```bash
   mkdir -p /c/Projects && cd /c/Projects
   git clone https://github.com/<personal-account>/coachnet-triage-agent.git
   cd coachnet-triage-agent
   git checkout feature/scaffold   # main holds the same commits; work continues on feature branches
   ```
2. **Install `uv` if missing.** Check first (Git Bash, any directory): `uv --version`.
   If it is missing, either:
   - **PowerShell** (any directory), the official installer:
     ```powershell
     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
     ```
   - or Git Bash, if company policy prefers PyPI: `python -m pip install --user uv`,
     then run it as `python -m uv ...`.
   Close and reopen Git Bash so `uv` is on PATH. Confirm the install method is allowed
   on a managed laptop.
3. **Install dependencies** (Git Bash, `/c/Projects/coachnet-triage-agent`):
   ```bash
   uv sync
   ```
   `uv` installs Python 3.12 itself if needed (pinned in `.python-version`).
4. **Create `.env`** (Git Bash, repo root):
   ```bash
   cp .env.example .env
   ```
   Leave `TRIAGE_MODE=mock` until a phase below says otherwise. Never commit `.env`.
5. **Run the tests** (Git Bash, repo root):
   ```bash
   uv run pytest -q
   ```
6. **Run the mock demo** (Git Bash, repo root):
   ```bash
   uv run python -m triage index
   uv run python -m triage ask "Case 00412345 looks like a duplicate, what happened?"
   uv run python -m triage chat
   ```

---

## 6. Move to TrxNow's GitHub

**Before pushing anything:** confirm with the team lead (a) which TrxNow GitHub org
and repo name it should live under, (b) that using AI coding tools on this repo is
within company policy, and (c) whether it must be private/internal.

Then (Git Bash, repo root):
```bash
git remote rename origin personal
git remote add origin https://github.com/<trxnow-org>/<repo-name>.git
git push -u origin main
git push -u origin feature/scaffold
git remote remove personal
```
Afterwards, **delete the temporary personal GitHub repo** (GitHub web: repo Settings >
Danger Zone > Delete). It only ever held synthetic data, but it should not outlive the
bridge.

---

## 7. Phase plan for the work session

Security checks that apply before **any** live tool goes on (repeat per phase):
1. The credential is dedicated (integration user / connected app), not a person's login.
2. It is read-only / view-only. **Prove it**: attempt one harmless write in the
   sandbox with that credential and confirm it is refused. Record the result.
3. Secrets live only in `.env` as `SecretStr` settings; grep the trace files after a
   live run to confirm none appear.
4. Scoping filter is in the query **and** re-checked on the result (`tools/scoping.py`).
5. HTTP client timeouts are set (httpx) in addition to the graph's per-tool timeout.
6. Response bodies are truncated and redacted before they enter state or traces.
7. A live-mode test exists that is skipped unless `TRIAGE_MODE=live` and creds exist.

### Phase 2: live `get_sf_case` against a **sandbox**
- Read the API contract and XMLs; update `sf_case_*_field` defaults and the
  `CaseRecord` mapping; resolve open items 1, 2, 11, 12.
- Create a read-only permission set (Case read; only the needed fields) and an
  integration user; connected app with JWT bearer flow.
- Implement `tools/live/sf_case.py` using `soql_case_filter` + `assert_case_in_scope`.
- **Acceptance:** a real sandbox CNET case returns a valid `CaseRecord`; a non-CNET
  case returns `NotFoundError` without revealing it exists; a wrong field name returns
  `ScopeError`; the write attempt with the credential is refused.

### Phase 3: live `check_mule_app_status`
- Anypoint connected app with view-only Runtime Manager scopes; resolve open item 3.
- Implement via the CloudHub 2.0 deployments API (status, replicas, last deploy,
  restarts). Reuse `AppStatus.is_healthy` unchanged.
- **Acceptance:** `uv run python -m triage health` matches Runtime Manager for the app;
  the credential cannot stop, start, or deploy.

### Phase 4: live `search_mule_logs`
- Resolve open item 4 (endpoint, retention, filters, payload logging).
- Implement OR-matching on correlation id / case number / external id, expanding to
  full transactions like the mock.
- **Acceptance:** for a known failed sandbox case, the tool returns the transaction
  lines and the error; a request with no identifier is refused by schema.

Then replace the synthetic log wording in the runbook (open item 6) and start
Phase 5 evals (see `docs/roadmap.md`).

---

## 8. Model guidance for the work session
- **Plan on Opus 5.5.** Field mapping from the contract, live tool design, security review of each phase.
- **Implement on Sonnet 5.5.** Live tool code, tests, fixture updates. Use Haiku 5.5 subagents for read-only sweeps (TODO audits, data-leak scans).
- **Use Fable 5.1 only** for a final architecture and security review before the pitch.

The agent's own runtime model is separate: `LLM_MODEL=claude-sonnet-5-5` by default;
try `claude-haiku-5-5` once Phase 5 evals exist and keep whichever passes.

---

## 9. Next session starts here

- [ ] Clone, `uv sync`, `cp .env.example .env`, `uv run pytest -q` passes (section 5).
- [ ] Mock demo runs: `uv run python -m triage ask "Case 00412345 looks like a duplicate, what happened?"`.
- [ ] Team lead confirms GitHub location and AI-tool policy; push to TrxNow GitHub; delete the personal bridge repo (section 6).
- [ ] Read the Coach Net API contract and XMLs; write the real field mapping into `.env.example` and `config.py`; close open items 1, 2, 11, 12.
- [ ] Request the sandbox read-only integration user and Anypoint view-only connected app.
- [ ] Phase 2: live `get_sf_case` against the sandbox, with its security checks and acceptance criteria.
- [ ] Update `docs/roadmap.md` statuses and the "Next session starts here" note in `CLAUDE.md` at the end of the session.
