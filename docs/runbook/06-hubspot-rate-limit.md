---
id: cnet-006
title: HubSpot rate limit (429) on outbound status updates
partner: all
tags: [rate-limit, 429, HTTP:TOO_MANY_REQUESTS, outbound, retry]
owner: TrxNow integrations (Dustin)
last_seen: never   # TODO(dustin)
---

# HubSpot rate limit (429) on outbound status updates

## Symptoms

### What the user reports
"Status updates are hours late in HubSpot." "Some cases updated, some didn't."
Usually after a bulk operation (mass status change, data load, backlog replay).

### What the logs show
Bursts of 429 from HubSpot on the outbound flow, with retry-after headers; some
transactions succeed after retries, some exhaust retries. Synthetic shape:

```
WARN  [cnet-outbound-status-flow] corr=r8l1m1t1 HTTP:TOO_MANY_REQUESTS 429 retry-after=10 attempt=1/3
WARN  [cnet-outbound-status-flow] corr=r8l1m1t1 HTTP:TOO_MANY_REQUESTS 429 retry-after=10 attempt=2/3
ERROR [cnet-outbound-status-flow] corr=r8l1m1t1 HTTP:TOO_MANY_REQUESTS 429 retries exhausted; ticket hs-55950 not updated
```

## Likely cause
Outbound call volume exceeded HubSpot's per-10-second or daily limit for the private
app. Triggered by bulk updates in Salesforce or a replay of queued events.

## How to confirm
1. `search_mule_logs` by correlation id or case number: 429s on the outbound flow,
   `retries exhausted` for the reported case.
2. Count of 429s in the window (agent reports the number and the first/last time).
3. Check what happened in Salesforce at that time (bulk job, data load).

## Fix
1. Wait; the limit resets. Re-send the failed updates (WRITE, human: replay from the
   dead-letter queue / re-trigger from Salesforce).
2. If recurring: add throttling / batching on the outbound flow, or request a higher
   limit from HubSpot. Code change, PR to `test`.

## Notes
Partner-agnostic. Direction is Salesforce -> HubSpot (outbound), unlike cnet-001..004.
