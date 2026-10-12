# ADR 0007: Slack interface over Socket Mode, fail-closed, edge-triggered alerts

**Status:** accepted (2026-10-11)

## Context
Phone access is the original pain point: verifying "case X didn't sync" was close to
impossible away from a desk. Slack is where the team already is. The roadmap put a
Slack bot and a health poller in Phase 6. Dustin asked for a mock-mode version first,
in a personal workspace, to demo before any TrxNow approval or credential exists.

## Decision
- **The bot is a thin interface over `run_triage`.** Same graph, tools, limits,
  guards, and JSONL traces as the CLI. No agent code changed; the run's answer kind is
  classified once (`response_kind`) and rendered by both the CLI and Slack.
- **Socket Mode.** The bot opens an outbound websocket to Slack. No public URL, no
  inbound firewall rule; identical on a laptop, behind the corporate network, or on
  Azure.
- **Fail-closed access, by where the answer lands.** Answers post only in an
  allowlisted channel or in a DM with an allowlisted user. An allowlisted user cannot
  summon answers into another channel (found by the review sweep: an "allowed channel
  OR allowed user" rule would have let case details land in a public channel). Both
  allowlists empty means nobody. Refusals are private and never run the agent. Only
  allowlisted users can use the mock `:app` switch.
- **Abuse limits.** One in-flight run per user, an hourly cap per user, and a global
  concurrency cap.
- **Slack's 3-second rule.** Ack immediately, post "Looking into it", run, then replace
  that message with the answer and put the evidence in its thread.
- **Escape everything.** All dynamic text (user input, case data, log-derived text,
  model output) is escaped before it reaches Slack, so injected `<!channel>` or links
  cannot ping people or render.
- **Minimal scopes.** `app_mentions:read`, `chat:write`, `commands`, `im:history`. The
  bot cannot read channel history.
- **Edge-triggered health alerts.** One message when the app becomes unhealthy, one on
  recovery with the outage duration, and one when the status check itself fails three
  times in a row. Same `is_healthy` predicate as the agent (ADR 0002).
- **Audit.** The JSONL `run_start` records `interface=slack` and the requester's Slack
  user id.

## Why
- Keeping Slack thin means the pitch demo, the CLI, and production all exercise the
  same, tested agent.
- Answers contain case details. Fail-closed access and a private channel are the
  minimum before real data.
- Alert fatigue kills alert channels; edge-triggering keeps every message meaningful.

## Consequences
- TrxNow's Slack needs: workspace-admin approval of a separate app, approval for case
  data in Slack (and its retention), and an always-on host (Azure, Phase 6).
- Rate-limit and in-flight state is in memory: fine for one process; a multi-replica
  deployment would need a shared store.
- Tests drive the handlers with a fake client and Bolt with an offline authorize
  function; no test touches Slack.
