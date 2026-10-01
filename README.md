# DK Capital

VPS-ready Python services for DK Capital.

Current services:

- Telegram listener using a Telegram user session
- Deterministic signal parser and live state processor
- Discord bot with an auto-updating live trade dashboard
- Dedicated XAUUSD paper-trading engine with a durable $100,000 virtual account
- Docker Compose deployment
- Structured logging and durable local JSON/JSONL state

The Telegram listener remains separate from parsing. Raw source messages are always retained first, then the signal processor derives current trade state from them. This makes edits, deletes and parser corrections recoverable.

## What the parser understands

The parser is designed for messy provider chat rather than a rigid signal format. It handles:

- New signals such as `BUY XAUUSD AT 4139 - 4136`
- TP and SL values supplied in the signal
- Replies to the original signal or to later update messages
- `TP1 HIT`, `TP1&TP2 +125 PIPS`
- `SL BREAKEVEN`, `SL TO BE`, `SL TO 4147`
- Global instructions such as `ALL GOLD SL TO 4272`
- Multi-position instructions such as `BOTH SL ONE MORE TIME TO 4142`
- `ADDS` / layering
- `BACK AT ENTRY WE ENTER AGAIN` / re-entry
- Explicit close and cancellation commands
- Telegram message edits and deletions

Ordinary commentary is ignored. Recognized updates that cannot be safely attached to a trade are retained in `unresolved_actions` rather than silently discarded.

## Quick start

1. Copy `.env.example` to `.env`.
2. Add your Telegram API ID and API hash from https://my.telegram.org.
3. Generate a Telegram StringSession using `scripts/generate_telegram_session.py`.
4. Put the generated session string in `.env`.
5. Set `TELEGRAM_SOURCE_CHATS` to the channel/group IDs or usernames you want to listen to.
6. Add the Discord bot token and server ID.
7. Start the services.

```bash
docker compose --profile discord up -d --build
docker compose ps
```

## Discord dashboard

Once the bot is online, run this slash command in the channel where you want the live dashboard:

```text
/dashboard_create
```

The bot stores that dashboard message ID in the shared Docker volume and refreshes it automatically as Telegram updates are parsed.

You can force a refresh with:

```text
/dashboard_refresh
```

The dashboard currently shows active trades, entry range, TP state, current SL or breakeven state, layers, re-entries and recent completed/closed trades.

## Data flow

```text
Telegram
  -> telegram-listener
  -> /app/data/telegram-events.jsonl
       -> signal-processor -> /app/data/signal-state.json -> live Discord dashboard
       -> 15-minute delayed paper clock

Standard Bullion XAU/USD
  -> live 15-second capture
  -> /app/data/xauusd-market-tape.jsonl
  -> 15-minute delayed paper clock
  -> /app/data/paper-trading.json
```

The state processor rebuilds from the latest version of every Telegram message, so edits are reflected deterministically. Replies resolve back through the message chain. Explicit `ALL GOLD` and `BOTH` instructions can target multiple open trades.

## VPS deployment

After cloning the repository:

```bash
cd DKCAPITAL
cp .env.example .env
nano .env
docker compose --profile discord up -d --build
docker compose ps
docker compose logs -f telegram-listener signal-processor discord-bot
```

If you need the numeric ID for a private Telegram channel or group:

```bash
docker compose run --rm telegram-dialogs
```


## Bybit demo trading

DK Capital includes an isolated Bybit Demo Trading service for XAUUSD provider
signals. Provider signals remain `XAUUSD`; execution is mapped to Bybit's
API-tradable `XAUUSDT` TradFi perpetual.

The service uses only the mainnet Demo Trading API:

```text
https://api-demo.bybit.com
```

Create the API key from Bybit while switched into **Demo Trading**. Never put a
live-account API key in the demo variables.

Add the demo credentials to `.env`:

```text
BYBIT_DEMO_API_KEY=...
BYBIT_DEMO_API_SECRET=...
BYBIT_DEMO_EXECUTION_ENABLED=false
```

Start with execution disabled:

```bash
docker compose --profile bybit-demo up -d --build bybit-demo
docker compose logs --tail=100 bybit-demo
```

A successful connection writes `/app/data/bybit-demo.json` and logs the demo
wallet equity, XAUUSDT price, tick size and quantity step. Once the connection
has been verified, set:

```text
BYBIT_DEMO_EXECUTION_ENABLED=true
```

and rebuild/restart the service. Only signals observed after activation are
eligible for execution and stale signals are rejected. The demo engine sizes
each new trade from current demo equity and the protective stop, uses hedge
mode so one BUY and one SELL can coexist, places a server-side stop, mirrors
provider partial closes and stop changes, and applies the existing 30/30/40
TP management for three-target signals.

Version 1 intentionally allows only one tracked signal per direction at a time.
A second same-direction signal is skipped rather than allowing Bybit to merge
allocations in a way that could make per-signal risk management ambiguous.

Durable state:

```text
/app/data/bybit-demo.json
```


## Tests

```bash
python -m unittest discover -s tests -v
```

## Security

Never commit `.env`, Telegram session strings, Discord tokens, API hashes, or session files. A Telegram StringSession and a Discord bot token must both be treated like passwords.


## XAUUSD paper engine

The paper engine is a separate delayed simulation service. Telegram ingestion and
the normal parsed signal dashboard remain live, but the paper engine consumes the
raw Telegram event stream on a 15-minute delayed clock. Standard Bullion XAU/USD
quotes are also captured live every 15 seconds into a durable local market tape
and released to the strategy only after the same 15-minute delay.

This means that at 19:45 real time the paper engine is processing approximately
19:30 provider events and 19:30 XAU/USD quotes in their original chronological
order. New signals, edits, partial closes, stop changes and market moves are all
delayed together rather than delaying only price.

The default account is:

```text
Starting balance: $100,000
Symbol: XAUUSD
Strategy mode: observe_only
```

`observe_only` is intentional. Until an execution strategy is configured, the
engine records every signal but cannot create a paper fill. This prevents entry,
sizing, stop, leverage, or exit assumptions from being silently invented.

Durable paper files:

```text
/app/data/paper-trading.json
/app/data/paper-trade-events.jsonl
/app/data/xauusd-market-tape.jsonl
/app/data/paper-delay-state.json
```

The default simulation delay is 900 seconds:

```text
PAPER_SIMULATION_DELAY_SECONDS=900
```

On first activation the existing paper ledger is preserved as the transition
baseline. New data is buffered for 15 minutes before the delayed clock begins
advancing. Restarting the container does not lose the buffer or replay already
processed delayed events because both stream cursors are persisted.

For a deliberate clean restart of the paper account, stop the paper engine first
and run the reset tool:

```bash
docker compose stop paper-engine
docker compose --profile tools run --rm paper-reset
docker compose up -d paper-engine
```

The reset creates backups of the existing paper account, paper event log and delay
cursor, resets the virtual account to the configured starting balance, and moves
the delayed Telegram and market cursors to the current end of each live stream.
Existing Telegram history and the Standard Bullion market tape are preserved, but
only newly captured data after the reset can enter the new paper ledger.

The accounting core supports paper fills, partial closes, realised USD PnL,
unrealised USD PnL, balance and equity. XAUUSD position quantity is stored in
troy ounces, so PnL is independent of any future broker-specific lot convention.

The strategy layer can be added without changing the Telegram parser or paper
accounting ledger.


## Permanent paper-trading dashboard

The Discord bot maintains one permanent dashboard message in channel
`1554479167024926820` by default. The dashboard is recreated automatically if
its message is deleted and the bot attempts to pin it when first created.

It shows the $100,000 paper account balance and paper equity, realised and open
PnL, return, peak equity, current and maximum drawdown, the delayed XAU/USD mark,
the simulation clock and delay status, trade statistics, signal intake, all open
paper positions with USD and pip PnL, and recent closed trades. During the first
15 minutes after initial activation the dashboard reports BUFFERING; afterward it
reports ACTIVE.

XAUUSD pip reporting defaults to a 0.01 price increment, so a $1.00 gold move is
reported as 100 pips. This convention is configurable with
`PAPER_XAU_PIP_SIZE`.

The entry-selection policy is `all_parsed`: every parsed XAUUSD signal from
the configured providers is queued for paper execution. Position sizing and
execution rules are intentionally kept separate so they can be defined without
changing signal selection.


## Paper history recovery

Live DK Capital paper trading now uses Standard Bullion XAU/USD bid/ask only.

The previous historical paper replay used Bybit XAUUSDT minute candles as a proxy.
That replay is intentionally disabled because XAUUSDT is a different instrument.
Standard Bullion's free historical API provides XAU/USD close observations at
15-minute intraday resolution, which is not sufficient to determine the true
order of intrabar TP and SL touches.

Telegram history can still be recovered safely without fabricating paper results:

```bash
docker compose --profile tools run --rm telegram-backfill
```

A historical paper ledger should only be rebuilt once a sufficiently granular
XAU/USD historical source is configured.



## Standard Bullion XAU/USD live feed

DK Capital paper trading uses Standard Bullion's free XAU/USD wholesale gold
bid/ask feed:

```text
https://standardbullion.com/spot-prices.json
```

The feed is polled on a 15-second cadence and every observed quote is written to
the local XAU/USD market tape. The paper engine consumes that tape 15 minutes
later. BUY entries use ask, SELL entries use bid, BUY exits are valued at bid and
SELL exits at ask. The displayed midpoint is only a reference mark.

No Standard Bullion API key is required for the anonymous feed. Discord surfaces
that display the quote include a visible "Data by Standard Bullion" attribution.
