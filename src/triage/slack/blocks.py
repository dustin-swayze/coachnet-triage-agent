"""Slack message formatting: pure functions that turn agent results into Block Kit.

Why this module has no Slack SDK import and no network: formatting is where the
security bugs hide (a log line containing ``<!channel>`` that pings the whole
workspace), so it is kept pure and unit-tested on its own.

Rules every function here follows
- Every dynamic string (question, case fields, log text, model prose, errors, runbook
  text) goes through ``escape()``. Only text this module writes itself may contain Slack
  syntax, and the only such thing is the requester mention ``<@U...>``, whose id is
  validated first.
- Slack limits are enforced here, not by the caller: section/context text <= 3000
  chars, header <= 150, at most 50 blocks. Truncation ends with an ellipsis.
- Every message carries a non-empty plain-text ``text`` (used for notifications and
  screen readers). It is escaped too, because Slack parses it as mrkdwn.
- No interactive elements. The agent is read-only; the bot only shows information.

The answer shapes mirror ``triage.graph.nodes.render_response`` (the CLI rendering),
chosen by the same ``response_kind`` function so the two can never disagree.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, TypedDict

from triage.graph.nodes import response_kind
from triage.schemas.tools import AppStatusValue

if TYPE_CHECKING:
    from triage.config import Settings
    from triage.graph.build import RunResult
    from triage.schemas.tools import AppStatus


class SlackMessage(TypedDict):
    """What the bot passes to chat_postMessage / chat_update / respond."""

    text: str  # plain-text fallback (notifications, screen readers); never empty
    blocks: list[dict[str, Any]]


# Slack Block Kit limits.
SECTION_LIMIT = 3000
HEADER_LIMIT = 150
MAX_BLOCKS = 50
QUESTION_QUOTE_LIMIT = 200
EVIDENCE_LINE_LIMIT = 300
FALLBACK_LIMIT = 300

_USER_ID = re.compile(r"[UW][A-Z0-9]{2,20}")
_PARTIAL_ENTITY = re.compile(r"&[a-z]{0,3}$")
_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Escaping and limits
# ---------------------------------------------------------------------------
def escape(text: str) -> str:
    """Slack mrkdwn escaping. ``&`` first so the entities we add are not re-escaped.

    After this, ``<!channel>``, ``<@U123>`` and ``<https://x|click>`` arrive in Slack as
    literal text: no pings, no links.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def truncate(text: str, limit: int) -> str:
    """Cut to ``limit`` characters, ending with an ellipsis when something was removed.

    Truncate *after* escaping. If the cut lands inside an entity such as ``&am`` the
    stub is dropped (a bare ``&`` cannot occur in escaped text, so any trailing
    ``&xx`` is a stub).
    """
    if len(text) <= limit:
        return text
    cut = text[: max(limit - 1, 0)]
    cut = _PARTIAL_ENTITY.sub("", cut).rstrip()
    return cut + "…"


def _oneline(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _safe(text: str, limit: int = SECTION_LIMIT, *, oneline: bool = False) -> str:
    """Escape, optionally collapse to one line, then truncate."""
    raw = _oneline(text) if oneline else text
    return truncate(escape(raw), limit)


def _mention(user_id: str | None) -> str | None:
    """``<@U123>`` only for a well-formed Slack user id; anything else gets no ping."""
    if user_id and _USER_ID.fullmatch(user_id):
        return f"<@{user_id}>"
    return None


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------
def _section(text: str) -> dict[str, Any]:
    body = truncate(text, SECTION_LIMIT) or "(empty)"
    return {"type": "section", "text": {"type": "mrkdwn", "text": body}}


def _context(*texts: str) -> dict[str, Any]:
    elements = [
        {"type": "mrkdwn", "text": truncate(t, SECTION_LIMIT) or "(empty)"} for t in texts if t
    ]
    return {"type": "context", "elements": elements[:10]}


def _divider() -> dict[str, Any]:
    return {"type": "divider"}


def _header(text: str) -> dict[str, Any]:
    # plain_text is never parsed as mrkdwn, so only trusted text (config) goes here.
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": truncate(text, HEADER_LIMIT), "emoji": True},
    }


def _cap_blocks(blocks: list[dict[str, Any]], tail: int = 0) -> list[dict[str, Any]]:
    """At most ``MAX_BLOCKS`` blocks. The last ``tail`` blocks (footer) always survive;
    when the middle is cut, a one-line note says so."""
    if len(blocks) <= MAX_BLOCKS:
        return blocks
    split = len(blocks) - tail
    head, foot = blocks[:split], blocks[split:]
    keep = MAX_BLOCKS - len(foot) - 1
    note = _context(
        f":scissors: Output shortened: {len(head) - keep} more blocks omitted (Slack limit)."
    )
    return [*head[:keep], note, *foot]


def _bullet_sections(lines: list[str]) -> list[dict[str, Any]]:
    """Pack already-escaped bullet lines into section blocks of <= 3000 chars."""
    sections: list[dict[str, Any]] = []
    buf: list[str] = []
    size = 0
    for raw in lines:
        line = truncate(raw, SECTION_LIMIT)
        if buf and size + len(line) + 1 > SECTION_LIMIT:
            sections.append(_section("\n".join(buf)))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        sections.append(_section("\n".join(buf)))
    return sections


def _message(text: str, blocks: list[dict[str, Any]], tail: int = 0) -> SlackMessage:
    """``text`` is raw (unescaped); it is escaped and shortened here."""
    fallback = truncate(escape(_oneline(text)), FALLBACK_LIMIT) or "Triage update"
    return SlackMessage(text=fallback, blocks=_cap_blocks(blocks, tail))


def _ask_line(question: str, user_id: str | None) -> str:
    quoted = _safe(question, QUESTION_QUOTE_LIMIT, oneline=True)
    who = _mention(user_id)
    return f"{who} asked: _{quoted}_" if who else f"Question: _{quoted}_"


def _last_deploy(app: AppStatus) -> str:
    when = app.last_deployed_at.isoformat(timespec="seconds") if app.last_deployed_at else "unknown"
    return f"Last deploy: {when}"


# ---------------------------------------------------------------------------
# Working message
# ---------------------------------------------------------------------------
def working_message(question: str, user_id: str | None) -> SlackMessage:
    """Posted immediately, then replaced by ``result_message`` (a run takes seconds)."""
    who = _mention(user_id)
    lines = _safe(question, QUESTION_QUOTE_LIMIT * 5).splitlines() or [""]
    quoted = "\n".join("> " + ln for ln in lines)
    head = f":mag: {who} asked:" if who else ":mag: Question:"
    blocks = [
        _section(f"{head}\n{quoted}"),
        _context("Looking into it… (checks app health, the case, the Mule logs, and the runbook)"),
    ]
    return _message("Looking into it…", blocks, tail=1)


# ---------------------------------------------------------------------------
# Result message
# ---------------------------------------------------------------------------
def result_message(
    result: RunResult, question: str, user_id: str | None, settings: Settings
) -> SlackMessage:
    """The answer. Mirrors ``render_response`` for the CLI, shape chosen by ``response_kind``."""
    state = result.state
    incomplete = bool(state.get("incomplete"))
    kind = response_kind(state, incomplete=incomplete)
    app, parsed, diag = state.get("app_status"), state.get("parsed"), state.get("diagnosis")
    partner = escape(settings.partner_name)
    code = escape(settings.partner_code)

    blocks: list[dict[str, Any]] = [_context(_ask_line(question, user_id))]
    if incomplete:
        reason = _safe(
            str(state.get("incomplete_reason") or "a limit was reached"), 500, oneline=True
        )
        blocks.append(
            _section(f":hourglass: *Incomplete:* {reason}. Showing what was found so far.")
        )

    if kind == "alert":
        assert app is not None
        lines = [
            f":red_circle: *{partner} Mule app is not healthy*",
            _safe(app.summary),
            _last_deploy(app),
        ]
        if app.detail:
            lines.append(f"Detail: {_safe(app.detail, 1000)}")
        blocks.append(_section("\n".join(lines)))
        blocks.append(
            _section(
                "Any sync problem reported right now is most likely caused by this. Case lookup "
                "was skipped.\n*Next step (human, in Runtime Manager):* check deployment logs, "
                "then redeploy or restart. See runbook `cnet-007`."
            )
        )
        fallback = f"{settings.partner_name} Mule app is not healthy: {app.summary}"
    elif kind == "health":
        if app is None or app.status is AppStatusValue.UNKNOWN:
            summary = _safe(app.summary) if app else "unknown"
            blocks.append(
                _section(
                    f":white_circle: *The {partner} Mule app status is unknown:* {summary}\n"
                    "The status check itself failed, so this is not confirmed."
                )
            )
            fallback = f"{settings.partner_name} Mule app status unknown: the check failed"
        else:
            blocks.append(
                _section(
                    f":large_green_circle: The {partner} Mule app looks healthy: "
                    f"{_safe(app.summary)}."
                )
            )
            fallback = f"{settings.partner_name} Mule app looks healthy: {app.summary}"
    elif kind == "ask_for_case":
        blocks.append(
            _section(
                "I need a case to look at. Give me a Salesforce case number (8 digits) or a "
                "HubSpot ticket id, and optionally when it happened."
            )
        )
        blocks.append(
            _context(f"App health right now: {_safe(app.summary) if app else 'unknown'}.")
        )
        fallback = "I need a case number (8 digits) or a HubSpot ticket id to look at."
    elif kind == "nothing_found":
        assert parsed is not None
        ref = parsed.case_number or parsed.external_id or parsed.correlation_id or "that"
        errors = [
            f"• `{_safe(e.tool, 80, oneline=True)}`: {_safe(e.error_type, 40, oneline=True)}: "
            f"{_safe(e.message, 300, oneline=True)}"
            for e in state.get("errors") or []
        ]
        blocks.append(
            _section(
                f"I found nothing for `{_safe(ref, 100, oneline=True)}` in {partner} ({code}) data:"
            )
        )
        blocks.extend(_bullet_sections(errors))
        blocks.append(
            _section(
                "No diagnosis is possible without a case or log transaction. Check the number, "
                f"that it is a {partner} case, and the time window. If a lookup timed out or "
                "failed upstream, try again shortly."
            )
        )
        fallback = f"Nothing found for {ref} in {settings.partner_name} data"
    elif kind == "diagnosis":
        assert diag is not None
        blocks.append(_section(f"*Diagnosis*\n{_safe(diag.summary, 2500)}"))
        blocks.append(_section(f"*Likely cause*\n{_safe(diag.likely_cause.strip(), 2500)}"))
        blocks.append(_section(f"*Suggested fix*\n{_safe(diag.suggested_fix.strip(), 2500)}"))
        meta = [f"Confidence: {diag.confidence:.0%}"]
        if diag.matched_runbook_ids:
            ids = ", ".join(f"`{_safe(i, 40, oneline=True)}`" for i in diag.matched_runbook_ids)
            meta.append(f"Runbook: {ids}")
        if diag.needs_human:
            meta.append(":warning: needs a human (write actions)")
        blocks.append(_context(" · ".join(meta)))
        fallback = f"Diagnosis: {diag.summary}"
    else:
        blocks.append(
            _section(
                "No diagnosis was produced. The evidence gathered so far is in the thread below."
            )
        )
        fallback = "No diagnosis was produced."

    run_id = _safe(str(result.tracer.run_id), 40, oneline=True)
    footer = (
        f"Evidence in thread · {state.get('steps_used', 0)}/{state.get('step_cap')} steps · "
        f"{result.elapsed_s:.1f}s · run `{run_id}`"
    )
    if settings.langsmith_enabled:
        footer += f" · LangSmith project `{_safe(settings.langsmith_project, 80, oneline=True)}`"
    blocks.append(_divider())
    blocks.append(_context(footer))

    if incomplete:
        fallback = f"Incomplete. {fallback}"
    return _message(fallback, blocks, tail=2)


# ---------------------------------------------------------------------------
# Evidence thread
# ---------------------------------------------------------------------------
def evidence_message(result: RunResult) -> SlackMessage:
    """Posted in the answer's thread: everything the agent gathered, with provenance."""
    evidence = result.state.get("evidence") or []
    run_id = str(result.tracer.run_id)
    blocks: list[dict[str, Any]] = [_section(f"*Evidence* ({len(evidence)} items)")]
    if evidence:
        lines = [
            "• " + _safe(f"[{e.source_tool}] {e.summary}", EVIDENCE_LINE_LIMIT, oneline=True)
            for e in evidence
        ]
        blocks.extend(_bullet_sections(lines))
    else:
        blocks.append(_section("No evidence was recorded."))
    blocks.append(
        _context(
            f"Trace file `{_safe(result.tracer.path.name, 100, oneline=True)}` · the trace is "
            "stored locally on the bot host, not in Slack."
        )
    )
    return _message(f"Evidence for run {run_id}: {len(evidence)} items", blocks, tail=1)


# ---------------------------------------------------------------------------
# Errors, refusals, help
# ---------------------------------------------------------------------------
def error_message(question: str, detail: str) -> SlackMessage:
    """The run crashed unexpectedly. Nothing was changed: the agent is read-only."""
    blocks = [
        _section(
            ":x: *The triage run failed.*\n"
            f"Detail: {_safe(detail, 300, oneline=True)}\n"
            "Nothing was changed anywhere (the agent is read-only). Please try again; if it "
            "keeps failing, tell the bot owner."
        ),
        _context(f"Question: _{_safe(question, QUESTION_QUOTE_LIMIT, oneline=True)}_"),
    ]
    return _message("The triage run failed. Nothing was changed. Please try again.", blocks)


def refusal_message(reason: str) -> SlackMessage:
    """Not allowed here / rate limited. One plain, polite section."""
    safe = _safe(reason, 1000)
    return _message(reason or "Request refused.", [_section(f":no_entry: {safe}")])


def help_message(settings: Settings) -> SlackMessage:
    partner = escape(settings.partner_name)
    blocks: list[dict[str, Any]] = [
        _header(f"Triage agent for {settings.partner_name}"),
        _section(
            f"I do read-only triage of {partner} sync problems (Salesforce, MuleSoft, HubSpot). "
            "I check the Mule app health, look up the case, search the Mule logs, match the "
            "runbook, and suggest a fix. I never change anything."
        ),
        _section(
            "*How to ask*\n"
            "• Slash command: `/triage case 00412345 looks like a duplicate`\n"
            "• Mention me in an allowed channel: `@triage why didn't case 00412345 sync?`\n"
            "• Send me a direct message."
        ),
        _section(
            "*Example questions*\n"
            "• `case 00412345 looks like a duplicate, what happened?`\n"
            "• `HubSpot ticket hs-55501 never reached Salesforce`\n"
            "• `is the Mule app up?`\n"
            "Give a Salesforce case number (8 digits) or a HubSpot ticket id."
        ),
    ]
    if settings.is_mock:
        blocks.append(
            _section(
                ":test_tube: Mock mode: answers come from synthetic fixtures, not real data.\n"
                "Demo commands switch the simulated app health:\n"
                "• `/triage :app down`\n"
                "• `/triage :app healthy`\n"
                "Other states: `deploying`, `restart_loop`, `status_error`."
            )
        )
    return _message(f"Triage agent for {settings.partner_name}: how to ask", blocks)


# ---------------------------------------------------------------------------
# Health watcher
# ---------------------------------------------------------------------------
def _status_phrase(status: AppStatus) -> str:
    if status.status in (AppStatusValue.STOPPED, AppStatusValue.FAILED):
        return "down"
    if status.status is AppStatusValue.DEPLOYING:
        return "deploying"
    if status.status is AppStatusValue.DEGRADED:
        return "degraded"
    if status.status is AppStatusValue.RUNNING:
        return "in a restart loop" if status.recent_restarts >= 3 else "down"
    return str(status.status).lower()


def health_alert_message(status: AppStatus, partner_name: str) -> SlackMessage:
    partner = escape(partner_name)
    phrase = _status_phrase(status)
    lines = [
        f":red_circle: *{partner} Mule app is {phrase}*",
        _safe(status.summary),
        _last_deploy(status),
    ]
    if status.detail:
        lines.append(f"Detail: {_safe(status.detail, 1000)}")
    blocks = [
        _section("\n".join(lines)),
        _context("Sync problems reported now are most likely caused by this."),
    ]
    return _message(f"{partner_name} Mule app is {phrase}", blocks)


def health_recovered_message(
    status: AppStatus, partner_name: str, down_for_seconds: float | None
) -> SlackMessage:
    partner = escape(partner_name)
    lines = [f":large_green_circle: *{partner} Mule app recovered*", _safe(status.summary)]
    if down_for_seconds is not None and down_for_seconds >= 0:
        minutes = down_for_seconds / 60
        if minutes < 1:
            span = "less than a minute"
        elif minutes < 120:
            n = round(minutes)
            span = f"{n} minute{'s' if n != 1 else ''}"
        else:
            span = f"{minutes / 60:.1f} hours"
        lines.append(f"Unhealthy for about {span}.")
    return _message(f"{partner_name} Mule app recovered", [_section("\n".join(lines))])


def health_check_failed_message(
    detail: str, consecutive_failures: int, partner_name: str
) -> SlackMessage:
    partner = escape(partner_name)
    plural = "s" if consecutive_failures != 1 else ""
    blocks = [
        _section(
            f":white_circle: *Cannot check {partner} Mule app health*\n"
            f"Detail: {_safe(detail, 500, oneline=True)}\n"
            f"{consecutive_failures} failed check{plural} in a row. I will keep trying; "
            "app health is unknown until a check succeeds."
        )
    ]
    return _message(f"Cannot check {partner_name} Mule app health", blocks)
