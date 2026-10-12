---
id: cnet-002
title: Case shows as sent but never appears in the Priority Queue
partner: CNET
tags: [priority-queue, sync-status, silent-failure, inbound]
owner: TrxNow integrations (Dustin)
last_seen: never   # TODO(dustin)
---

# Case shows as sent but never appears in the Priority Queue

## Symptoms

### What the user reports
"Coach Net says they sent it, HubSpot shows it as synced, but it's not in our
priority queue." The Case may exist in Salesforce with `sync_status = SENT` or
`SYNCED`, yet dispatch never saw it.

### What the logs show
A complete inbound transaction with a successful Case insert and a 200 response to
HubSpot. No error. The gap is *after* the Mule app: the Case was created but the
Salesforce-side automation that places it in the queue did not run or filtered it
out. Synthetic shape:

```
INFO  [cnet-inbound-case-flow] corr=c0ffee01 received ticket hs-55620 action=create
INFO  [cnet-inbound-case-flow] corr=c0ffee01 case created 00412400
INFO  [cnet-inbound-case-flow] corr=c0ffee01 responded 200 to HubSpot
```

## Likely cause
Mule did its job. The Case is missing a value the queue logic requires (priority,
region, service type, or the partner code itself), or the queue automation (Flow /
trigger) errored silently, or the Case was created in a status the queue excludes.
TODO(dustin): confirm how the Priority Queue selects cases (object/field/report).

## How to confirm
1. `get_sf_case`: Case exists, `sync_status` is SENT/SYNCED, `last_sync_at` set.
2. `search_mule_logs` by correlation id: no ERROR lines; 200 response logged.
3. Compare the Case's queue-relevant fields against the queue's entry criteria
   (TODO(dustin): list them; a future read-only "priority queue check" tool does this).
4. Check Salesforce Flow error emails / Apex logs around `last_sync_at` (manual in v1).

## Fix
1. If a required field is blank: raise the mapping with Coach Net or fix the DataWeave
   default (see cnet-004). (WRITE, human) Backfill the field so the queue picks it up.
2. If the queue automation failed: fix the Flow/trigger; (WRITE, human) re-run for the
   affected Case.
3. If status excludes it: confirm expected initial status in the API contract.

## Notes
This is the "everything looks green" failure. The agent's value is saying clearly:
"Mule succeeded; the problem is on the Salesforce side after insert," with the
evidence. Candidate tool for Phase 5+: `check_priority_queue(case_number)`.
