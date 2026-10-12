# ADR 0004: Markdown runbook is the source of truth; the vector index is derived

**Status:** accepted (2026-10-11)

## Context
The agent's diagnostic knowledge is a set of known failure modes: symptoms, likely
cause, how to confirm, fix. This knowledge must be readable and editable by humans
(Dustin, teammates, eventually the partner), reviewable in pull requests, and
searchable by the agent.

## Decision
- Known failure modes live in `docs/runbook/<nn>-<slug>.md`, one per file, following
  `docs/runbook/_template.md` (Title, Symptoms split into *what the user reports* and
  *what the logs show*, Likely cause, How to confirm, Fix, Owner, Last seen).
- `uv run python -m triage index` parses those files and (re)builds a local vector
  index. The index is gitignored and can be deleted at any time.
- The agent reads the index only through a `RunbookRetriever` interface
  (`search(query, k) -> list[RunbookHit]`). Chroma is the v1 backend; Postgres +
  pgvector is the planned production backend. Hybrid (BM25 + vector) is a candidate.
- In mock mode the index is built with deterministic fake embeddings so indexing is
  free and tests are hermetic.

## Why
- **Humans fix the runbook, not the database.** Markdown in git gets review,
  history, and blame. A vector store has none of those.
- **Rebuildable derived artifacts remove a whole class of bugs.** If retrieval looks
  wrong, delete the index and rebuild. There is no "the index drifted" failure.
- **The retriever interface is the swap point.** The graph never sees Chroma. Moving
  to pgvector or adding keyword search touches one module.
- **Honest sizing.** With 7 to 20 entries, vector search is not strictly necessary;
  keyword matching would work. We keep it because RAG practice is a stated goal of the
  project and the entry count will grow. The interface keeps the option to switch to
  hybrid search cheap.

## Consequences
- Each runbook file carries front matter (`id`, `title`, `partner`, `tags`,
  `last_seen`, `owner`) so hits can be filtered by partner later.
- A runbook entry is small enough to be a single chunk. If entries grow, chunk by
  section with the title prepended to each chunk.
- Tests assert that each seed entry is the top hit for its own "what the user
  reports" symptoms. That is a retrieval sanity check, not an eval.

## Amendment (Session 1 Part B)
The default embedder is `HashingEmbeddings` (lexical, free, deterministic; see ADR 0003).
For a runbook of under ~50 entries it is likely good enough in live mode too.
Anthropic has no embeddings API, so a semantic embedder means Voyage or OpenAI; that
choice is `TODO(dustin)` and is gated on Phase 5 eval results, not taste.
