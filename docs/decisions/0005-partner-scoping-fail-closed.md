# ADR 0005: Partner-generic, fail-closed scoping

**Status:** accepted (2026-10-11)

## Context
The brief named this a Coach Net agent. Dustin clarified: all Coach Net cases carry
Partner Code `CNET`, and the agent should start with Coach Net but not be hard-coded
to it. Constraint 2 says any tool that cannot prove it is scoped to the partner's data
must return an error, not unscoped results. The same invariant exists in Dustin's
Priority Queue project.

## Decision
- `PARTNER_CODE` (and display name `PARTNER_NAME`) come from config. An empty
  `PARTNER_CODE` fails at startup, before any tool can run.
- The Salesforce field that holds the partner code is config too
  (`SF_CASE_PARTNER_FIELD`, placeholder `Partner_Code__c`, `TODO(dustin)`).
- `get_sf_case` **must** build its query with the partner filter
  (`... WHERE CaseNumber = :n AND <partner_field> = :code`). If the filter cannot be
  built, the tool raises `ScopeError`. After the query, the returned record's partner
  field is checked again; a mismatch or missing field raises `ScopeError`. Belt and
  braces: the filter prevents the leak, the post-check catches a wrong field name.
- `search_mule_logs` is scoped by the Mule app name from config (`MULE_APP_NAME`) and
  by requiring at least one identifier (correlation id or case number). A request with
  neither raises `ScopeError`; the tool never does a bare time-window dump.
- `check_mule_app_status` is scoped by `MULE_APP_NAME`. Status for any other app is
  not requestable.
- `search_runbook` filters hits by `partner` front matter (entries may also be marked
  `partner: all`).
- No logic in `src/` branches on the strings "CNET" or "Coach Net"; they appear only in config defaults, docstrings,
  and fixture data. Runbook content may name the partner freely.

## Why
- **Fail-closed is the only safe default for a tool that talks to production CRM
  data.** A read-only credential limits damage, but an unscoped read still leaks data
  from other partners or internal cases into a trace file or a Slack message.
- **Config over code for partner identity** means a second partner is a second
  `.env`, not a fork.
- **Two checks, not one.** The query filter is the control; the post-check is the
  detector for a misconfigured field name, which is exactly the kind of error a
  placeholder like `Partner_Code__c` invites.

## Consequences
- `ScopeError` is a first-class tool error (ADR 0003) and is tested explicitly:
  `test_fail_closed_scoping` covers empty partner code, mismatched record, and missing
  field.
- The live Salesforce implementation (Phase 2) must use the permission set to restrict
  field visibility too; scoping in code is not a substitute for least privilege.
- Multi-partner runs (one agent serving several partners) are out of scope for v1 and
  would need per-request partner selection with the same checks.
