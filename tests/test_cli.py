"""CLI smoke tests: every subcommand imports, runs in mock mode, and exits sensibly.

Protects against breakage in ``__main__.py``, which nothing else imports.
"""

from __future__ import annotations

import pytest

import triage.__main__ as cli


@pytest.fixture
def use_settings(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def test_ask_prints_a_diagnosis(use_settings, capsys):
    assert cli.main(["ask", "Case 00412345 looks like a duplicate, what happened?"]) == 0
    out = capsys.readouterr().out
    assert "cnet-001" in out and "trace:" in out


def test_health_exit_codes(use_settings, monkeypatch, capsys):
    assert cli.main(["health"]) == 0
    monkeypatch.setattr(
        cli, "get_settings", lambda: use_settings.model_copy(update={"mock_app_state": "down"})
    )
    assert cli.main(["health"]) == 1
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: use_settings.model_copy(update={"mock_app_state": "status_error"}),
    )
    assert cli.main(["health"]) == 3
    assert "UNKNOWN" in capsys.readouterr().out


def test_slack_without_tokens_exits_with_setup_pointer(use_settings, capsys):
    assert cli.main(["slack"]) == 2
    assert "docs/slack-setup.md" in capsys.readouterr().out


def test_index_rebuilds(use_settings, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: use_settings.model_copy(update={"index_dir": str(tmp_path / "idx")}),
    )
    assert cli.main(["index"]) == 0
    assert "Indexed 7" in capsys.readouterr().out
