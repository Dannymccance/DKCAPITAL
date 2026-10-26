# Dan's MT5 Account 3 REAL — dedicated Discord health alerts

Dedicated Discord destination: `1564401829796778124`.

This feature mirrors the demo alert/recovery pattern with separate persistent state,
separate broker identity and no shared Account1 routing. It reads the *authenticated*
Account 3 gateway telemetry at `/app/data/mt5-account3.json`, checks it at one-minute
intervals in the DK Capital Discord bot, and posts only fault/recovery transitions.

## What gets reported

- Missing or stale Account3 telemetry (>150 seconds)
- Authenticated feed HTTP other than 200 (`MT5_ACCOUNT3_INSTRUCTION_FEED_UNAVAILABLE`)
- Wrong broker login, REAL/USD mode, or exact broker server
- Unknown broker inventory or unverified native protective stops
- Risk cap exceeding a cash-balance-based percentage
- Optional expected entry switch mismatch, if `MT5_ACCOUNT3_EXPECT_ENTRIES` is set
- Terminal down or recently restarted **only when** an existing fresh host audit includes Account 3

The alert posts do not restart MT5, block trade entries, close positions, or prove
broker orders reconciled. Recovery notices mean an alert predicate cleared, not
that a terminal was automatically restarted.

## Automatic restart: scope and limitations

Account 3's **existing** systemd unit has `Restart=always`,
`RestartSec=120`, and `RestartPreventExitStatus=78`. It can restart
after crashes/non-approved exits, but an unapproved executable/LiveUpdate
fails closed. This is not automatic recovery from a hung but still-active MT5.

The repository also has a separate recovery supervisor for *frozen* MT5
terminals. It is opt-in per monitored profile and refuses REAL account
auto-restart without verified `restart_reconciliation_supported=true` and
independently verified broker stops/orders. Dan's live profile is **not**
automatically enrolled by these alerting changes. Do not enable the recovery
supervisor `--apply` on Account3 until broker reconciliation capability and
open-position safety have been separately tested.

The current demo alert in the screenshot is sent by the Discord bot. The new
Account3 alert loop also depends on that bot being operational. A whole-VPS
or Discord outage cannot reliably be reported by an on-VPS Discord bot;
use an external uptime monitor for that scenario.

## GitHub-first deployment

After approved PR is merged and CI passes:

```bash
cd /opt/dkcapital
git fetch origin main
git merge --ff-only origin/main
python3 -m unittest discover -s tests -p 'test_mt5_account3_live_alerts.py' -v
docker compose --profile discord config --quiet
```

On the VPS private `.env`, the existing Account3 broker identity must be
configured: `MT5_ACCOUNT3_LOGIN=36193223`,
`MT5_ACCOUNT3_MODE=REAL`, `MT5_ACCOUNT3_CURRENCY=USD`,
`MT5_ACCOUNT3_SERVER=VantageMarkets-Live 4`. Do not commit this
live configuration or the Account3 feed token.

The channel ID defaults to `1564401829796778124` in the bot, or can be
explicitly configured with `MT5_ACCOUNT3_ALERT_CHANNEL_ID`. If you want an
alert when Dan's EA is unexpectedly disarmed, explicitly set
`MT5_ACCOUNT3_EXPECT_ENTRIES=true` in the private .env. This merely checks
the EA state; it does not enable entries.

Build and recreate **only the existing Discord bot**, once it is safe to
briefly interrupt its messaging and after confirming that the bot has permission
to send messages in the new channel:

```bash
docker compose --profile discord up -d --no-deps --build discord-bot
docker compose --profile discord ps discord-bot
docker compose --profile discord logs --tail=35 discord-bot
```

Do not restart MT5 terminals, gateway, Telegram listener or signal processor.
The bot's 60-second monitor will post a fault if Account3 is unhealthy, and a
separate recovery when it clears. A healthy account generates no startup spam.

To see which host telemetry optional status is available, inspect the
`mt5-profile-host-status.json` file from the shared Docker data volume.
Without a commissioned Account3 host audit, the bot still monitors authenticated
broker heartbeat and feed status, but doesn't claim to know whether systemd
actually restarted Dan.

## Validation

Verify an actual healthy Account3 snapshot is being updated:
`GET /v5/accounts/3/snapshot` must be authenticated and report
login 36193223, REAL/USD, exact Vantage server, `feed_http=200` and a
recent `received_at`. The health checker emits nothing when healthy.
For negative cases, run the offline unit tests; do **not** intentionally
interrupt the live gateway/EA to manufacture a Discord alert. Channel
permissions can be checked using a read-only Discord bot channel lookup
without posting a fabricated recovery message.
