"""Parse ``docs/runbook/*.md`` into ``RunbookEntry`` objects (ADR 0004).

The markdown is the source of truth, so the parser is deliberately strict about
identity (``id``, ``title``, ``partner``) and forgiving about prose sections. Front
matter is parsed by hand (``key: value`` and ``tags: [a, b]``) to avoid a YAML
dependency for six flat keys.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_TRAILING_COMMENT = re.compile(r"\s+#.*$")
_HEADING = re.compile(r"^(#{2,3})\s+(.*?)\s*$")
_REQUIRED = ("id", "title", "partner")

# (heading level, normalised heading) -> RunbookEntry field
_SECTION_FIELDS: dict[tuple[int, str], str] = {
    (3, "what the user reports"): "symptoms_reported",
    (3, "what the logs show"): "symptoms_logs",
    (2, "likely cause"): "likely_cause",
    (2, "how to confirm"): "how_to_confirm",
    (2, "fix"): "fix",
    (2, "notes"): "notes",
}


class RunbookEntry(BaseModel):
    """One known failure mode. Mirrors ``RunbookHit`` plus the fields only indexing needs."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    partner: str
    tags: list[str] = Field(default_factory=list)
    owner: str | None = None
    last_seen: str | None = None
    path: str = Field(description="repo-relative, forward slashes")
    symptoms_reported: str = ""
    symptoms_logs: str = ""
    likely_cause: str = ""
    how_to_confirm: str = ""
    fix: str = ""
    notes: str = ""

    def embedding_text(self) -> str:
        """Text that gets embedded: what a complaint or log excerpt would resemble.

        How-to-confirm and fix are excluded on purpose: they describe our tools and
        remedies, not the symptoms a query contains, and would only add noise.
        """
        return "\n".join(
            [
                self.title,
                " ".join(self.tags),
                self.symptoms_reported,
                self.symptoms_logs,
                self.likely_cause,
            ]
        )


def _parse_front_matter(block: str) -> dict[str, str | list[str]]:
    data: dict[str, str | list[str]] = {}
    for raw in block.splitlines():
        line = _TRAILING_COMMENT.sub("", raw).strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[key.strip()] = [t.strip() for t in value[1:-1].split(",") if t.strip()]
        else:
            data[key.strip()] = value.strip("\"'")
    return data


def _split_sections(body: str) -> dict[str, str]:
    """Collect text under each known heading; unknown headings (e.g. ``## Symptoms``)
    only act as containers. Headings inside code fences are ignored."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            level, name = len(match.group(1)), match.group(2).strip().lower()
            current = _SECTION_FIELDS.get((level, name))
            continue
        if current:
            sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _repo_relative(path: Path) -> str:
    """Path relative to the repo root (nearest parent with pyproject.toml), else as given."""
    resolved = path.resolve()
    for parent in resolved.parents:
        if (parent / "pyproject.toml").exists():
            return resolved.relative_to(parent).as_posix()
    return path.as_posix()


def load_runbook(runbook_dir: str | Path) -> list[RunbookEntry]:
    """Load every ``*.md`` in ``runbook_dir`` except files starting with ``_``."""
    entries: list[RunbookEntry] = []
    seen: dict[str, str] = {}
    for path in sorted(Path(runbook_dir).glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        match = _FRONT_MATTER.match(text)
        if not match:
            raise ValueError(f"{path}: missing front matter (--- block)")
        meta = _parse_front_matter(match.group(1))
        missing = [k for k in _REQUIRED if not meta.get(k)]
        if missing:
            raise ValueError(f"{path}: front matter missing required key(s): {', '.join(missing)}")
        entry_id = str(meta["id"])
        if entry_id in seen:
            raise ValueError(
                f"{path}: duplicate id {entry_id!r} (already used by {seen[entry_id]})"
            )
        seen[entry_id] = path.name
        tags = meta.get("tags", [])
        entries.append(
            RunbookEntry(
                id=entry_id,
                title=str(meta["title"]),
                partner=str(meta["partner"]),
                tags=tags if isinstance(tags, list) else [str(tags)],
                owner=str(meta["owner"]) if meta.get("owner") else None,
                last_seen=str(meta["last_seen"]) if meta.get("last_seen") else None,
                path=_repo_relative(path),
                **_split_sections(match.group(2)),
            )
        )
    return entries
