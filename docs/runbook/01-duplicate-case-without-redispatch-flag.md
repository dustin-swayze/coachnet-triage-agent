---
id: cnet-001
title: Duplicate Case created without the redispatch flag
partner: CNET
tags: [duplicate, redispatch, idempotency, inbound]
owner: TrxNow integrations (Dustin)
last_seen: never   # TODO(dustin): fill from real history
---

# Duplicate Case created without the redispatch flag

## Symptoms

### What the user reports
"We got two cases for the same Coach Net ticket." "Case 00412345 is a duplicate of
00412301 but nobody re-dispatched it." Dispatch sees the same customer twice in the
queue.

### What the logs show
Two inbound transactions for the same HubSpot ticket id within a short window, each
with its own correlation id, both ending in a successful Case insert. No log line
mentions redispatch. Synthetic shape (TODO(dustin): confirm real wording):

```
INFO  [cnet-inbound-case-flow] corr=a1b2c3d4 received ticket hs-55501 action=create
INFO  [cnet-inbound-case-flow] corr=a1b2c3d4 case created 00412301
INFO  [cnet-inbound-case-flow] corr=e5f6a7b8 received ticket hs-55501 action=create
INFO  [cnet-inbound-case-flow] corr=e5f6a7b8 case created 00412345
```

## Likely cause
The inbound flow did not treat the second message as a retry/update. Usual reasons:
HubSpot re-fired the webhook (timeout on first ack), or the upsert key was missing or
blank on the second payload, so the flow fell back to insert. A legitimate re-send
should carry the redispatch flag; its absence means this is an unintended duplicate.

## How to confirm
1. `get_sf_case` for both case numbers: same `external_id`, second has
   `redispatch = false/null`, `duplicate_of_case_number` set.
2. `search_mule_logs` by `external_id`: two transactions, both `action=create`.
3. Check whether the first transaction's ack to HubSpot timed out (an ERROR or a
   long gap before the response line).

## Fix
1. (WRITE, human) Close the newer Case as duplicate and link it to the original.
2. If the upsert key was blank: raise with Coach Net; the payload must include the
   ticket id in the agreed field (see API contract). Add to failure mode cnet-003 evidence.
3. If HubSpot retried after an ack timeout: check Mule app response times around that
   time; consider idempotency on ticket id in the inbound flow (code change, PR to `test`).

## Notes
Related: cnet-003 (payload vs contract). A duplicate *with* the redispatch flag is not a
bug; the agent should say so and stop.
