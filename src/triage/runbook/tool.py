"""``search_runbook`` tool: the SearchRunbook implementation used by the graph."""

from __future__ import annotations

from triage.config import Settings
from triage.runbook import RunbookRetriever
from triage.runbook.embeddings import get_embeddings
from triage.runbook.retriever import ChromaRunbookRetriever
from triage.schemas.tools import RunbookSearchRequest, RunbookSearchResult


class RunbookSearch:
    """Scoped to ``settings.partner_code``: the caller cannot choose the partner (ADR 0005)."""

    def __init__(self, settings: Settings, retriever: RunbookRetriever | None = None) -> None:
        self._settings = settings
        self._retriever = retriever

    def _get_retriever(self) -> RunbookRetriever:
        # Lazy so constructing the toolset never touches disk or loads Chroma.
        if self._retriever is None:
            self._retriever = ChromaRunbookRetriever(
                self._settings.index_dir, get_embeddings(self._settings)
            )
        return self._retriever

    def __call__(self, request: RunbookSearchRequest) -> RunbookSearchResult:
        hits = self._get_retriever().search(
            request.query, request.k, partner=self._settings.partner_code
        )
        return RunbookSearchResult(query=request.query, hits=hits, source="chroma")
