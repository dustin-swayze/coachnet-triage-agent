---
id: cnet-003
title: Send failed because the inbound HubSpot payload does not match the API contract
partner: CNET
tags: [payload, contract, validation, 400, inbound]
owner: TrxNow integrations (Dustin)
last_seen: never   # TODO(dustin)
---

# Send failed because the inbound HubSpot payload does not match the API contract

## Symptoms

### What the user reports
"Coach Net says the send failed." "HubSpot shows an error on the ticket." The Case
does not exist in Salesforce, or exists with `sync_status = FAILED`.

### What the logs show
The inbound request is rejected at validation or during the Salesforce call. The
payload is logged (TODO(dustin): confirm the app logs inbound bodies and at what
level). Synthetic shape:

```
INFO  [cnet-inbound-case-flow] corr=bad0cafe received ticket hs-55777 action=create
INFO  [cnet-inbound-case-flow] corr=bad0cafe payload={"ticketId":"hs-55777","priority":"Urgent","serviceType":null,...}
ERROR [cnet-inbound-case-flow] corr=bad0cafe APIKIT:BAD_REQUEST required property 'serviceType' is null
ERROR [cnet-inbound-case-flow] corr=bad0cafe responded 400 to HubSpot
```
Or, when validation is loose and Salesforce rejects it:
```
ERROR [cnet-inbound-case-flow] corr=bad0cafe SALESFORCE:INVALID_INPUT REQUIRED_FIELD_MISSING: [Service_Type__c]
```

## Likely cause
The payload violates the API contract: a required property is missing or null, an
enum value is outside the agreed set (e.g. `priority = "Urgent"` where the contract
says `High`), a date is in the wrong format, or a field was renamed on the HubSpot
side.

## How to confirm
1. `search_mule_logs` by external id (HubSpot ticket id): find the `payload=` line and
   the ERROR line.
2. Compare the payload with the API contract. In v1 the agent quotes both and names
   the offending property; a deterministic `validate_payload` tool is on the roadmap.
3. `get_sf_case` by external id: NotFound (never created) or FAILED.

## Fix
1. Send Coach Net the exact property and expected value/format from the contract.
   They correct the ticket and re-send (with the redispatch flag if the Case already
   exists; see cnet-001).
2. If the contract is wrong or the field is genuinely optional: update the RAML/schema
   and the DataWeave default (code change, PR to `test`).

## Notes
Dustin's key need here: see the *exact* payload and diff it against the contract. The
contract lives in the work repo (TODO(dustin)); nothing real is in this repo.
