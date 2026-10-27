# Kyle REAL Account2 — independent Discord fault / recovery alerts

This implements the same read-only alert predicates and cadence as Dan's
Account3, with Account2's own independent channel, snapshot and delivery state.

- Kyle Account2: Discord channel ID `1564421762752450592`
- Dan Account3: existing channel ID `1564401829796778124` (unchanged)
- Demo Account1: existing dashboard / alert channel (unchanged)
- State: `/app/data/mt5-account2-alert-state.json` in shared Docker data volume
- Snapshot: `/app/data/mt5-account2.json`
- Host state: `/app/data/mt5-profile-host-status.json` (must include Account2)
- Cadence: check once per minute; deduplicate ongoing faults; send recovery
  notice only after a previously delivered fault clears.

Alert checks: missing/stale telemetry (>150s), wrong login/mode/currency/server,
feed HTTP != 200, unexpected entry flag (when configured), missing broker
positions/orders inventory, unverified broker-native SLs, nominal signal
risk cap exceeding 4% of CASH balance, systemd terminal down, and newly
observed service restart. No credit is counted as risk cash.

On first successful channel send only, Kyle gets a one-time
`DK CAPITAL | KYLE ACCOUNT 2 REAL ALERT MONITOR` message showing the current
broker balance/equity, signal-risk cap, broker positions/orders and feed state,
plus the warning that live frozen-terminal auto-recovery is not commissioned.
Future checks post only new fault and recovered notifications. There are
no repeated periodic status messages. Only confirmed Discord deliveries advance
alert state.

## Deploy from reviewed GitHub main

    cd /opt/dkcapital
    git fetch origin main
    git merge --ff-only origin/main

Confirm the existing VPS .env still has Kyle login 24038172, REAL GBP,
server `VantageMarkets-Live 14`. The new code defaults to the channel above,
but explicitly configure `MT5_ACCOUNT2_ALERT_CHANNEL_ID` so deployments do
not silently follow an application default if it changes. Set the optional
`MT5_ACCOUNT2_EXPECT_ENTRIES=true` only when the operator has deliberately
armed Kyle's EA. Neither setting modifies the EA.

Use the operator's normal secret-preserving editing method; do not print
private .env contents or share the token. New example fields:

    MT5_ACCOUNT2_ALERT_CHANNEL_ID=1564421762752450592
    MT5_ACCOUNT2_EXPECT_ENTRIES=true

Confirm the DK Capital Discord bot has **View Channel**, **Send Messages** and
**Read Message History** access in Kyle's channel. It must be in the correct
guild. No webhooks or bot-token rotation are required.

Rebuild **only** the Discord bot container to load the new code and env:

    docker compose --profile discord up -d --no-deps --build --force-recreate discord-bot

This restarts ONLY the bot, not MT5, the signal processor, or the gateway.
There may be a short gap in Discord notifications during the bot restart.
On startup the Account2 alert loop sends its one-time monitoring status and
persists a separate delivery record; Dan's and demo state files are untouched.

Verify:

    docker compose --profile discord ps discord-bot
    docker compose --profile discord logs --tail=60 discord-bot

A missing message in Kyle's channel usually means the bot cannot see or send
to the new channel. Correct channel permissions, then the next retry should
post the initial status (it will not be marked delivered on an exception).

## Important recovery distinction

Systemd crash and boot recovery are commissioned for all three accounts, but
REAL Account2 and Account3 freeze-restart remain intentionally disabled.
The EA telemetry currently reports restart_reconciliation_supported=false.
Alerts do not restart terminals, stop trading, or claim broker fills/TP/SL
execution are proven. Never interpret a recovered telemetry alert as proof
that all positions/deals reconciled after a restart.
