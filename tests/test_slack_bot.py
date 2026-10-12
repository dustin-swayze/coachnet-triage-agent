"""Slack bot request handling and health alerts (Phase 6, mock mode).

Invariants protected:
- Fail-closed access: empty allowlists refuse everyone; otherwise channel OR user must
  be listed. Refusals never run the agent.
- Abuse limits: one in-flight run per user, an hourly cap, a global concurrency cap.
- Slack flow: post "working", run, replace with the answer, evidence in the thread.
- A crashing run still replaces the "working" message and releases the user's slot.
- Mock demo switch (:app) changes health for questions and the poller; refused live.
- Health alerts are edge-triggered: one alert, one recovery, one check-failed notice.
- Bolt wiring delivers slash commands, mentions, and DMs to the handler.

No test touches the network: Slack is a fake client; Bolt runs with token checks off.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime

import pytest

from triage.config import Settings
from triage.schemas.tools import AppStatus, AppStatusValue
from triage.slack.bot import (
    RateLimiter,
    SlackTriageBot,
    access_decision,
    build_app,
    clean_question,
    default_runner,
)
from triage.slack.health_watch import HealthEvent, HealthWatch, run_poller

CHANNEL, OTHER_CHANNEL, DM = "C0DEMO1", "C0OTHER", "D0DM001"
USER, STRANGER = "U0DUSTIN", "U0STRANGE"


class FakeClient:
    """Records the Slack Web API calls the bot makes."""

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self.ephemerals: list[dict] = []
        self._n = 0

    def chat_postMessage(self, **kw):  # noqa: N802 - Slack SDK method name
        self._n += 1
        kw["ts"] = f"1700000000.{self._n:06d}"
        self.posts.append(kw)
        return {"ok": True, "ts": kw["ts"], "channel": kw["channel"]}

    def chat_update(self, **kw):  # noqa: N802
        self.updates.append(kw)
        return {"ok": True}

    def chat_postEphemeral(self, **kw):  # noqa: N802
        self.ephemerals.append(kw)
        return {"ok": True}


@pytest.fixture
def slack_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"slack_allowed_channel_ids": CHANNEL, "slack_allowed_user_ids": USER}
    )


def _dump(*messages) -> str:
    return json.dumps(messages, default=str)


def _ask(
    bot, client, *, channel=CHANNEL, user=USER, text="case 00412345", source="slash", thread_ts=None
):
    private: list[dict] = []
    bot.handle(
        client=client,
        channel=channel,
        user=user,
        text=text,
        source=source,
        reply_private=private.append,
        thread_ts=thread_ts,
    )
    return private


# --------------------------------------------------------------------------- access
def test_empty_allowlists_refuse_everyone(settings):
    """Fail-closed: no allowlist configured means nobody is served."""
    calls = []
    bot = SlackTriageBot(settings, runner=lambda *a: calls.append(a))
    client = FakeClient()
    private = _ask(bot, client)
    assert calls == [] and client.posts == []
    assert "no allowlist" in _dump(private)


@pytest.mark.parametrize(
    ("channel", "user", "allowed"),
    [
        (CHANNEL, STRANGER, True),  # approved channel, anyone in it
        (DM, USER, True),  # approved user in a DM
        (OTHER_CHANNEL, USER, False),  # approved user cannot pull answers into other channels
        (OTHER_CHANNEL, STRANGER, False),
        (DM, STRANGER, False),
    ],
)
def test_access_is_channel_or_user(slack_settings, channel, user, allowed):
    assert (access_decision(slack_settings, channel, user) is None) is allowed


def test_refused_request_never_runs_agent(slack_settings):
    calls = []
    bot = SlackTriageBot(slack_settings, runner=lambda *a: calls.append(a))
    client = FakeClient()
    private = _ask(bot, client, channel=OTHER_CHANNEL, user=STRANGER)
    assert calls == [] and client.posts == [] and client.updates == []
    assert "channel" in _dump(private)


# --------------------------------------------------------------------------- flow
def test_slash_command_full_flow_with_real_agent(slack_settings):
    """Working message, then the answer replaces it, then evidence in its thread."""
    bot = SlackTriageBot(slack_settings)
    client = FakeClient()
    private = _ask(bot, client, text="case 00412345 looks like a duplicate")

    assert private == []
    working, evidence = client.posts
    assert working["channel"] == CHANNEL and working["thread_ts"] is None
    (update,) = client.updates
    assert update["ts"] == working["ts"]
    assert "cnet-001" in _dump(update)
    assert evidence["thread_ts"] == working["ts"]
    assert "get_sf_case" in _dump(evidence)


def test_mention_replies_in_the_mention_thread(slack_settings):
    bot = SlackTriageBot(slack_settings)
    client = FakeClient()
    _ask(
        bot,
        client,
        text="<@U0BOT123> case 00412400 never reached the queue",
        source="mention",
        thread_ts="1699999999.000100",
    )
    working, evidence = client.posts
    assert working["thread_ts"] == "1699999999.000100"
    assert evidence["thread_ts"] == "1699999999.000100"
    assert "cnet-002" in _dump(client.updates)


def test_requester_and_interface_recorded_in_trace(slack_settings):
    """The local audit trail says who asked and that it came from Slack."""
    result = default_runner("case 00412345", slack_settings, USER)
    start = next(e for e in result.tracer.events if e["event"] == "run_start")
    assert start["requester"] == USER and start["interface"] == "slack"


def test_injected_slack_syntax_is_never_posted_raw(slack_settings):
    bot = SlackTriageBot(slack_settings)
    client = FakeClient()
    _ask(bot, client, text="<!channel> case 00412345 <https://evil.example|click here>")
    payload = _dump(client.posts, client.updates)
    assert "<!channel>" not in payload
    assert "<https://evil.example" not in payload


def test_crashing_run_replaces_working_message_and_releases_slot(slack_settings):
    def boom(*_):
        raise RuntimeError("kaboom")

    bot = SlackTriageBot(slack_settings, runner=boom)
    client = FakeClient()
    _ask(bot, client)
    (update,) = client.updates
    assert "kaboom" in _dump(update)
    assert bot.limiter.try_start(USER) is None, "user slot was not released"


def test_help_is_private_and_runs_nothing(slack_settings):
    calls = []
    bot = SlackTriageBot(slack_settings, runner=lambda *a: calls.append(a))
    client = FakeClient()
    for text in ("", "help", "<@U0BOT123> help"):
        private = _ask(bot, client, text=text)
        assert private and "/triage" in _dump(private)
    assert calls == [] and client.posts == []


def test_clean_question_strips_mentions():
    assert clean_question("<@U0BOT123> case 00412345 <@W0ABC|bob>") == "case 00412345"


# --------------------------------------------------------------------------- mock switch
def test_app_switch_changes_health_for_questions(slack_settings):
    bot = SlackTriageBot(slack_settings)
    client = FakeClient()
    private = _ask(bot, client, text=":app down")
    assert bot.settings.mock_app_state == "down"
    assert "down" in _dump(private) and client.posts == []
    _ask(bot, client, text="case 00412345")
    assert "cnet-007" in _dump(client.updates), "question did not see the outage"


@pytest.mark.parametrize("text", [":app", ":app sideways", ":app down now"])
def test_app_switch_rejects_bad_usage(slack_settings, text):
    bot = SlackTriageBot(slack_settings)
    private = _ask(bot, FakeClient(), text=text)
    assert "Usage" in _dump(private) and bot.settings.mock_app_state == "healthy"


def test_app_switch_refused_in_live_mode(slack_settings):
    bot = SlackTriageBot(slack_settings.model_copy(update={"triage_mode": "live"}))
    private = _ask(bot, FakeClient(), text=":app down")
    assert "mock mode" in _dump(private)
    assert bot.settings.mock_app_state == "healthy"


# --------------------------------------------------------------------------- limits
def test_hourly_cap_per_user():
    t = [0.0]
    lim = RateLimiter(per_user_per_hour=2, clock=lambda: t[0])
    for _ in range(2):
        assert lim.try_start("U1") is None
        lim.finish("U1")
    assert "limit" in lim.try_start("U1")
    assert lim.try_start("U2") is None, "one user's cap must not block another"
    t[0] = 3601.0
    lim.finish("U2")
    assert lim.try_start("U1") is None, "cap should reset after an hour"


def test_one_inflight_run_per_user_and_global_cap():
    lim = RateLimiter(per_user_per_hour=99, max_concurrent=2)
    assert lim.try_start("U1") is None
    assert "already" in lim.try_start("U1")
    assert lim.try_start("U2") is None
    assert "several" in lim.try_start("U3")
    lim.finish("U1")
    assert lim.try_start("U3") is None


def test_rate_limited_request_is_refused_privately(slack_settings):
    bot = SlackTriageBot(slack_settings, limiter=RateLimiter(per_user_per_hour=1))
    client = FakeClient()
    _ask(bot, client)
    private = _ask(bot, client)
    assert "limit" in _dump(private)
    assert len(client.updates) == 1


# --------------------------------------------------------------------------- health watch
def _status(state: AppStatusValue, running: int = 2) -> AppStatus:
    return AppStatus(
        app_name="coachnet-saas-api",
        status=state,
        replicas_desired=2,
        replicas_running=running,
        checked_at=datetime.now(UTC),
        source="mock",
    )


UP, DOWN = _status(AppStatusValue.RUNNING), _status(AppStatusValue.STOPPED, running=0)


def test_watch_alerts_once_and_reports_recovery_duration():
    t = [100.0]
    w = HealthWatch(clock=lambda: t[0])
    assert w.observe(UP) is None, "first healthy observation is silent"
    alert = w.observe(DOWN)
    assert alert.kind == "alert"
    t[0] = 400.0
    assert w.observe(DOWN) is None, "still down: no repeat alert"
    rec = w.observe(UP)
    assert rec.kind == "recovered" and rec.down_for_seconds == pytest.approx(300.0)
    assert w.observe(UP) is None


def test_watch_alerts_if_first_observation_is_down():
    assert HealthWatch().observe(DOWN).kind == "alert"


def test_watch_check_failures_alert_once_at_threshold():
    w = HealthWatch(failure_threshold=3)
    assert w.observe(None, "401") is None
    assert w.observe(None, "401") is None
    ev = w.observe(None, "401")
    assert ev.kind == "check_failed" and ev.consecutive_failures == 3
    assert w.observe(None, "401") is None, "no repeat after the threshold"
    assert w.observe(UP) is None and w.consecutive_failures == 0


def test_poller_posts_alert_for_mock_outage(settings):
    events: list[HealthEvent] = []
    stop = threading.Event()

    def on_event(ev):
        events.append(ev)
        stop.set()

    from triage.tools import get_tools

    run_poller(
        get_settings=lambda: settings.model_copy(update={"mock_app_state": "down"}),
        get_tools=get_tools,
        on_event=on_event,
        stop=stop,
    )
    assert [e.kind for e in events] == ["alert"]


def test_health_events_go_to_the_alert_channel(slack_settings):
    bot = SlackTriageBot(slack_settings.model_copy(update={"slack_alert_channel_id": "C0ALERTS"}))
    client = FakeClient()
    bot.post_health_event(client, HealthEvent("alert", status=DOWN))
    bot.post_health_event(client, HealthEvent("recovered", status=UP, down_for_seconds=120))
    bot.post_health_event(client, HealthEvent("check_failed", detail="401", consecutive_failures=3))
    assert [p["channel"] for p in client.posts] == ["C0ALERTS"] * 3
    quiet = SlackTriageBot(slack_settings)
    quiet.post_health_event(client, HealthEvent("alert", status=DOWN))
    assert len(client.posts) == 3, "no alert channel configured: nothing posted"


# --------------------------------------------------------------------------- Bolt wiring
class RecordingBot(SlackTriageBot):
    def __init__(self, settings):
        super().__init__(settings)
        self.calls: list[dict] = []

    def handle(self, **kw):
        self.calls.append(kw)


def _offline_authorize(**_kwargs):
    """Stands in for Bolt's auth.test call so no request ever reaches slack.com."""
    from slack_bolt.authorization import AuthorizeResult

    return AuthorizeResult(
        enterprise_id=None,
        team_id="T0TEAM",
        bot_user_id="U0BOT123",
        bot_id="B0BOT",
        bot_token="xoxb-test-not-real",
    )


def _bolt(slack_settings):
    from slack_bolt.request import BoltRequest

    bot = RecordingBot(slack_settings)
    app = build_app(bot, authorize=_offline_authorize)
    return bot, app, BoltRequest


def test_bolt_routes_slash_command(slack_settings):
    bot, app, BoltRequest = _bolt(slack_settings)
    body = {
        "command": "/triage",
        "text": "case 00412345",
        "channel_id": CHANNEL,
        "user_id": USER,
        "team_id": "T0TEAM",
        "response_url": "https://hooks.slack.com/x",
        "trigger_id": "1.2.3",
    }
    resp = app.dispatch(BoltRequest(body=body, mode="socket_mode"))
    assert resp.status == 200
    (call,) = bot.calls
    assert (call["channel"], call["user"], call["text"], call["source"]) == (
        CHANNEL,
        USER,
        "case 00412345",
        "slash",
    )


def _event(app, BoltRequest, event):
    body = {
        "type": "event_callback",
        "team_id": "T0TEAM",
        "api_app_id": "A0APP",
        "event": event,
        "event_id": "Ev1",
        "event_time": 1700000000,
        "authorizations": [{"user_id": "U0BOT123", "team_id": "T0TEAM", "is_bot": True}],
    }
    return app.dispatch(BoltRequest(body=body, mode="socket_mode"))


def test_bolt_routes_mention_with_thread(slack_settings):
    bot, app, BoltRequest = _bolt(slack_settings)
    _event(
        app,
        BoltRequest,
        {
            "type": "app_mention",
            "channel": CHANNEL,
            "user": USER,
            "text": "<@U0BOT123> case 00412345",
            "ts": "1700000000.000001",
        },
    )
    (call,) = bot.calls
    assert call["source"] == "mention" and call["thread_ts"] == "1700000000.000001"


def test_bolt_routes_dms_but_ignores_bot_messages_and_edits(slack_settings):
    bot, app, BoltRequest = _bolt(slack_settings)
    _event(
        app,
        BoltRequest,
        {
            "type": "message",
            "channel_type": "im",
            "channel": DM,
            "user": USER,
            "text": "case 00412345",
            "ts": "1.1",
        },
    )
    _event(
        app,
        BoltRequest,
        {
            "type": "message",
            "channel_type": "im",
            "channel": DM,
            "bot_id": "B0BOT",
            "text": "my own reply",
            "ts": "1.2",
        },
    )
    _event(
        app,
        BoltRequest,
        {
            "type": "message",
            "channel_type": "im",
            "channel": DM,
            "subtype": "message_changed",
            "ts": "1.3",
        },
    )
    _event(
        app,
        BoltRequest,
        {
            "type": "message",
            "channel_type": "channel",
            "channel": CHANNEL,
            "user": USER,
            "text": "chatter",
            "ts": "1.4",
        },
    )
    assert [c["source"] for c in bot.calls] == ["dm"]


# --------------------------------------------------------------------------- review-sweep fixes
def test_allowlisted_user_in_other_channel_is_told_to_dm(slack_settings):
    """Answers contain case details; they must not land in an unapproved channel."""
    calls = []
    bot = SlackTriageBot(slack_settings, runner=lambda *a: calls.append(a))
    client = FakeClient()
    private = _ask(bot, client, channel=OTHER_CHANNEL, user=USER)
    assert calls == [] and client.posts == []
    assert "direct message" in _dump(private)


def test_app_switch_requires_allowlisted_user(slack_settings):
    """People in an approved channel can ask, but cannot flip the demo app health."""
    bot = SlackTriageBot(slack_settings)
    private = _ask(bot, FakeClient(), channel=CHANNEL, user=STRANGER, text=":app down")
    assert bot.settings.mock_app_state == "healthy"
    assert "allowlisted users" in _dump(private)


class FailingClient(FakeClient):
    def chat_update(self, **kw):  # noqa: N802
        raise RuntimeError("ratelimited")


def test_slack_api_error_is_reported_privately_and_slot_released(slack_settings):
    bot = SlackTriageBot(slack_settings)
    private = _ask(bot, FailingClient())
    assert "Slack error" in _dump(private)
    assert bot.limiter.try_start(USER) is None
