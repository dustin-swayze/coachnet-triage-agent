# Session prompts

Paste-ready prompts for picking this project back up. Both assume the repo state at the
end of 11 October 2026: Session 1 complete, LangSmith flag added, mock Slack bot built,
221 tests passing, everything pushed to `main` on the private bridge repo
`github.com/dustin-swayze/coachnet-triage-agent`.

---

## A. Home PC: pick up where we left off

Use in the same Claude Code session on the home PC (DSPC), or a new one opened on
`C:\Projects\coachnet-triage-agent`. Model: **Opus 5.5**.

```text
Pick up the Coach Net triage agent project in C:\Projects\coachnet-triage-agent. Read CLAUDE.md first.

Where we are: Session 1 is complete, plus optional LangSmith tracing (off by default, ADR 0006) and a mock-mode Slack bot with health alerts (ADR 0007). 221 tests pass. Branch feature/slack-bot; main is identical and pushed to the private bridge repo github.com/dustin-swayze/coachnet-triage-agent. On this PC, uv is not on PATH: prefix commands with
export PATH="/c/Users/Dustin/AppData/Local/Packages/PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0/LocalCache/local-packages/Python313/Scripts:$PATH"

The Slack bot has never connected to a real workspace. Tonight's goal is the live Slack demo:
1. I am creating a free personal Slack workspace and the app from slack/manifest.yaml, following docs/slack-setup.md. I will put the tokens and ids in .env myself. Never ask me to paste tokens into chat, and never print .env.
2. When I say ready: run the tests, start the bot with `uv run python -m triage slack` in the background, and watch its output while I try /triage help, a case question, an @mention, a DM, and the :app down / :app healthy outage demo from my phone.
3. Fix anything that breaks, with a test for each fix. Commit on a feature branch, fast-forward main, push.
4. Update the README demo section and docs/overview.html (republish the private overview artifact) with what the live demo showed.

Working style: Opus for core changes, Sonnet subagents for well-specified implementation and tests, Haiku subagents for read-only sweeps. Subagents never commit. State the shell and directory for any command you give me. When done, tell me where things stand and remind me the next step is the work laptop (docs/session-prompts.md, prompt B).
```

---

## B. Work laptop: first session

Use in a brand-new Claude Code session on the work laptop. It has never seen the home
PC conversation. Model: **Opus 5.5**.

Before pasting, in **Git Bash**:

```bash
mkdir -p /c/Projects && cd /c/Projects
git clone https://github.com/dustin-swayze/coachnet-triage-agent.git
cd coachnet-triage-agent
git checkout main
```

Then open `C:\Projects\coachnet-triage-agent` in the Claude Desktop app (Code tab) and paste:

```text
This is the Partner Sync Triage Agent: a read-only AI agent that diagnoses why a Coach Net case did not sync between HubSpot and TrxNow's Salesforce through our MuleSoft app on CloudHub 2.0. It was built in mock mode on my home PC; this laptop is where it goes live. You have not seen any of that work, so get up to date from the repo before doing anything.

1. Read, in order: HANDOFF.md (complete handoff), CLAUDE.md (project memory and conventions), docs/roadmap.md (phases and every TODO(dustin)), and the ADRs in docs/decisions/ (0001 to 0007). docs/overview.html is a wiki-style overview if you want the whole picture in one page. Then give me a short summary of the architecture, what is built, what is mocked, and the open items, so I know you have it.

2. Set up and verify, telling me the shell and directory for each step: install uv if missing (check company policy for the install method), uv sync, cp .env.example .env (leave TRIAGE_MODE=mock), uv run pytest -q (expect 221 passed), and the mock demo: uv run python -m triage ask "Case 00412345 looks like a duplicate, what happened?"

3. Help me prepare the approvals I need from my team lead, as a short checklist I can send: where the repo lives in TrxNow's GitHub, AI-tool use on it, Anthropic as the agent's live model provider, whether LangSmith may receive redacted case data, and a Slack app in TrxNow's workspace (admin approval, case details in Slack, hosting). Do not push anywhere until I confirm the approvals.

4. Once I confirm: add TrxNow's GitHub as the remote and push (HANDOFF section 6), and remind me to delete the personal bridge repo.

5. Then Phase 2 prep: read the Coach Net API contract and the associated XMLs in our work repo (I will point you to them), and propose the exact Salesforce field mapping for partner code, HubSpot ticket id, sync status, last sync time, Mule correlation id, and redispatch flag, plus where the correlation id appears in Mule logs. Propose it; do not change code until I agree. Also draft the requests for a read-only Salesforce sandbox integration user (JWT bearer) and a view-only Anypoint connected app.

Non-negotiables (full text in HANDOFF section 2): every tool is read-only; scoping is fail-closed (filter in the query and recheck the result); no real TrxNow data ever goes into the repo (fixtures stay synthetic); secrets live only in .env and are never printed, logged, or pasted into chat; prove each credential is read-only in the sandbox before a tool goes live; ask me before touching any credentials.

Working style: plan and review on Opus; delegate well-specified implementation and tests to Sonnet subagents and read-only sweeps (secret scans, TODO audits) to Haiku subagents; subagents never commit, you do, in logical steps on feature/<name> branches. Explain the why of architectural choices in a sentence or two. Mark anything unknown about our org as TODO(dustin) in code and in docs/roadmap.md. At the end of the session, update docs/roadmap.md, the "Next session starts here" note in CLAUDE.md, and HANDOFF.md.
```
