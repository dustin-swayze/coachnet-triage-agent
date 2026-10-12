"""Runbook loading, indexing, and retrieval (ADR 0004).

- ``loader.py``   parse docs/runbook/*.md (front matter + sections) -> RunbookEntry
- ``indexer.py``  (re)build the vector index: ``uv run python -m triage index``
- ``retriever.py`` ``RunbookRetriever`` interface + ChromaRetriever; pgvector later

The graph only ever sees ``RunbookRetriever.search(query, k) -> list[RunbookHit]``.
"""

from __future__ import annotations

from typing import Protocol

from triage.schemas.tools import RunbookHit


class RunbookRetriever(Protocol):
    def search(self, query: str, k: int, *, partner: str) -> list[RunbookHit]:
        """Top-k hits filtered to ``partner`` or 'all'. Scores normalised to [0, 1]."""
        ...
