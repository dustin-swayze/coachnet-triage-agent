# ADR 0006: JSONL traces are the record; LangSmith is optional and off by default

**Status:** accepted (2026-10-12)

## Context
Logging and usage metrics matter for this project: the director needs to see exactly
what the agent did, and model choice (Sonnet vs Haiku) will be made on cost, latency,
and eval pass rate. LangSmith (LangChain's hosted tracing) gives run trees, token/cost
dashboards, and eval tooling with almost no code, because LangGraph and LangChain
emit traces natively. It also uploads prompts and outputs, which in live mode contain
case fields and Mule log lines, to LangChain's cloud (US or EU). Self-hosting is
Enterprise-only.

## Decision
- **JSONL traces stay the system of record.** Always on, local, redacted at write
  time (`tracing.py`). Nothing about them changes.
- **LangSmith is optional, behind `LANGSMITH_ENABLED`, default `false`.**
  `src/triage/observability.py` owns it.
- **Off is authoritative.** When our flag is off, the library's own switches
  (`LANGSMITH_TRACING` and legacy names) are forced to `false` in-process and the run
  executes in a disabled tracing context. A probe showed that a stray
  `LANGSMITH_TRACING=true` in the shell otherwise starts uploads even when the code
  says "disabled."
- **On requires a key** (startup validation) and every outbound payload goes through
  the same redaction as the JSONL traces (`Client(hide_inputs=mask, hide_outputs=mask)`).
- **One run = one trace.** Tools are wrapped with `traceable(run_type="tool")` and
  executed with `contextvars.copy_context()` in the timeout pool, so they nest under
  their graph node. The LangSmith run carries our JSONL `run_id` as metadata, and the
  JSONL `run_start` event records whether LangSmith was on.

## Why
- The audit trail must not depend on a third-party service being approved, reachable,
  or retained. LangSmith's base retention is 14 days; our files are ours.
- LangSmith is still the cheapest way to get a good UI and eval tooling (free tier: one
  seat, 5k traces/month; one triage run is one trace).
- Flag-gated means each environment opts in deliberately: free on the home PC with
  synthetic data; at TrxNow only after the data-sharing question is approved.

## Consequences
- Before enabling with real case data: team-lead approval (same conversation as the
  Anthropic API approval). If data cannot leave TrxNow, Langfuse (open source,
  self-hostable on Azure) is the drop-in alternative to evaluate.
- Tests replace the LangSmith client with a recorder; no test touches the network.
