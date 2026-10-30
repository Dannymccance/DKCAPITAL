# GWS provider-specific Discord channels

The existing DK Capital #gws-signals channel currently receives parsed XAUUSD cards from several Telegram sources. New GWS signals will be routed to separate Discord channels:

| Telegram source | Discord channel |
| --- | --- |
| GWS - STORM | #gws-storm-signals |
| GWS - CAPITAL | #gws-capital-signals |
| GWS - MONEY HOUSE | #gws-money-house-signals |
| GWS (VIP) | #gws-vip-signals |

Money House and VIP remain presentation-only. The live MT5 execution allowlist remains STORM and CAPITAL.

## Routing

- STORM and CAPITAL are identified by the previously pinned Telegram peer IDs. MONEY HOUSE and VIP require strict NFKC-normalized source titles and unforwarded matching origin/chat IDs.
- Unattributed GWS signals do not publish to an incorrect channel.
- Previously posted Discord cards stay in their existing channels. Their edits and deletion tombstones continue using recorded message IDs; future cards route to source-specific channels.
- Without a mapping file, legacy #gws-signals routing remains active. Invalid files block Discord publishing until corrected.
- No change to Telegram listener, signal parsing, the signal processor, account policies, EA, gateway, risk, broker orders or other Discord channels.

## GitHub-first deployment on existing VPS

After merging a passing PR, run in the existing authorized SSH shell. Do not copy any secrets into chats or GitHub.

    cd /opt/dkcapital
    git pull --ff-only origin main
    docker compose build discord-bot

Run the setup command as a short-lived container sharing the existing DK Capital data volume, without launching another connected bot:

    docker compose --profile discord run --rm --no-deps discord-bot python -m dkcapital.gws_discord_routing setup

It checks that the existing DISCORD_INTERNAL_SIGNALS_CHANNEL_ID points to #gws-signals and creates/reuses four provider channels beside it. This requires the existing bot to have Manage Channels if creation is needed. It prints the real channel IDs (never the token) and writes /app/data/gws-provider-channels.json on the existing dkcapital-data volume. It does not post signals.

After validating the four printed channels, restart ONLY the Discord presentation service:

    docker compose --profile discord up -d --no-deps discord-bot
    docker compose --profile discord logs --tail=80 discord-bot

Verify that new XAUUSD cards from each group arrive in the respective provider channel. Old #gws-signals messages should remain in place. The bot restart briefly interrupts Discord notifications only, not live MT5 operation.

## Rollback

Disable the route file through the same short-lived container:

    docker compose --profile discord run --rm --no-deps discord-bot python -c 'from dkcapital.gws_discord_routing import routing_path; p=routing_path(); p.rename(p.with_suffix(".disabled.json"))'
    docker compose --profile discord up -d --no-deps discord-bot

New cards return to legacy #gws-signals; old message IDs and all provider channels remain. Do not delete the DK Capital state, cards or channels as part of rollback.
