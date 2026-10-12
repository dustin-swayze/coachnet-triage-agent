# ADR 0002: Deterministic health check runs first, in code

**Status:** accepted (2026-10-11)

## Context
The most common root cause of "nothing synced" is the Mule app being down, restarting,
or mid-deploy. It is also the cheapest thing to check and the one Dustin wants to know
about instantly, even without a case number.

## Decision
The graph's entry node is `health_check`. It calls `check_mule_app_status()` with no
LLM involvement, stores an `AppStatus` in state, and a conditional edge decides:

- app unhealthy (stopped, zero running replicas, deploying, or restart storm) ->
  go straight to `respond` with an alert and end the run
- app healthy or status unknown -> continue to `parse_request`

If the status tool itself fails (auth error, timeout), the run continues with
`app_status.status = UNKNOWN` and the error recorded as evidence. A monitoring failure
must not block triage of the case.

## Why
- **Judgment is not needed.** "Replicas running: 0" is not something a model should
  interpret. Deterministic code is faster, free, and testable.
- **It short-circuits cost and time.** If the app is down, pulling the case and
  searching logs is wasted work and a misleading answer.
- **It is reusable.** Phase 6's scheduled poller reuses the same tool and the same
  "unhealthy" predicate, so the agent and the alert can never disagree.

## Consequences
- `AppStatus.is_healthy` is a pure function of the record; it is the single definition
  of "down" for the whole project.
- Tests: `test_health_check_short_circuit` asserts that with a stopped-app fixture, no
  other tool is invoked and the response contains the alert.
- The LLM's job is reduced to: parse the request, interpret evidence, write the answer.
  That is the "deterministic where possible, LLM only where judgment is needed"
  principle from the brief, applied at the first node.
