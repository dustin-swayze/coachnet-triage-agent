"""Shared fixtures.

Every test runs in mock mode with no network and no API keys (ADR 0003). The
``settings`` fixture ignores any developer ``.env`` and scrubs the process
environment so a local configuration can never change a test outcome.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from triage.config import Settings
from triage.llm import ScriptedChatModel, default_mock_responders
from triage.runbook.indexer import build_index
from triage.tools.base import ToolSet
from triage.tools.mock import build_mock_tools

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNBOOK_DIR = REPO_ROOT / "docs" / "runbook"

_LEAKY_PREFIXES = (
    "TRIAGE_",
    "LLM_",
    "PARTNER_",
    "MOCK_",
    "SF_",
    "ANYPOINT_",
    "STEP_CAP",
    "TOOL_TIMEOUT",
    "RUN_BUDGET",
    "ANTHROPIC_",
    "OPENAI_",
    "EMBEDDINGS_",
    "INDEX_DIR",
    "TRACE_DIR",
    "RUNBOOK_DIR",
    "MULE_APP_NAME",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every config env var so the environment cannot leak into Settings."""
    for name in list(os.environ):
        if name.upper().startswith(_LEAKY_PREFIXES):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def runbook_dir() -> Path:
    return RUNBOOK_DIR


@pytest.fixture(scope="session")
def built_index_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Runbook Chroma index, built once per session (a build takes about a second)."""
    index_dir = tmp_path_factory.mktemp("runbook-index") / "index"
    base = Settings(_env_file=None, triage_mode="mock", index_dir=str(index_dir))
    count = build_index(base, runbook_dir=RUNBOOK_DIR, index_dir=index_dir)
    assert count >= 7, "seed runbook should index at least the 7 seed entries"
    return index_dir


@pytest.fixture
def settings(tmp_path: Path, built_index_dir: Path, _clean_env: None) -> Settings:
    """Mock-mode settings pointing at the shared index and a per-test trace dir."""
    return Settings(
        _env_file=None,
        triage_mode="mock",
        index_dir=str(built_index_dir),
        trace_dir=str(tmp_path / "traces"),
        runbook_dir=str(RUNBOOK_DIR),
    )


@pytest.fixture
def tools(settings: Settings) -> ToolSet:
    return build_mock_tools(settings)


@pytest.fixture
def llm() -> ScriptedChatModel:
    return ScriptedChatModel(responders=default_mock_responders())
