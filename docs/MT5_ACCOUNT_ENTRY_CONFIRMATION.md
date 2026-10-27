# MT5 entry confirmation channel: Demo, Kyle and Dan

**Discord channel:** `1564370691313377300` (#account-entry-confirmation).

This Discord bot workflow creates ONE signal reference message for each XAUUSD
signal parsed after the bot's first deployment. It edits that message as
broker-confirmed entry evidence arrives for each independent MT5 account:

- **Kyle** — Account2, REAL GBP, login 24038172, VantageMarkets-Live 14
- **Dan** — Account3, REAL USD, login 36193223, VantageMarkets-Live 4
- **Demo** — Account1, DEMO GBP, login 5057220041

Sample:

    SELL XAUUSD | GWS
    Signal reference: -1004334637102:8742
    Entry zone: 4000–4004

    Kyle: ✅ Entered (broker-confirmed)
    Dan: ✅ Entered (broker-confirmed)
    Demo: ❌ No broker entry confirmed

A green check is issued ONLY when a current authenticated, correctly identified
MT5 gateway snapshot shows a native open position or an actual
`DEAL_ENTRY_IN` matching the signal's unique MT5 magic number. The signal's
Telegram status, ETA, EA entry decision, feed HTTP 200 or another account's
fill NEVER creates a check. Each confirmed account is stored persistently,
so its check stays green after the broker position closes or old deal records
roll off. Account IDs have distinct state keys.

Other states are `⏳ Awaiting broker fill` (signal younger than three minutes),
`❌ No broker entry confirmed` (no native fill in available evidence),
and `⚠️ Unknown` (missing or stale telemetry, account identity mismatch,
missing exposure inventory or unavailable feed). A cross does NOT prove the
broker rejected the order; history can be bounded and not all orders fill
within three minutes.

Each card is keyed by the Telegram `signal_id` (channel ID plus message ID).
An initial "SELL NOW" post that becomes a complete signal on edit is eligible
when the first complete signal was parsed, without changing the EA's original
broker entry freshness timestamp. The bot creates no historical backlog when
newly deployed. It tracks new signal confirmations for up to seven days,
limits updates to four messages per minute, edits an existing message instead
of posting duplicates, and honours deliberate deletion of cards. State is
persisted at `/app/data/mt5-entry-confirmation-state.json` inside the shared
Docker volume.

## Deployment

Merge reviewed CI-passing GitHub PR into `main`, then on the VPS:

    cd /opt/dkcapital
    git fetch origin main
    git merge --ff-only origin/main

Set only this optional routing field in the existing private `.env`
(the code also defaults to this ID):

    MT5_ENTRY_CONFIRMATION_CHANNEL_ID=1564370691313377300

Confirm the DK Capital bot has View Channel, Send Messages and Read Message
History permissions in that Discord channel. Recreate ONLY the bot:

    docker compose --profile discord up -d --no-deps --build --force-recreate discord-bot
    docker compose --profile discord ps discord-bot
    docker compose --profile discord logs --tail=60 discord-bot

The bot announces that future entry confirmations are being tracked, then
creates one card per new signal and edits statuses when fresh broker telemetry
proves a fill. If initial announcement is not delivered, it retries; the
cutover time is durable across restarts so no historical flood occurs.

**No change to broker orders, EA parameters, SL management, risk controls,
gateway, signal processor, telegram listener, watchdog or systemd.** This
is a Discord-only, read-only monitoring workflow. Rebuilding the bot briefly
interrupts Discord notifications, and does not reboot MT5.
