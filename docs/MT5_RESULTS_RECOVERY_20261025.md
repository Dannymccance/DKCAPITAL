# MT5 Account 1 results repair and broker-confirmed backfill

Scope: **GBP DEMO account 5057220041**, Discord #results, broker-reconciled trades only.
This change does **not** modify MQL5 execution, order placement, EA toggles,
risk limits, or Kyle's MT5 terminal.

## Change

- Final results show both **PROVIDER** (GWS / Elite) and **SOURCE**, the
  recorded originating Telegram channel title (e.g. GWS - CAPITAL).
  When an old source title is unavailable, display its numeric Telegram
  chat identity if known. Do not infer a specific channel.
- Signal processing carries the original Telegram `chat_title` as
  passive `source_chat_title` metadata and does not change parsing/execution.
- A one-off preview-first utility evaluates broker-confirmed closes
  from **22 October 2026 onward**, excluding already-published result IDs.
- Backfill preserves broker IN/OUT reconciliation, all legs and commissions,
  broker stop/close reasons, and the actual account balance.
- Publishing uses a durable Discord message marker and existing
  `mt5-results-published.json` ledger. It scans the results channel for
  duplicates, fails closed on ambiguous legacy duplicates or inaccessible
  channel history, and backs up the original ledger.
- Historical coverage is limited by the MT5 EA's current telemetry deal
  inventory (the EA reports up to 1000 history deals). Missing IN/OUT deals
  must never be fabricated. If a trade is absent from the preview,
  inspect broker-history availability before attempting a new export.

## VPS: deploy non-trading services

After merge and green GitHub CI:

```bash
cd /opt/dkcapital
git pull --ff-only origin main
docker compose --profile discord build signal-processor discord-bot
docker compose --profile discord up -d --no-deps --no-build signal-processor discord-bot
```

This changes only the Python signal processor and Discord bot.
The MT5 gateway and both MT5 trading terminals remain untouched.
Confirm both containers are healthy and that the source titles appear
in `/app/data/signal-state.json`.

## Preview, no posts

```bash
docker compose --profile discord run --rm --no-deps -T discord-bot \
  python ops/backfill/mt5_results_since_oct22.py --since 2026-10-22
```

The preview enumerates eligible broker groups, exact GBP profit/loss,
source, date, existing publication status and how many appear unposted.
**A zero result is not evidence of no trades:** a too-short broker deal
window or incomplete/recently removed source signal can also explain it.
Verify the ledger and broker history first.

## Controlled publication, only after reviewing preview

The normal bot also writes the results ledger, so **stop only discord-bot**
during the one-off publisher. Leave MT5 terminals, gateway, listener and
signal processor running. A failed backfill must not leave the Discord
bot stopped. Use this restart-on-exit sequence:

```bash
cd /opt/dkcapital
docker compose --profile discord stop discord-bot
docker compose --profile discord run --rm --no-deps -T discord-bot \
  python ops/backfill/mt5_results_since_oct22.py --since 2026-10-22 --publish
rc=$?
docker compose --profile discord up -d --no-deps --no-build discord-bot
test "$rc" -eq 0
```

For each eligible missing group, the script verifies an existing message
marker or scans for a possible older result card before posting. Successful
posts record real Discord message IDs into the existing publication ledger.
Re-running is idempotent when channel history and ledger are intact. Do not
force publication through any ambiguity stop.

## Verification

Inspect the new #results embeds for SOURCE, correct original provider,
fully broker-confirmed 1–6 TP breakdown, correct realised GBP and unique
broker closes. Inspect `docker compose --profile discord logs --tail=150
discord-bot` for bot connection and any errors. Do not claim historical
backfill complete unless Discord acknowledges the individual message IDs.

The Telegram mirror's earlier heartbeat-blocking trace is a separate
potential liveness issue; it does not by itself prove that completed
broker results were generated but lost.
