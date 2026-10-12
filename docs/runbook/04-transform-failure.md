---
id: cnet-004
title: Transform (DataWeave) failure while mapping HubSpot payload to Case
partner: CNET
tags: [dataweave, transform, mapping, MULE:EXPRESSION, inbound]
owner: TrxNow integrations (Dustin)
last_seen: never   # TODO(dustin)
---

# Transform (DataWeave) failure while mapping HubSpot payload to Case

## Symptoms

### What the user reports
"The case failed in the transform." "Mule threw an error on a ticket that looks
fine." Often a payload that *passes* validation but has an unexpected shape (array
where an object was expected, empty string in a date, unknown picklist value).

### What the logs show
A `MULE:EXPRESSION` or `MULE:TRANSFORMATION` error from the mapping step, naming the
DataWeave line/field. Synthetic shape:

```
INFO  [cnet-inbound-case-flow] corr=d00dfeed received ticket hs-55810 action=create
ERROR [cnet-inbound-case-flow] corr=d00dfeed MULE:EXPRESSION "Cannot coerce String ("") to Date" at map-ticket-to-case.dwl line 14, column 22 (field: scheduledDate)
ERROR [cnet-inbound-case-flow] corr=d00dfeed responded 500 to HubSpot
```

## Likely cause
A DataWeave mapping assumes a shape or value the payload does not have. Common:
empty string instead of null for dates/numbers; picklist value not in the mapping
table; nested object missing; new field added on the HubSpot side without a mapping.

## How to confirm
1. `search_mule_logs` by external id: ERROR with `MULE:EXPRESSION` /
   `MULE:TRANSFORMATION`, the `.dwl` file name, and the field.
2. Look at the `payload=` line (if logged) for the named field's actual value.
3. `get_sf_case` by external id: NotFound or FAILED.

## Fix
1. Short term: Coach Net corrects the value on the ticket and re-sends.
2. Proper fix: harden the mapping (null-safe coercion, default, or explicit error with
   a clear message). Code change, PR to `test`, then deploy (WRITE, human).
3. If a new field appeared: update the API contract first (cnet-003), then the mapping.

## Notes
Distinguish from cnet-003: contract mismatch fails *before* the transform (400);
transform failures happen *after* validation (500). The error type is the tell.
