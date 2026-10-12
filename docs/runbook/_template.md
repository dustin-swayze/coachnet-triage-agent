---
id: <partner>-<nnn>            # e.g. cnet-008; stable, never reused
title: <short, symptom-first title>
partner: <partner code or "all">
tags: [<error-type>, <flow>, ...]
owner: <team or person>
last_seen: <YYYY-MM-DD or "never">
---

# <Title>

## Symptoms

### What the user reports
<Plain-English, as a teammate would phrase it. This text is what retrieval matches
against most often, so write it the way people actually complain.>

### What the logs show
<Log lines, error types, flow names. Use synthetic values with real shapes.
Mark guesses with TODO(dustin).>

## Likely cause
<One paragraph.>

## How to confirm
<Numbered steps a human (or the agent) can follow using read-only tools.>

## Fix
<Numbered steps. Say explicitly if a step is a WRITE action; the agent never performs
those, it only suggests them.>

## Notes
<Related entries, caveats, open questions.>
