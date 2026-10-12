"""Chroma-backed ``RunbookRetriever`` (ADR 0004).

Everything needed to rebuild a ``RunbookHit`` is stored in Chroma metadata, so a
search never re-reads the markdown files and cannot disagree with the index.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from triage.schemas.tools import RunbookHit
from triage.tools.base import UpstreamError

INDEX_MISSING = "runbook index not built; run: uv run python -m triage index"

# Stored verbatim in metadata. Tags are joined with "," at index time because Chroma
# metadata values must be scalars.
_TEXT_FIELDS = (
    "title",
    "path",
    "partner",
    "symptoms_reported",
    "symptoms_logs",
    "likely_cause",
    "how_to_confirm",
    "fix",
)
_OPTIONAL_FIELDS = ("owner", "last_seen")


def open_store(persist_dir: str | Path, embeddings: Embeddings, collection_name: str) -> Chroma:
    """Shared by indexer and retriever so both use the same cosine/no-telemetry config."""
    return Chroma(
        collection_name=collection_name,
        embedding_function=embeddings,
        persist_directory=str(persist_dir),
        collection_metadata={"hnsw:space": "cosine"},
        client_settings=chromadb.config.Settings(anonymized_telemetry=False),
    )


class ChromaRunbookRetriever:
    def __init__(
        self,
        persist_dir: str | Path,
        embeddings: Embeddings,
        collection_name: str = "runbook",
    ) -> None:
        self._persist_dir = Path(persist_dir)
        self._embeddings = embeddings
        self._collection_name = collection_name
        self._store: Chroma | None = None

    def _open(self) -> Chroma:
        if self._store is None:
            # Check first: Chroma would happily create an empty directory and we would
            # then report "no hits" instead of "index not built".
            if not self._persist_dir.is_dir():
                raise UpstreamError(INDEX_MISSING)
            self._store = open_store(self._persist_dir, self._embeddings, self._collection_name)
        return self._store

    def search(self, query: str, k: int, *, partner: str) -> list[RunbookHit]:
        store = self._open()
        try:
            if store._collection.count() == 0:  # noqa: SLF001
                raise UpstreamError(INDEX_MISSING)
            results = store.similarity_search_with_score(
                query, k=k, filter={"partner": {"$in": [partner, "all"]}}
            )
        except UpstreamError:
            raise
        except Exception as exc:  # Chroma raises assorted types on a corrupt index
            raise UpstreamError(f"runbook search failed: {exc}") from exc
        return [self._to_hit(doc.id, doc.metadata, distance) for doc, distance in results]

    @staticmethod
    def _to_hit(entry_id: str | None, meta: dict[str, Any], distance: float) -> RunbookHit:
        text = {name: str(meta.get(name) or "") for name in _TEXT_FIELDS}
        optional = {name: str(meta.get(name) or "") or None for name in _OPTIONAL_FIELDS}
        return RunbookHit(
            id=entry_id or "",
            score=min(1.0, max(0.0, 1.0 - float(distance))),
            **text,
            **optional,
        )
