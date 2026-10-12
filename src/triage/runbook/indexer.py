"""Build the runbook vector index (ADR 0004).

The index is a derived artifact: every build wipes and recreates it, so there is no
"index drifted from the markdown" failure mode.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import chromadb

from triage.config import Settings
from triage.runbook.embeddings import get_embeddings
from triage.runbook.loader import RunbookEntry, load_runbook
from triage.runbook.retriever import open_store

COLLECTION = "runbook"


def _entry_metadata(entry: RunbookEntry) -> dict[str, str]:
    return {
        "title": entry.title,
        "path": entry.path,
        "partner": entry.partner,
        "tags": ",".join(entry.tags),
        "symptoms_reported": entry.symptoms_reported,
        "symptoms_logs": entry.symptoms_logs,
        "likely_cause": entry.likely_cause,
        "how_to_confirm": entry.how_to_confirm,
        "fix": entry.fix,
        "owner": entry.owner or "",
        "last_seen": entry.last_seen or "",
    }


def _wipe(index_path: Path) -> None:
    """Delete the index dir. On Windows a lingering Chroma handle can block rmtree."""
    if not index_path.exists():
        return
    try:
        shutil.rmtree(index_path)
    except OSError:
        # Locked files: fall back to dropping the collection in place.
        client = chromadb.PersistentClient(
            path=str(index_path), settings=chromadb.config.Settings(anonymized_telemetry=False)
        )
        try:
            client.delete_collection(COLLECTION)
        except Exception:  # noqa: BLE001, S110 - collection may not exist
            pass


def build_index(
    settings: Settings,
    *,
    runbook_dir: str | Path | None = None,
    index_dir: str | Path | None = None,
) -> int:
    """Rebuild the index from markdown; returns the number of entries indexed."""
    entries = load_runbook(runbook_dir or settings.runbook_dir)
    index_path = Path(index_dir or settings.index_dir)
    _wipe(index_path)  # before creating the new client, so no handles are held
    index_path.mkdir(parents=True, exist_ok=True)

    store = open_store(index_path, get_embeddings(settings), COLLECTION)
    if entries:
        store.add_texts(
            texts=[e.embedding_text() for e in entries],
            metadatas=[_entry_metadata(e) for e in entries],
            ids=[e.id for e in entries],
        )
    return len(entries)


def ensure_index(settings: Settings) -> int | None:
    """Make sure an index exists before serving questions.

    Mock mode builds it on first use (free, about a second). Live mode refuses, so
    embedding costs are never a surprise. Returns the entry count if it built one.
    """
    if Path(settings.index_dir).is_dir():
        return None
    if not settings.is_mock:
        raise RuntimeError("Runbook index not found. Run: uv run python -m triage index")
    return build_index(settings)
