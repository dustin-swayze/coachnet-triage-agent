"""Runbook as source of truth (ADR 0004): loader strictness, embedder determinism, retrieval."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from triage.config import Settings
from triage.runbook.embeddings import HashingEmbeddings
from triage.runbook.loader import RunbookEntry, load_runbook
from triage.runbook.tool import RunbookSearch
from triage.schemas.tools import RunbookSearchRequest

EXPECTED = {
    "cnet-001": "CNET",
    "cnet-002": "CNET",
    "cnet-003": "CNET",
    "cnet-004": "CNET",
    "cnet-005": "all",
    "cnet-006": "all",
    "cnet-007": "all",
}


def _entry_md(entry_id: str | None, *, title: str = "T", partner: str = "CNET") -> str:
    lines = ["---"]
    if entry_id is not None:
        lines.append(f"id: {entry_id}")
    lines += [f"title: {title}", f"partner: {partner}", "tags: [a, b]", "---", ""]
    lines += ["# T", "", "## Symptoms", "### What the user reports", "it broke", ""]
    lines += ["## Likely cause", "a reason", "", "## Fix", "do a thing", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# loader
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seed_entries(runbook_dir: Path) -> list[RunbookEntry]:
    return load_runbook(runbook_dir)


def test_loader_parses_all_seed_entries(seed_entries: list[RunbookEntry]):
    """The seed runbook yields exactly cnet-001..007 with the expected partner scoping."""
    assert {e.id: e.partner for e in seed_entries} == EXPECTED


def test_loader_fills_every_retrieval_section(seed_entries: list[RunbookEntry]):
    """Each seed entry has the sections retrieval and the diagnosis prompt depend on."""
    for e in seed_entries:
        assert e.title, e.id
        assert e.symptoms_reported, e.id
        assert e.likely_cause, e.id
        assert e.how_to_confirm, e.id
        assert e.fix, e.id
        assert e.path.endswith(".md") and "\\" not in e.path


def test_loader_strips_trailing_comments_and_parses_tags(seed_entries: list[RunbookEntry]):
    """Front matter: 'key: value   # comment' keeps only the value; tags become a list."""
    by_id = {e.id: e for e in seed_entries}
    assert by_id["cnet-001"].last_seen == "never"
    assert by_id["cnet-006"].tags[:2] == ["rate-limit", "429"]


def test_loader_skips_underscore_files(runbook_dir: Path, seed_entries: list[RunbookEntry]):
    """_template.md exists in the real directory but is never loaded as an entry."""
    assert (runbook_dir / "_template.md").exists()
    assert all(not e.path.rsplit("/", 1)[-1].startswith("_") for e in seed_entries)
    assert "_template.md" not in {e.path.rsplit("/", 1)[-1] for e in seed_entries}


def test_loader_skips_underscore_files_even_if_malformed(tmp_path: Path):
    """Skipped means skipped: a broken template must not raise."""
    (tmp_path / "_template.md").write_text("no front matter at all", encoding="utf-8")
    (tmp_path / "01.md").write_text(_entry_md("x-1"), encoding="utf-8")
    assert [e.id for e in load_runbook(tmp_path)] == ["x-1"]


def test_loader_rejects_duplicate_ids(tmp_path: Path):
    """Two files claiming the same id would make citations ambiguous -> ValueError."""
    (tmp_path / "a.md").write_text(_entry_md("dup-1"), encoding="utf-8")
    (tmp_path / "b.md").write_text(_entry_md("dup-1"), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate id"):
        load_runbook(tmp_path)


def test_loader_rejects_missing_id(tmp_path: Path):
    """An entry without an id cannot be cited -> ValueError naming the missing key."""
    (tmp_path / "a.md").write_text(_entry_md(None), encoding="utf-8")
    with pytest.raises(ValueError, match="id"):
        load_runbook(tmp_path)


def test_loader_rejects_missing_partner(tmp_path: Path):
    """No partner means no scoping decision can be made -> refuse to load."""
    (tmp_path / "a.md").write_text(_entry_md("p-1", partner=""), encoding="utf-8")
    with pytest.raises(ValueError, match="partner"):
        load_runbook(tmp_path)


def test_loader_rejects_missing_front_matter(tmp_path: Path):
    """A markdown file with no front matter block is an error, not silently ignored."""
    (tmp_path / "a.md").write_text("# just prose\n", encoding="utf-8")
    with pytest.raises(ValueError, match="front matter"):
        load_runbook(tmp_path)


def test_loader_ignores_headings_inside_code_fences(tmp_path: Path):
    """A '## Fix' inside a code fence must not start a new section."""
    body = _entry_md("f-1").replace(
        "do a thing", "do a thing\n```\n## Fix\nnot a heading\n```\nmore"
    )
    (tmp_path / "a.md").write_text(body, encoding="utf-8")
    (entry,) = load_runbook(tmp_path)
    assert "not a heading" in entry.fix
    assert "more" in entry.fix


# ---------------------------------------------------------------------------
# embeddings
# ---------------------------------------------------------------------------

SAMPLE = "HTTP:TOO_MANY_REQUESTS 429 on status update for case 00412500"


def test_hashing_embeddings_deterministic_across_instances():
    """Two instances produce identical vectors, so an index built earlier stays usable."""
    a = HashingEmbeddings().embed_query(SAMPLE)
    b = HashingEmbeddings().embed_query(SAMPLE)
    assert a == b
    assert HashingEmbeddings().embed_documents([SAMPLE]) == [a]


def test_hashing_embeddings_deterministic_across_processes():
    """Vectors do not depend on Python's per-process str hash salt (PYTHONHASHSEED)."""
    code = (
        "from triage.runbook.embeddings import HashingEmbeddings;"
        f"v=HashingEmbeddings().embed_query({SAMPLE!r});"
        "print(sum((i+1)*x for i,x in enumerate(v)))"
    )
    outs = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
        )
        outs.append(proc.stdout.strip())
    assert outs[0] == outs[1]
    here = HashingEmbeddings().embed_query(SAMPLE)
    assert math.isclose(float(outs[0]), sum((i + 1) * x for i, x in enumerate(here)), rel_tol=1e-9)


@pytest.mark.parametrize("text", [SAMPLE, "duplicate case", "tire", "429 429 429 429 429"])
def test_hashing_embeddings_are_l2_normalised(text: str):
    """Unit length vectors make cosine distance = 1 - dot, which the score mapping relies on."""
    vec = HashingEmbeddings().embed_query(text)
    assert len(vec) == 1024
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-9)


def test_hashing_embeddings_empty_text_is_zero_vector():
    """Text with no usable tokens yields a zero vector rather than dividing by zero."""
    vec = HashingEmbeddings().embed_query("the of and")
    assert not any(vec)


def test_hashing_embeddings_respect_dim():
    """The dimension is configurable and honoured."""
    assert len(HashingEmbeddings(dim=64).embed_query(SAMPLE)) == 64


def test_similar_text_scores_higher_than_unrelated_text():
    """Sanity: the lexical embedder ranks overlapping text above unrelated text."""
    emb = HashingEmbeddings()
    q = emb.embed_query("rate limit 429 too many requests")
    near = emb.embed_query("HubSpot rate limit 429 too many requests on status updates")
    far = emb.embed_query("DataWeave transform expression failure")

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert dot(q, near) > dot(q, far)


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry_id", sorted(EXPECTED))
def test_each_entry_is_its_own_top_hit(
    settings: Settings, seed_entries: list[RunbookEntry], entry_id: str
):
    """Querying with an entry's own 'what the user reports' text retrieves that entry first."""
    entry = next(e for e in seed_entries if e.id == entry_id)
    result = RunbookSearch(settings)(
        RunbookSearchRequest(query=entry.symptoms_reported[:1900], k=3)
    )
    assert result.hits[0].id == entry_id  # only the top rank is asserted: lower ranks are noisy
    assert result.source == "chroma"
    assert 0.0 <= result.hits[0].score <= 1.0


def test_hit_carries_the_fields_the_model_cites(settings: Settings):
    """A hit mirrors the markdown (title, path, fix, ...) so the model never re-reads files."""
    result = RunbookSearch(settings)(
        RunbookSearchRequest(query="HubSpot 429 rate limit status updates late", k=1)
    )
    hit = result.hits[0]
    assert hit.id == "cnet-006"
    assert hit.partner == "all"
    assert hit.path.endswith("06-hubspot-rate-limit.md")
    assert hit.fix and hit.likely_cause and hit.how_to_confirm


def test_k_limits_the_number_of_hits(settings: Settings):
    """k is honoured."""
    search = RunbookSearch(settings)
    assert len(search(RunbookSearchRequest(query="duplicate case", k=2)).hits) == 2
    assert len(search(RunbookSearchRequest(query="duplicate case", k=1)).hits) == 1
