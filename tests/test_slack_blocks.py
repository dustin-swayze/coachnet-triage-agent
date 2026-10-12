"""Slack message formatting: shapes, escaping, and Block Kit limits.

All pure: runs are real mock-mode agent runs (no network), and nothing here imports
the Slack SDK. The security-relevant tests are the escaping ones: injected ``<!channel>``
must never reach Slack as live syntax.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from helpers import run

from triage.config import Settings
from triage.graph.build import RunResult
from triage.schemas.state import Diagnosis, Evidence
from triage.schemas.tools import AppStatus, AppStatusValue
from triage.slack import blocks as sb

INJECTION = "<!channel> <@U999> <https://evil.example|click> & stuff"
USER = "U0123ABCD"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def walk_texts(msg: sb.SlackMessage) -> list[tuple[str, str]]:
    """(block_type/element_kind, text) for every text in the message."""
    out: list[tuple[str, str]] = []
    for b in msg["blocks"]:
        if b["type"] in ("section", "header"):
            out.append((b["type"], b["text"]["text"]))
        elif b["type"] == "context":
            out.extend(("context", el["text"]) for el in b["elements"])
    return out


def dump(msg: sb.SlackMessage) -> str:
    return json.dumps(msg, ensure_ascii=False)


def joined(msg: sb.SlackMessage) -> str:
    return "\n".join(t for _, t in walk_texts(msg))


def assert_valid(msg: sb.SlackMessage) -> None:
    assert msg["text"].strip()
    assert 1 <= len(msg["blocks"]) <= sb.MAX_BLOCKS
    for kind, text in walk_texts(msg):
        assert text.strip(), "empty text block"
        assert len(text) <= (sb.HEADER_LIMIT if kind == "header" else sb.SECTION_LIMIT)
    assert all(b["type"] in ("section", "context", "divider", "header") for b in msg["blocks"])


def assert_no_live_syntax(msg: sb.SlackMessage, *, allow: tuple[str, ...] = ()) -> None:
    s = dump(msg)
    for needle in ("<!", "<@U999>", "<https", "<http", "|click>"):
        assert needle not in s, f"{needle!r} leaked into {s[:200]}"
    # The only angle brackets allowed are our own validated mentions.
    stripped = s
    for a in allow:
        stripped = stripped.replace(a, "")
    assert "<" not in stripped
    assert ">" not in stripped.replace("\\n> ", "")  # working_message block quote marker


def result(question: str, settings: Settings, **overrides: Any) -> RunResult:
    return run(question, settings.model_copy(update=overrides))


@pytest.fixture
def dup(settings: Settings) -> RunResult:
    return result("Case 00412345 looks like a duplicate, what happened?", settings)


# ---------------------------------------------------------------------------
# escape / truncate / mention
# ---------------------------------------------------------------------------
def test_escape_order_and_chars() -> None:
    assert sb.escape("a & b < c > d") == "a &amp; b &lt; c &gt; d"
    assert sb.escape("&lt;") == "&amp;lt;"  # already-escaped text is escaped again, not trusted
    assert sb.escape("plain") == "plain"


def test_truncate() -> None:
    assert sb.truncate("short", 10) == "short"
    cut = sb.truncate("x" * 100, 20)
    assert len(cut) == 20 and cut.endswith("…")
    # a cut inside an entity drops the stub instead of leaving "&am"
    cut = sb.truncate(sb.escape("&" * 50), 10)
    assert len(cut) <= 10 and cut.endswith("…")
    assert "&" not in cut.rstrip("…").replace("&amp;", "")


@pytest.mark.parametrize(
    "bad", [None, "", "u123", "U1", "X12345", "U12 34", "U123\n", "U" + "A" * 30]
)
def test_invalid_user_id_gets_no_mention(bad: str | None) -> None:
    msg = sb.working_message("hi", bad)
    assert "<@" not in dump(msg)


# ---------------------------------------------------------------------------
# working message
# ---------------------------------------------------------------------------
def test_working_message() -> None:
    msg = sb.working_message("case 00412345", USER)
    assert_valid(msg)
    assert f"<@{USER}>" in dump(msg)
    assert "> case 00412345" in joined(msg)
    assert "Looking into it" in joined(msg)


# ---------------------------------------------------------------------------
# result message: every response kind
# ---------------------------------------------------------------------------
def test_diagnosis_message(dup: RunResult, settings: Settings) -> None:
    msg = sb.result_message(dup, "Case 00412345 looks like a duplicate", USER, settings)
    assert_valid(msg)
    text = joined(msg)
    assert f"<@{USER}> asked" in text
    assert "*Diagnosis*" in text and "*Likely cause*" in text and "*Suggested fix*" in text
    assert "1. (WRITE, human)" in text and "\n2. " in text  # numbered lines and breaks kept
    assert "Confidence: 61%" in text
    assert "`cnet-001`" in text
    assert ":warning: needs a human (write actions)" in text
    assert "Incomplete" not in text
    footer = walk_texts(msg)[-1][1]
    assert footer.startswith("Evidence in thread · 4/8 steps · ")
    assert f"run `{dup.tracer.run_id}`" in footer
    assert "LangSmith" not in footer
    assert msg["text"].startswith("Diagnosis:")


def test_no_needs_human_marker_when_not_needed(dup: RunResult, settings: Settings) -> None:
    dup.state["diagnosis"] = dup.state["diagnosis"].model_copy(
        update={"needs_human": False, "matched_runbook_ids": []}
    )
    text = joined(sb.result_message(dup, "q", USER, settings))
    assert "needs a human" not in text
    assert "Runbook:" not in text


def test_alert_message(settings: Settings) -> None:
    r = result("case 00412345", settings, mock_app_state="down")
    msg = sb.result_message(r, "case 00412345", USER, settings)
    assert_valid(msg)
    text = joined(msg)
    assert ":red_circle: *Coach Net Mule app is not healthy*" in text
    assert "STOPPED" in text and "Last deploy:" in text and "Detail:" in text
    assert "Runtime Manager" in text and "`cnet-007`" in text
    assert "not healthy" in msg["text"]


def test_health_message(settings: Settings) -> None:
    r = result("is the mule app up?", settings)
    msg = sb.result_message(r, "is the mule app up?", USER, settings)
    assert_valid(msg)
    assert ":large_green_circle:" in joined(msg) and "looks healthy" in joined(msg)


def test_health_message_status_unknown(settings: Settings) -> None:
    r = result("is the mule app up?", settings, mock_app_state="status_error")
    msg = sb.result_message(r, "is the mule app up?", USER, settings)
    assert_valid(msg)
    text = joined(msg)
    assert ":white_circle:" in text and "status check itself failed" in text
    assert ":large_green_circle:" not in text


def test_ask_for_case_message(settings: Settings) -> None:
    r = result("hello there", settings)
    msg = sb.result_message(r, "hello there", USER, settings)
    assert_valid(msg)
    text = joined(msg)
    assert "8 digits" in text and "HubSpot ticket id" in text
    assert "App health right now: coachnet-saas-api" in text


def test_nothing_found_message(settings: Settings) -> None:
    r = result("Look at case 00499999", settings)
    msg = sb.result_message(r, "Look at case 00499999", USER, settings)
    assert_valid(msg)
    text = joined(msg)
    assert "found nothing for `00499999` in Coach Net (CNET) data" in text
    assert "`get_sf_case`: NotFoundError" in text
    assert "`search_mule_logs`: NotFoundError" in text
    assert "Check the number" in text


def test_no_diagnosis_and_incomplete_banner(settings: Settings) -> None:
    r = result("case 00412345", settings, step_cap=2)
    assert r.state["incomplete"]
    msg = sb.result_message(r, "case 00412345", USER, settings)
    assert_valid(msg)
    blocks = walk_texts(msg)
    # banner is the first section, right after the "who asked" context line
    assert blocks[0][0] == "context"
    assert blocks[1][1].startswith(":hourglass: *Incomplete:* step cap reached")
    assert blocks[1][1].endswith("Showing what was found so far.")
    assert "No diagnosis was produced." in joined(msg)
    assert "2/2 steps" in blocks[-1][1]
    assert msg["text"].startswith("Incomplete.")


def test_langsmith_suffix_only_when_enabled(dup: RunResult, settings: Settings) -> None:
    off = walk_texts(sb.result_message(dup, "q", USER, settings))[-1][1]
    assert "LangSmith" not in off
    on_settings = settings.model_copy(
        update={"langsmith_enabled": True, "langsmith_project": "demo-proj"}
    )
    on = walk_texts(sb.result_message(dup, "q", USER, on_settings))[-1][1]
    assert on.endswith("· LangSmith project `demo-proj`")


def test_result_without_user_has_no_mention(dup: RunResult, settings: Settings) -> None:
    msg = sb.result_message(dup, "q", None, settings)
    assert "<@" not in dump(msg)
    assert "Question: _q_" in joined(msg)


# ---------------------------------------------------------------------------
# escaping
# ---------------------------------------------------------------------------
def test_injected_question_is_escaped_everywhere(dup: RunResult, settings: Settings) -> None:
    mention = f"<@{USER}>"
    messages = [
        sb.working_message(INJECTION, USER),
        sb.result_message(dup, INJECTION, USER, settings),
        sb.error_message(INJECTION, INJECTION),
        sb.refusal_message(INJECTION),
        sb.health_check_failed_message(INJECTION, 3, "Coach Net"),
    ]
    for msg in messages:
        assert_valid(msg)
        assert_no_live_syntax(msg, allow=(mention,))
    # the escaped form is visible instead
    assert "&lt;!channel&gt;" in joined(messages[0])
    # the requester mention is ours and is live
    assert mention in dump(messages[0]) and mention in dump(messages[1])


def test_injected_model_and_case_text_is_escaped(dup: RunResult, settings: Settings) -> None:
    diag = dup.state["diagnosis"].model_copy(
        update={
            "summary": INJECTION,
            "likely_cause": INJECTION,
            "suggested_fix": f"1. {INJECTION}\n2. <!here>",
            "matched_runbook_ids": ["<!here>"],
        }
    )
    state = dict(dup.state)
    state["diagnosis"] = diag
    state["evidence"] = [
        Evidence(source_tool="<!channel>", summary=INJECTION, at=datetime.now(UTC))
    ]
    fake = SimpleNamespace(state=state, tracer=dup.tracer, elapsed_s=1.0)
    for msg in (
        sb.result_message(fake, "q", USER, settings),  # type: ignore[arg-type]
        sb.evidence_message(fake),  # type: ignore[arg-type]
    ):
        assert_valid(msg)
        assert_no_live_syntax(msg, allow=(f"<@{USER}>",))


def test_injected_app_status_text_is_escaped() -> None:
    status = AppStatus(
        app_name="<!channel>app",
        status=AppStatusValue.FAILED,
        replicas_desired=2,
        replicas_running=0,
        checked_at=datetime.now(UTC),
        source="mock",
        detail=INJECTION,
    )
    for msg in (
        sb.health_alert_message(status, "<!here>"),
        sb.health_recovered_message(status, "<!here>", 60),
    ):
        assert_valid(msg)
        assert_no_live_syntax(msg)


# ---------------------------------------------------------------------------
# limits
# ---------------------------------------------------------------------------
def test_huge_inputs_respect_limits(dup: RunResult, settings: Settings) -> None:
    huge = "<&>" * 20_000
    diag = Diagnosis(
        summary=huge,
        likely_cause=huge,
        suggested_fix="\n".join(f"{i}. {huge[:500]}" for i in range(500)),
        matched_runbook_ids=["cnet-001"],
        confidence=0.5,
        needs_human=True,
    )
    state = dict(dup.state)
    state["diagnosis"] = diag
    state["errors"] = []
    fake = SimpleNamespace(state=state, tracer=dup.tracer, elapsed_s=1.0)
    status = dup.state["app_status"]
    for msg in (
        sb.working_message(huge, USER),
        sb.result_message(fake, huge, USER, settings),  # type: ignore[arg-type]
        sb.error_message(huge, huge),
        sb.refusal_message(huge),
        sb.health_alert_message(status.model_copy(update={"detail": huge}), "Coach Net"),
        sb.health_check_failed_message(huge, 1, "Coach Net"),
    ):
        assert_valid(msg)
        assert len(msg["text"]) <= sb.FALLBACK_LIMIT
    # truncation marker is present where text was cut
    assert "…" in joined(sb.result_message(fake, huge, USER, settings))  # type: ignore[arg-type]


def test_header_is_truncated() -> None:
    assert len(sb._header("x" * 1000)["text"]["text"]) == sb.HEADER_LIMIT


def fake_with_evidence(base: RunResult, count: int) -> Any:
    items = [
        Evidence(
            source_tool="search_mule_logs", summary=f"entry {i} " + "z" * 800, at=datetime.now(UTC)
        )
        for i in range(count)
    ]
    state = dict(base.state)
    state["evidence"] = items
    return SimpleNamespace(
        state=state,
        tracer=SimpleNamespace(run_id="abc123", path=Path("traces/abc123.jsonl")),
        elapsed_s=1.0,
    )


def test_evidence_message(dup: RunResult) -> None:
    msg = sb.evidence_message(dup)
    assert_valid(msg)
    text = joined(msg)
    assert f"*Evidence* ({len(dup.state['evidence'])} items)" in text
    assert "• [check_mule_app_status] coachnet-saas-api: RUNNING" in text
    assert "• [get_sf_case] Case 00412345" in text
    assert f"`{dup.tracer.path.name}`" in text and "stored locally on the bot host" in text


def test_evidence_message_200_items_splits_sections(dup: RunResult) -> None:
    fake = fake_with_evidence(dup, 200)
    msg = sb.evidence_message(fake)
    assert_valid(msg)
    sections = [t for k, t in walk_texts(msg) if k == "section"]
    assert len(sections) > 3  # split across several 3000-char sections
    # every line truncated to 300 chars (+ "• ")
    lines = [ln for s in sections[1:] for ln in s.splitlines()]
    assert lines and all(len(ln) <= sb.EVIDENCE_LINE_LIMIT + 2 for ln in lines)
    assert any(ln.endswith("…") for ln in lines)
    assert "abc123.jsonl" in walk_texts(msg)[-1][1]


def test_evidence_message_caps_blocks_and_keeps_trace_footer(dup: RunResult) -> None:
    fake = fake_with_evidence(dup, 5000)
    msg = sb.evidence_message(fake)
    assert_valid(msg)
    assert len(msg["blocks"]) == sb.MAX_BLOCKS
    assert "Output shortened" in joined(msg)
    assert "abc123.jsonl" in walk_texts(msg)[-1][1]


def test_evidence_message_empty(dup: RunResult) -> None:
    fake = fake_with_evidence(dup, 0)
    msg = sb.evidence_message(fake)
    assert_valid(msg)
    assert "No evidence was recorded." in joined(msg)


def test_result_message_caps_blocks_with_many_errors(settings: Settings) -> None:
    r = result("Look at case 00499999", settings)
    from triage.schemas.tools import ToolErrorRecord

    r.state["errors"] = [
        ToolErrorRecord(tool="t", error_type="UpstreamError", message="m" * 400)
        for _ in range(3000)
    ]
    msg = sb.result_message(r, "q", USER, settings)
    assert_valid(msg)
    assert walk_texts(msg)[-1][1].startswith("Evidence in thread")  # footer survives


# ---------------------------------------------------------------------------
# errors, refusals, help
# ---------------------------------------------------------------------------
def test_error_message() -> None:
    msg = sb.error_message("case 00412345", "RuntimeError: boom")
    assert_valid(msg)
    text = joined(msg)
    assert "failed" in text and "RuntimeError: boom" in text
    assert "Nothing was changed" in text and "read-only" in text and "try again" in text
    assert "case 00412345" in text


def test_refusal_message() -> None:
    msg = sb.refusal_message("This channel is not allowed.")
    assert_valid(msg)
    assert len(msg["blocks"]) == 1 and "This channel is not allowed." in joined(msg)


def test_help_message_mock_mode(settings: Settings) -> None:
    msg = sb.help_message(settings)
    assert_valid(msg)
    text = joined(msg)
    assert "Coach Net" in text
    assert "/triage case 00412345 looks like a duplicate" in text
    assert "@triage" in text and "direct message" in text
    assert "Mock mode: answers come from synthetic fixtures" in text
    assert "/triage :app down" in text and "/triage :app healthy" in text
    for state in ("deploying", "restart_loop", "status_error"):
        assert state in text


def test_help_message_live_mode_has_no_demo_commands(settings: Settings) -> None:
    live = settings.model_copy(update={"triage_mode": "live"})
    assert not live.is_mock
    text = joined(sb.help_message(live))
    assert ":app" not in text
    assert "Mock mode" not in text


# ---------------------------------------------------------------------------
# health watcher messages
# ---------------------------------------------------------------------------
def make_status(status: AppStatusValue, **kw: Any) -> AppStatus:
    base: dict[str, Any] = {
        "app_name": "coachnet-saas-api",
        "status": status,
        "replicas_desired": 2,
        "replicas_running": 0,
        "last_deployed_at": datetime(2026, 10, 11, 1, 47, 30, tzinfo=UTC),
        "checked_at": datetime.now(UTC),
        "source": "mock",
        "detail": "Application stopped; 0 of 2 replicas running",
    }
    return AppStatus(**{**base, **kw})


def test_health_alert_message() -> None:
    msg = sb.health_alert_message(make_status(AppStatusValue.STOPPED), "Coach Net")
    assert_valid(msg)
    text = joined(msg)
    assert ":red_circle: *Coach Net Mule app is down*" in text
    assert "STOPPED" in text and "Last deploy: 2026-10-11T01:47:30+00:00" in text
    assert "Detail: Application stopped" in text
    assert "Sync problems reported now are most likely caused by this." in text


def test_health_alert_status_words() -> None:
    def title(status: AppStatus) -> str:
        return sb.health_alert_message(status, "Coach Net")["blocks"][0]["text"]["text"]

    assert "is deploying" in title(make_status(AppStatusValue.DEPLOYING))
    assert "is degraded" in title(make_status(AppStatusValue.DEGRADED, replicas_running=1))
    restart = make_status(AppStatusValue.RUNNING, replicas_running=2, recent_restarts=5)
    assert "restart loop" in title(restart)


def test_health_recovered_message() -> None:
    ok = make_status(AppStatusValue.RUNNING, replicas_running=2, detail=None)
    known = sb.health_recovered_message(ok, "Coach Net", 7 * 60 + 20)
    assert_valid(known)
    assert ":large_green_circle: *Coach Net Mule app recovered*" in joined(known)
    assert "Unhealthy for about 7 minutes." in joined(known)
    assert "Unhealthy" not in joined(sb.health_recovered_message(ok, "Coach Net", None))
    assert "less than a minute" in joined(sb.health_recovered_message(ok, "Coach Net", 20))
    assert "1 minute." in joined(sb.health_recovered_message(ok, "Coach Net", 60))
    assert "3.0 hours" in joined(sb.health_recovered_message(ok, "Coach Net", 3 * 3600))


def test_health_check_failed_message() -> None:
    msg = sb.health_check_failed_message("timeout <after> 15s", 3, "Coach Net")
    assert_valid(msg)
    text = joined(msg)
    assert ":white_circle: *Cannot check Coach Net Mule app health*" in text
    assert "timeout &lt;after&gt; 15s" in text
    assert "3 failed checks in a row" in text and "keep trying" in text
    assert "1 failed check in a row" in joined(sb.health_check_failed_message("x", 1, "Coach Net"))
