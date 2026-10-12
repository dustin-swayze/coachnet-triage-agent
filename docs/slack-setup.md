# Slack setup (personal workspace, mock mode)

This gets the triage bot running in a free personal Slack workspace on your home PC.
Everything uses synthetic data. No TrxNow systems or data are involved.

Shell for all commands: **Git Bash**, from the repo root `C:\Projects\coachnet-triage-agent`.

## What you will end up with

- A Slack app called "Triage Agent (mock)" in your own workspace.
- A bot process on your PC that connects out to Slack (Socket Mode, no public URL).
- A private channel `#triage-demo` where you can ask the agent about cases.

## 1. Create a free workspace

1. Go to <https://slack.com/get-started> and choose "Create a workspace".
2. Sign up with your personal email. Name it anything, for example `dustin-sandbox`.
3. Skip the invite-teammates step.

Use a personal workspace, not TrxNow's. See "Moving to TrxNow's Slack" below.

## 2. Create the app from the manifest

1. Open <https://api.slack.com/apps> and click **Create New App**.
2. Choose **From a manifest**.
3. Pick your new workspace and click **Next**.
4. Switch the editor to **YAML**. Delete what is there.
5. Paste the whole contents of `slack/manifest.yaml` from this repo. Click **Next**, then **Create**.

The manifest sets the `/triage` command, the four minimum bot scopes, the two event
subscriptions, and turns on Socket Mode.

## 3. Install the app and copy the bot token

1. In the app settings, open **OAuth & Permissions**.
2. Click **Install to Workspace**, then **Allow**.
3. Copy the **Bot User OAuth Token**. It starts with `xoxb-`.

## 4. Create the app-level token

1. Open **Basic Information** and scroll to **App-Level Tokens**.
2. Click **Generate Token and Scopes**. Name it `socket`.
3. Add the scope `connections:write`. Click **Generate**.
4. Copy the token. It starts with `xapp-`.

You now have two tokens. They are different. `xoxb-` is the bot token. `xapp-` is the app token.

## 5. Create a private channel and invite the bot

1. In Slack, click **+** next to Channels, then **Create a new channel**.
2. Name it `triage-demo`. Make it **Private**.
3. In the channel, type `/invite @triage` and press Enter.

## 6. Find the ids you need

**Channel id**

1. Click the channel name at the top of the channel.
2. Open **About** (also called "View channel details").
3. The channel id is at the bottom. It starts with `C` or `G`.

**Your member id**

1. Click your profile picture, then **Profile**.
2. Click the three dots (**⋮**), then **Copy member ID**. It starts with `U` or `W`.

## 7. Fill in `.env`

If you have no `.env` yet:

```bash
cp .env.example .env
```

Open `.env` and set these lines. Leave everything else as it is.

```
SLACK_BOT_TOKEN=xoxb-...your bot token...
SLACK_APP_TOKEN=xapp-...your app token...
SLACK_ALLOWED_CHANNEL_IDS=C0123ABCD
SLACK_ALLOWED_USER_IDS=U0123ABCD
SLACK_ALERT_CHANNEL_ID=C0123ABCD
HEALTH_POLL_SECONDS=30
```

- Use your own channel id and member id from step 6.
- `SLACK_ALERT_CHANNEL_ID` is where health alerts are posted. Using the same channel is fine.
- `HEALTH_POLL_SECONDS=30` makes the demo quick. The default is 300 (5 minutes).
- Optional: `SLACK_MAX_RUNS_PER_USER_PER_HOUR` caps triages per person (default 20).
- Keep `TRIAGE_MODE=mock`.

## 8. Run the bot

```bash
uv run python -m triage slack
```

Leave this window open. The bot is online only while it runs. Stop it with Ctrl+C.

## 9. Try it

In `#triage-demo`:

1. `/triage help`. You should see what the bot does and how to ask.
2. `/triage case 00412345 looks like a duplicate`. First a "Looking into it" message, then the answer replaces it. The evidence is in the thread under the answer.
3. An @mention: `@triage case 00412345 looks like a duplicate`.
4. A direct message: open the bot under **Apps** in the sidebar and send `case 00412345`.

### The outage demo

1. Run `/triage :app down`. This switches the simulated Mule app to "stopped". It only works in mock mode.
2. Wait up to 30 seconds. The poller posts a red alert in the alert channel.
3. Ask `/triage case 00412345`. The answer is now the "app is not healthy" alert, because the agent checks health first.
4. Run `/triage :app healthy`.
5. Wait for the poll. A green "recovered" message appears, with how long the app was unhealthy.

Other simulated states: `deploying`, `restart_loop`, `status_error`.

## Security notes

- **Tokens live only in `.env`.** That file is gitignored. Never commit it. Never paste a token into chat, a ticket, or a screenshot. If one leaks, revoke it in the app settings and install again.
- **Access is fail-closed.** The bot answers only in allowlisted channels, or in a direct message with an allowlisted user. An allowlisted user who asks in some other channel is told to DM the bot, so answers never land in an unapproved channel. If both lists are empty, the bot refuses everyone.
- **Only allowlisted users can use `:app`.** People in the channel can ask questions but cannot change the demo app health.
- **Answers contain case details.** Keep the channel private. Even synthetic data here is practice for real data later.
- **Slack text is untrusted input.** What people type is passed to the agent as data, never as instructions. The agent's own guards still apply (case numbers must appear in the message, runbook ids must come from the search).
- **Output is escaped.** Anything dynamic is escaped before it goes to Slack. A message or log line containing `@channel`, `<!here>`, or a fake link shows up as plain text and cannot ping anyone.
- **The bot is read-only.** It has no write tools, and its Slack scopes cannot read channel history. The only things it can do are read and reply.

## Moving to TrxNow's Slack

Do not reuse anything from this personal setup. Before any TrxNow workspace use:

1. A workspace admin must review and approve the app.
2. Get approval to show case data in Slack, and agree how long Slack keeps it.
3. Create a separate app with separate tokens. Never reuse the personal ones.
4. Run the bot on an always-on host, not a PC. That is Phase 6 (Azure).
5. Rename the app (drop "(mock)" from the manifest) and keep the allowlists tight.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `not_in_channel` in the bot log | The bot is not in that channel. Run `/invite @triage` there. |
| Bot is silent in a channel | In channels it only answers @mentions and `/triage`. Check that the channel id is in `SLACK_ALLOWED_CHANNEL_IDS`, with no spaces or typos. Your user id alone only allows DMs. |
| `dispatch_failed` after `/triage` | The bot process is not running. Start it with `uv run python -m triage slack`. |
| `invalid_auth` on start | Wrong token type or a stale token. `SLACK_BOT_TOKEN` must start with `xoxb-`. `SLACK_APP_TOKEN` must start with `xapp-`. |
| Slash command says "not a valid command" | The app was not installed after the manifest changed. Reinstall under **OAuth & Permissions**. |
| DMs do nothing | Check that **Messages Tab** is on under **App Home**, and your user id is in `SLACK_ALLOWED_USER_IDS`. |
