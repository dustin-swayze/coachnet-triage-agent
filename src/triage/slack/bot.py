"""Slack interface for the triage agent (Phase 6). Socket Mode, read-only.

The bot is a thin layer over ``run_triage``: the same graph, tools, limits, guards, and
traces as the CLI. It adds four things only:

1. **Fail-closed access.** Answers are posted only where they are allowed to be seen:
   in an allowlisted channel, or in a direct message with an allowlisted user. Being
   on the user allowlist does not let someone summon answers into other channels.
   Both allowlists empty means the bot refuses everyone.
2. **Abuse limits.** One in-flight run per user, a per-user hourly cap, and a global cap
   on concurrent runs, so one person cannot exhaust the model budget or the tool pool.
3. **Slack's 3-second rule.** Slack needs an acknowledgement within 3 seconds; a triage
   takes up to ~30. The bot acks, posts "Looking into it", runs the agent, then replaces
   that message with the answer and puts the evidence in the thread.
4. **Escaped output.** All text goes through ``blocks.escape`` so nothing a user, a log
   line, or the model writes can ping @channel or create links.

Why Socket Mode: the bot opens an outbound websocket to Slack, so it needs no public
URL, no inbound firewall rule, and runs the same on a laptop, behind the corporate
network, or on Azure.

Handler logic lives in ``SlackTriageBot`` and talks to Slack through a small client
surface (``chat_postMessage``, ``chat_update``), so tests drive it with a fake client
and never touch the network. ``build_app`` is the only Bolt-specific code.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from triage.config import Settings
from triage.graph.build import RunResult, run_triage
from triage.llm import get_chat_model
from triage.slack import blocks
from triage.slack.health_watch import HealthEvent, HealthWatch, run_poller
from triage.tools import get_tools

Source = Literal["slash", "mention", "dm"]
MOCK_APP_STATES = ("healthy", "down", "deploying", "restart_loop", "status_error")
_MENTION = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>")
Runner = Callable[[str, Settings, str | None], RunResult]
log = logging.getLogger(__name__)


def default_runner(question: str, settings: Settings, requester: str | None) -> RunResult:
    """Run one triage exactly as the CLI does, tagged with the Slack requester."""
    return run_triage(
        question,
        settings,
        tools=get_tools(settings),
        llm=get_chat_model(settings),
        requester=requester,
        interface="slack",
    )


def clean_question(text: str) -> str:
    """Strip bot mentions and surrounding whitespace from a Slack message."""
    return _MENTION.sub("", text or "").strip()


@dataclass
class RateLimiter:
    """Per-user hourly cap + one in-flight run per user + a global concurrency cap."""

    per_user_per_hour: int
    max_concurrent: int = 4
    clock: Callable[[], float] = time.monotonic
    _history: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    _inflight: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def try_start(self, user: str) -> str | None:
        """Reserve a run for ``user``. Returns None if allowed, else the refusal reason."""
        now = self.clock()
        with self._lock:
            if user in self._inflight:
                return "You already have a triage running. I'll answer that one first."
            if len(self._inflight) >= self.max_concurrent:
                return "I'm handling several triages right now. Try again in a minute."
            hist = self._history[user]
            while hist and now - hist[0] > 3600:
                hist.popleft()
            if len(hist) >= self.per_user_per_hour:
                return (
                    f"You've hit the limit of {self.per_user_per_hour} triages per hour. "
                    "Try again later."
                )
            hist.append(now)
            self._inflight.add(user)
            return None

    def finish(self, user: str) -> None:
        with self._lock:
            self._inflight.discard(user)


def access_decision(settings: Settings, channel: str | None, user: str | None) -> str | None:
    """None if allowed; otherwise why not. Fail-closed: empty allowlists allow nobody.

    Answers contain case details, so the rule is about where the answer will be seen:
    an allowlisted channel, or a DM (channel id starting with "D") with an allowlisted
    user. An allowlisted user in some other channel is told to DM the bot instead.
    """
    channels, users = settings.slack_allowed_channels, settings.slack_allowed_users
    if not channels and not users:
        return "This bot has no allowlist configured yet, so it isn't answering anyone."
    if channel and channel in channels:
        return None
    is_dm = bool(channel and channel.startswith("D"))
    if is_dm and user and user in users:
        return None
    if is_dm:
        return "You're not on this bot's allowlist. Ask the bot owner to add you."
    if user and user in users:
        return (
            "I only answer in approved channels, because answers contain case details. "
            "Send me a direct message instead."
        )
    return "I don't answer in this channel. Use an approved channel or ask the bot owner."


class SlackTriageBot:
    """Slack request handling, independent of Bolt so it can be tested offline."""

    def __init__(
        self,
        settings: Settings,
        *,
        runner: Runner = default_runner,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.limiter = limiter or RateLimiter(settings.slack_max_runs_per_user_per_hour)

    # ------------------------------------------------------------------ entry point
    def handle(
        self,
        *,
        client: Any,
        channel: str,
        user: str | None,
        text: str,
        source: Source,
        reply_private: Callable[[blocks.SlackMessage], None],
        thread_ts: str | None = None,
    ) -> None:
        """Handle one request end to end. Never raises: failures become a Slack reply."""
        question = clean_question(text)
        refusal = access_decision(self.settings, channel, user)
        if refusal:
            reply_private(blocks.refusal_message(refusal))
            return

        if question.lower() in ("", "help", "?"):
            reply_private(blocks.help_message(self.settings))
            return
        if question.startswith(":app"):
            if not user or user not in self.settings.slack_allowed_users:
                reply_private(
                    blocks.refusal_message(
                        "Only the bot's allowlisted users can change the demo app health."
                    )
                )
                return
            reply_private(self._switch_mock_app(question))
            return

        requester = user or "unknown"
        limited = self.limiter.try_start(requester)
        if limited:
            reply_private(blocks.refusal_message(limited))
            return
        try:
            self._run_and_reply(client, channel, user, question, thread_ts)
        except Exception as exc:  # noqa: BLE001 - Slack API errors must not escape to Bolt
            log.warning("slack reply failed: %s: %s", type(exc).__name__, exc)
            try:
                reply_private(blocks.error_message(question, f"Slack error: {type(exc).__name__}"))
            except Exception:  # noqa: BLE001 - nothing more we can do
                pass
        finally:
            self.limiter.finish(requester)

    # ------------------------------------------------------------------ internals
    def _run_and_reply(
        self, client: Any, channel: str, user: str | None, question: str, thread_ts: str | None
    ) -> None:
        working = client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, **blocks.working_message(question, user)
        )
        ts = working["ts"]
        try:
            result = self.runner(question, self.settings, user)
        except Exception as exc:  # noqa: BLE001 - the graph should not raise, but never leave "Looking into it"
            client.chat_update(
                channel=channel,
                ts=ts,
                **blocks.error_message(question, f"{type(exc).__name__}: {exc}"),
            )
            return
        client.chat_update(
            channel=channel, ts=ts, **blocks.result_message(result, question, user, self.settings)
        )
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts or ts, **blocks.evidence_message(result)
        )

    def _switch_mock_app(self, question: str) -> blocks.SlackMessage:
        """Demo control: ``:app down`` flips the simulated Mule app health (mock only)."""
        if not self.settings.is_mock:
            return blocks.refusal_message(":app only works in mock mode.")
        parts = question.split()
        if len(parts) != 2 or parts[1] not in MOCK_APP_STATES:
            return blocks.refusal_message(
                f"Usage: `:app <state>`. States: {', '.join(MOCK_APP_STATES)}. "
                f"Current: {self.settings.mock_app_state}."
            )
        # Swap the whole settings object: atomic, and the poller reads it every tick.
        self.settings = self.settings.model_copy(update={"mock_app_state": parts[1]})
        return blocks.refusal_message(
            f"Mock app health is now `{parts[1]}`. "
            "Questions and the health poller use it from now on."
        )

    # ------------------------------------------------------------------ health alerts
    def post_health_event(self, client: Any, event: HealthEvent) -> None:
        channel = self.settings.slack_alert_channel_id
        if not channel:
            return
        name = self.settings.partner_name
        if event.kind == "alert" and event.status is not None:
            msg = blocks.health_alert_message(event.status, name)
        elif event.kind == "recovered" and event.status is not None:
            msg = blocks.health_recovered_message(event.status, name, event.down_for_seconds)
        else:
            msg = blocks.health_check_failed_message(
                event.detail or "unknown error", event.consecutive_failures, name
            )
        client.chat_postMessage(channel=channel, **msg)

    def start_poller(self, client: Any) -> threading.Event | None:
        """Start the health poller thread if an alert channel is configured."""
        if not self.settings.slack_alert_channel_id:
            return None
        stop = threading.Event()
        thread = threading.Thread(
            target=run_poller,
            kwargs={
                "get_settings": lambda: self.settings,
                "get_tools": get_tools,
                "on_event": lambda ev: self.post_health_event(client, ev),
                "stop": stop,
                "watch": HealthWatch(),
            },
            name="health-poller",
            daemon=True,
        )
        thread.start()
        return stop


# ---------------------------------------------------------------------------
# Bolt wiring (the only code that needs real Slack credentials)
# ---------------------------------------------------------------------------
def build_app(bot: SlackTriageBot, **app_kwargs: Any) -> Any:
    """Register the three listeners.

    ``app_kwargs`` is for tests: passing ``authorize=...`` replaces Bolt's token-based
    authorization (which calls slack.com) with an offline function.
    """
    from slack_bolt import App  # noqa: PLC0415

    if "authorize" not in app_kwargs:
        assert bot.settings.slack_bot_token is not None
        app_kwargs["token"] = bot.settings.slack_bot_token.get_secret_value()
    app = App(**app_kwargs)

    @app.command("/triage")
    def on_slash(ack: Any, command: dict, client: Any, respond: Any) -> None:
        ack()  # within Slack's 3 seconds; the work happens after
        bot.handle(
            client=client,
            channel=command["channel_id"],
            user=command.get("user_id"),
            text=command.get("text", ""),
            source="slash",
            reply_private=lambda m: respond(response_type="ephemeral", **m),
        )

    @app.event("app_mention")
    def on_mention(event: dict, client: Any) -> None:
        channel, user = event["channel"], event.get("user")
        bot.handle(
            client=client,
            channel=channel,
            user=user,
            text=event.get("text", ""),
            source="mention",
            thread_ts=event.get("thread_ts") or event["ts"],
            reply_private=lambda m: client.chat_postEphemeral(channel=channel, user=user, **m),
        )

    @app.event("message")
    def on_message(event: dict, client: Any) -> None:
        # Direct messages only; ignore edits, joins, and every bot's messages (incl. ours).
        if event.get("channel_type") != "im" or event.get("subtype") or event.get("bot_id"):
            return
        channel = event["channel"]
        bot.handle(
            client=client,
            channel=channel,
            user=event.get("user"),
            text=event.get("text", ""),
            source="dm",
            reply_private=lambda m: client.chat_postMessage(channel=channel, **m),
        )

    return app


def run_bot(settings: Settings) -> int:
    """Start the Socket Mode bot (and the health poller if configured). Blocks."""
    from slack_bolt.adapter.socket_mode import SocketModeHandler  # noqa: PLC0415

    from triage.runbook.indexer import ensure_index  # noqa: PLC0415

    missing = [
        name
        for name, value in (
            ("SLACK_BOT_TOKEN", settings.slack_bot_token),
            ("SLACK_APP_TOKEN", settings.slack_app_token),
        )
        if not value
    ]
    if missing:
        print(f"Missing {', '.join(missing)} in .env. See docs/slack-setup.md.")
        return 2
    if not settings.slack_allowed_channels and not settings.slack_allowed_users:
        print(
            "Warning: SLACK_ALLOWED_CHANNEL_IDS and SLACK_ALLOWED_USER_IDS are both empty, "
            "so the bot will refuse everyone (fail-closed)."
        )
    built = ensure_index(settings)
    if built is not None:
        print(f"(built the runbook index from {built} entries)")

    bot = SlackTriageBot(settings)
    app = build_app(bot)
    stop = bot.start_poller(app.client)
    mode = "mock" if settings.is_mock else "live"
    poller = (
        f"health alerts to {settings.slack_alert_channel_id} every {settings.health_poll_seconds}s"
        if stop
        else "no health alerts (SLACK_ALERT_CHANNEL_ID unset)"
    )
    print(f"Triage bot running in {mode} mode, Socket Mode; {poller}. Ctrl+C to stop.")
    assert settings.slack_app_token is not None
    try:
        SocketModeHandler(app, settings.slack_app_token.get_secret_value()).start()
    except KeyboardInterrupt:
        pass
    finally:
        if stop:
            stop.set()
    return 0
