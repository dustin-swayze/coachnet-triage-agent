---
id: cnet-005
title: Expired or invalid credentials between Mule and Salesforce or HubSpot
partner: all
tags: [auth, 401, 403, token, SALESFORCE:CONNECTIVITY, HTTP:UNAUTHORIZED]
owner: TrxNow integrations (Dustin)
last_seen: never   # TODO(dustin)
---

# Expired or invalid credentials between Mule and Salesforce or HubSpot

## Symptoms

### What the user reports
"Nothing has synced since this morning." Many cases affected at once, not one. The
Mule app is up and healthy.

### What the logs show
Every transaction fails at the same outbound call with 401/403. Synthetic shape:

```
INFO  [cnet-inbound-case-flow] corr=0auth001 received ticket hs-55900 action=create
ERROR [cnet-inbound-case-flow] corr=0auth001 SALESFORCE:CONNECTIVITY INVALID_SESSION_ID: Session expired or invalid
ERROR [cnet-inbound-case-flow] corr=0auth001 responded 502 to HubSpot
```
or, outbound to HubSpot:
```
ERROR [cnet-outbound-status-flow] corr=0auth002 HTTP:UNAUTHORIZED 401 {"category":"INVALID_AUTHENTICATION",...}
```

## Likely cause
Rotated password/secret, expired connected-app certificate, revoked token, a
deactivated integration user, or an IP allowlist change. Secrets in Anypoint secure
properties were not updated after a rotation.

## How to confirm
1. `check_mule_app_status`: RUNNING (this is not an outage).
2. `search_mule_logs` for the reported case: 401/403 or INVALID_SESSION_ID at the
   outbound step.
3. Pattern across cases: same error, same step, same start time. (Agent notes "likely
   affects all cases since <first error timestamp>".)

## Fix
1. (WRITE, human) Rotate/renew the credential and update the secure property in
   Runtime Manager; restart the app if the connector caches tokens.
2. Confirm the integration user is active and the IP allowlist includes the Private
   Space egress IPs.
3. Re-send affected tickets from HubSpot (with redispatch flag where Cases exist).

## Notes
Partner-agnostic (`partner: all`). The agent must never print the credential values
it sees in logs; redaction covers `Authorization`, `access_token`, session ids.
