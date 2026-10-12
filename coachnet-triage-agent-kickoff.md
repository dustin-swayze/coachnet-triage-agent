# Coach Net Integration Triage Agent — Kickoff Brief (v2)

## 0. How to start (for Dustin)

**Machine:** home desktop (DSPC). **Shell:** Git Bash. **Directory:** any.

```bash
mkdir -p /c/Projects/coachnet-triage-agent
cd /c/Projects/coachnet-triage-agent
git init -b main
```

1. Move this file into `C:\Projects\coachnet-triage-agent\`.
2. Open that folder in the Claude Desktop app → Code tab.
3. Set the model picker to **Fable 5.1**.
4. Paste this kickoff line:

> `Read coachnet-triage-agent-kickoff.md and run Session 1, starting with Part A. Ask me the open questions first.`

The session will tell you when to switch the model to **Opus 5.5**. You can't automate that switch; the session will stop and ask.

---

## 1. Context

I'm Dustin, a Salesforce developer (Apex, LWC, integrations) at TrxNow. TrxNow's Salesforce org integrates with a partner, **Coach Net**, whose HubSpot instance syncs with Salesforce through a **MuleSoft** app (the "Coach Net API application", listed as CoachNET SaaS API in API Manager, running on Anypoint / CloudHub 2.0 in a Private Space).

**The pain:** When someone reports "Case X didn't sync," verifying it means jumping between Anypoint Runtime Manager, Mule logs, and Salesforce. That's slow, and nearly impossible from my phone. I also want to know instantly if the Mule app is down.

**The goal:** A homegrown, read-only AI triage agent. I ask it in plain English ("Case 123 isn't in HubSpot, what happened?"), it checks app health, pulls the case, finds the Mule transaction and errors, matches against a runbook of known issues, and replies with a diagnosis and a suggested fix in about 30 seconds.

This is also a learning project. I'm building GenAI-architect skills (agent loops, tool design, RAG, evals, failure handling), so **explain architectural decisions as you make them**, briefly.

It's also a pitch to my team lead and director, so the code and docs should be demo-quality.

**Where this runs:** Tonight is my home PC, mock mode only. The project will move to my **work laptop** and **TrxNow's GitHub** before any real credentials, case data, or log formats touch it. Tonight's session ends by writing the handoff for that move.

## 2. Non-negotiable constraints

1. **Read-only.** No tool may create, update, or delete anything in Salesforce, Anypoint, or HubSpot. Not in v1, not "just for testing."
2. **Fail-closed scoping.** If a tool can't confirm it is scoped to Coach Net data, it returns an error, never unscoped results. This is the same invariant as my Priority Queue project.
3. **No real TrxNow data in this repo.** Fixtures and mocks use synthetic data only. Secrets live in `.env` (gitignored) and are loaded via config, and `.env.example` documents every variable. Never print secrets in logs or traces.
4. **Least-privilege credentials.** Each real integration gets its own read-only credential (a Salesforce integration user with read-only perms, and an Anypoint connected app with view-only scopes).
5. **Bounded agent.** Hard cap on loop steps (e.g. 8), per-tool timeouts, and a total time budget. On hitting a limit, it returns what it found plus "incomplete," never hangs.
6. **Every tool call is traced.** Log tool name, inputs (redacted), output summary, latency, and error to a JSONL trace file per run, so I can show my director exactly what it did.
7. **Swappable LLM provider.** Provider and model come from config (see 3.1).
8. **Portable to Agentforce later.** Keep tools as plain, well-typed functions with clear input/output schemas, separate from orchestration. The tools and runbook are the reusable assets; the orchestration layer may get replaced.
9. **Zero cost in mock mode.** Mock mode must run the full graph with **no API key**, using LangChain's fake/scripted chat model with canned responses per node.

## 3. Architecture

- **Language:** Python 3.12, `uv` for env and deps.
- **Orchestration:** LangGraph 1.x (explicit `StateGraph` with typed state).
- **LangChain's role:** Use LangChain 1.x for the building blocks, not the orchestration: `init_chat_model` for the swappable provider, `@tool` definitions with Pydantic schemas, and its vector-store integration for the runbook. LangGraph owns the flow. Write an ADR explaining why we use an explicit `StateGraph` instead of LangChain's `create_agent` (answer: the deterministic health-check-first step), and note that `create_agent` is the fast path for simpler agents.
- **Schemas:** Pydantic models for every tool input and output.
- **Interface:** CLI chat first (`uv run python -m triage chat`). Slack comes later.
- **Runbook search:** Start with a local vector store (sqlite-vec or Chroma). Design the interface so it can move to Postgres + pgvector later. In mock mode, use a fake embedding model so indexing is free too.
- **Tests:** pytest. Every tool has a mock implementation and a real one behind the same interface, selected by config (`TRIAGE_MODE=mock|live`).

### 3.1 Runtime model config

This is the model *inside the agent*, which is separate from the Claude Code session model.

| Setting | Default | Notes |
|---|---|---|
| `TRIAGE_MODE` | `mock` | `mock` uses the fake chat model + fake embeddings; no key needed |
| `LLM_PROVIDER` | `anthropic` | or `openai`, depending on TrxNow policy (`TODO(dustin)`) |
| `LLM_MODEL` | `claude-sonnet-5-5` | try `claude-haiku-5-5` once evals exist; keep whichever passes |

### Design principle: deterministic where possible, LLM only where judgment is needed

The health check is *not* an LLM decision. It always runs first, in code. If the app is down, short-circuit with an alert. The LLM's job is interpreting logs and errors, matching them to runbook entries, and writing the explanation.

### Graph (v1)

```
start
  → health_check            (deterministic; app down → alert + end)
  → parse_request           (LLM: extract case number / time window / symptom)
  → get_case                (tool)
  → find_transaction        (tool: search Mule logs by case id / correlation id)
  → diagnose                (LLM + search_runbook tool; may loop within step cap)
  → respond                 (LLM: diagnosis, evidence, suggested fix, confidence)
end
```

### Tools (v1, all read-only)

| Tool | Live source | Returns |
|---|---|---|
| `check_mule_app_status()` | Anypoint Runtime Manager / CloudHub 2.0 API | status, replicas, last deploy, recent restarts |
| `get_sf_case(case_number)` | Salesforce REST API (read-only integration user) | case fields + sync-related fields (external IDs, last sync time, sync status) |
| `search_mule_logs(case_id, start, end)` | Anypoint logs API (exact endpoint depends on our setup, so confirm with me) | matching log lines, correlation IDs, errors |
| `search_runbook(query, k)` | local vector store over `docs/runbook/*.md` | top-k known issues with symptoms, cause, fix |

## 4. Knowledge base lives in the repo

This project doubles as my template for keeping project knowledge independent of any chat session. Create and maintain:

```
CLAUDE.md                  # project memory for Claude Code: purpose, constraints, commands, conventions
README.md                  # human-facing overview + how to run the demo
HANDOFF.md                 # written at the end of Session 1 (see Part C)
.env.example
docs/
  decisions/               # ADRs: one short file per architectural decision
  runbook/                 # one file per known failure mode (this is the RAG source of truth)
    _template.md
  glossary.md              # agent / RAG / vector DB / A2A / MCP terms as used in this project
  roadmap.md               # phases below, with status
```

- **Runbook entry format** (`_template.md`): Title, Symptoms (what the user reports and what the logs show), Likely cause, How to confirm, Fix, Owner, Last seen.
- **Rule:** The markdown files are the source of truth. The vector index is derived and can be rebuilt with one command (`uv run python -m triage index`).

## 5. Session 1 (tonight)

### Part A: Kickoff and architecture (model: Fable 5.1)

Use the strongest model for the decisions that are expensive to change later.

1. Ask me the open questions in section 7. Wait for answers. Use placeholders for anything I don't know.
2. Review this brief critically. If anything in the architecture is wrong or weak, say so now, before code exists.
3. Write `CLAUDE.md`, `README.md` (outline is fine), `docs/roadmap.md`, `docs/glossary.md`.
4. Write the ADRs:
   - `0001` — LangGraph `StateGraph` over `create_agent` / a raw loop
   - `0002` — deterministic health check first
   - `0003` — mock/live tool interface with a fake LLM in mock mode
   - `0004` — markdown runbook as source of truth, vector index as a derived artifact
5. Define all Pydantic schemas (tool I/O and graph state) and the graph's node/edge layout, as code stubs with docstrings.
6. Write seed runbook entries from my answers to open question 1.
7. Commit on branch `feature/scaffold`.
8. **Stop and tell me: "Part A done — switch the model to Opus 5.5, then say continue."** Do not start Part B until I confirm.

### Part B: Build (model: Opus 5.5)

1. Mock implementations of all four tools, with synthetic fixtures in realistic shapes, including failure cases that match the seed runbook entries.
2. LangGraph graph wired up: step cap, per-tool timeouts, total time budget, JSONL tracing with redaction.
3. Fake chat model and fake embeddings for mock mode; `init_chat_model` path for live mode.
4. Runbook indexer and the `search_runbook` tool.
5. CLI chat that works end-to-end in mock mode.
6. Tests for:
   - health-check short-circuit
   - step-cap behavior
   - fail-closed scoping
   - tool error handling
   - trace redaction
   - each seed runbook scenario producing the right diagnosis in mock mode
7. Run the tests and a demo conversation; paste the demo transcript into the README.
8. Commit in logical steps.

### Part C: Handoff (model: Opus 5.5)

Write **`HANDOFF.md`**: a complete, standalone kickoff prompt for a *new* Claude Code session on my **work laptop**, where I'll finish the project. Assume that session has never seen this conversation. It must include:

1. **Paste-ready kickoff line** at the top.
2. **Project summary:** goal, constraints (copy section 2 verbatim), architecture, and links to the ADRs.
3. **Current state:** what's built, what's tested, test results, and what's mocked.
4. **Open items:** every `TODO(dustin)`, with what's needed to resolve each one.
5. **Work-laptop setup steps**, each with shell and directory stated. Shell is **Git Bash** unless a step requires PowerShell, in which case say so. Cover:
   - Cloning
   - Installing `uv` if missing
   - `uv sync`
   - Creating `.env` from `.env.example`
   - Running tests
   - Running the mock demo
6. **Move to company GitHub:** steps to add TrxNow's GitHub org as a remote and push, plus a reminder to confirm with my team lead where it should live and that AI-tool use on it is within policy.
7. **Phase plan for the work session:**
   - Phase 2: live `get_sf_case` against a **sandbox**, with a read-only integration user
   - Phase 3: live `check_mule_app_status`
   - Phase 4: live `search_mule_logs`
   - Each phase has acceptance criteria and the security checks to do before going live.
8. **Model guidance for that session:** plan on Opus 5.5, implement on Sonnet 5.5, and use Fable 5.1 only for a final architecture/security review before the pitch.
9. **"Next session starts here"** checklist.

Then update `CLAUDE.md` with a 5-line "next session starts here" note, update `docs/roadmap.md` statuses, and commit.

Finally, give me the commands (Git Bash, from `/c/Projects/coachnet-triage-agent`) to push this repo to a **new private repo on my personal GitHub** as a temporary bridge to the work laptop. This machine uses HTTPS remotes, not SSH. The repo contains only synthetic data, so that's acceptable as a short-lived bridge. Note that it should be deleted once the code is in TrxNow's GitHub.

## 6. Later phases (for the roadmap; don't build tonight)

- **Phase 5:** Evals. Build a set of 10–20 scripted scenarios (known failure → expected diagnosis), run them on every change, and track a pass rate. Use the results to pick between Sonnet and Haiku at runtime.
- **Phase 6:** Interfaces. Add a Slack bot for phone access, plus a scheduled poller that reuses `check_mule_app_status` to alert before anyone files a bug. The likely host is TrxNow's Azure.
- **Phase 7:** Email intake. Allowlisted bug emails get classified, turned into Asana tickets, and run through this agent's diagnosis. Fix drafts go out as PRs to `test`, behind approval. Email content is untrusted (prompt injection), so keep reader and actor privileges split.
- **Backburner:** A conversational dispatch agent that gathers dispatch details by slot-filling, confirms with the user, then invokes existing Salesforce Flows.

## 7. Open questions: ask me these before building

1. What are the top 3–5 ways a Coach Net sync actually fails? (Examples: missing required field, expired auth, HubSpot rate limit, mapping mismatch, Mule app down or restarting.) For each: what do I see, and what's the usual fix?
2. How does a Salesforce Case map to its HubSpot record? Which external-ID / sync-status / last-sync fields exist?
3. Is there a correlation ID that ties a Salesforce Case to a Mule transaction in the logs? If so, where is it?
4. Which LLM provider should the live config default to, given company policy?

If I don't know an answer, make a sensible placeholder, mark it `TODO(dustin)` in code and in `roadmap.md`, and keep going.

## 8. Working style

- I'm comfortable with implementation-level detail. Explain the *why* of architectural choices in a sentence or two, not the basics of Python.
- When you give me terminal commands, say which shell (Git Bash, PowerShell, or WSL Ubuntu) and which directory. Default to Git Bash.
- Branching: work on `feature/<name>` branches. Commit at the end of each logical step with clear messages.
