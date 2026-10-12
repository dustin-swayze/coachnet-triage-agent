---
id: cnet-007
title: Mule app down, mid-deploy, or restart-looping
partner: all
tags: [outage, cloudhub, replicas, deploy, restart, health]
owner: TrxNow integrations (Dustin)
last_seen: never   # TODO(dustin)
---

# Mule app down, mid-deploy, or restart-looping

## Symptoms

### What the user reports
"Nothing is syncing in either direction." "HubSpot shows connection errors." Multiple
cases, sudden onset.

### What the logs show
Either no log lines at all for the window (app not running), or a startup/shutdown
loop. Runtime Manager shows `STOPPED`, `DEPLOYING`, `FAILED`, or RUNNING with 0/N
replicas. Synthetic shape:

```
INFO  [mule] Application coachnet-saas-api starting...
ERROR [mule] Failed to deploy artifact: org.mule.runtime... Connection refused (secure properties)
INFO  [mule] Application coachnet-saas-api stopping...
```

## Likely cause
Failed deployment (bad config / missing secure property), Private Space capacity or
networking change, replica crash (OOM), or a scheduled restart that did not come
back.

## How to confirm
1. `check_mule_app_status`: `is_healthy == False`. This is the health-check
   short-circuit (ADR 0002): the agent stops here and reports it, no case lookup.
2. Note `last_deployed_at` and `recent_restarts` for the alert.

## Fix
1. (WRITE, human) In Runtime Manager: inspect deployment logs, fix config, redeploy or
   restart.
2. After recovery: HubSpot will retry recent webhooks; older ones may need a replay.
3. Phase 6: the scheduled poller reuses this check so the team knows before a bug is
   filed.

## Notes
This entry exists mainly so the runbook search can cite it; the detection is
deterministic and happens before any LLM call.
