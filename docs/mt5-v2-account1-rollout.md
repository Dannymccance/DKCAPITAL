# DKcapital V2 — MT5 DEMO Account 1 rollout (NOT YET LIVE)

This code is a first implementation candidate, not a declaration of successful broker execution. Never enable trading on a live account. The old `DKcapitalDemo.mq5` is V1 and must not be attached alongside V2.

## What's implemented in this branch

- `mt5/DKcapitalV2.mq5`: three independently tagged MT5 BUY/SELL hedging positions for XAUUSD, with native broker SL and TP1/TP2/TP3. Invalid/missing TP remains broker-blank until a valid target is supplied. Broker-confirmed TP1 execution drives breakeven modification on surviving legs.
- Risk: normal total lots = `floor(GBP balance / 100) * 0.01`, volume-step-aware 30/30/40 allocation, Friday Isle-of-Man 0.01 × 3 maximum, £75 planned risk per root signal and £150 aggregate per Account 1, each including stop gap cushion/fees reserve. Both GBP DEMO hedging mode and exact demo login are hard gates. Unknown/manual XAUUSD positions block new entries without ever being closed by this EA.
- Telegram: `GET /v2/demo/feed` transmits signal updates and **explicit provider close/partial** action rows. Provider commentary/TP-hit statuses cannot force broker closes. Historic signals are available for weekend-held position management but are age-gated from new entry. Provider partial close intents record a durable target remaining volume to avoid replaying a percentage on restart. Layer and re-entry execution is NOT enabled.
- Telemetry: EA periodically POSTs broker account, native positions and recent deal history to `/v2/demo/telemetry` using existing local bearer authentication. Gateway enforces a configured allowed demo account login and GBP currency, then atomically stores verified snapshots for Discord. `GET /v2/demo/account1` provides the authenticated read-only broker snapshot.
- Discord: if optional channels are configured, edits a pinned Account 1 MT5 demo dashboard approximately every minute; uses actual balance, equity, floating P&L, broker execution history, volume-weighted weekly pip performance and native-stop risk. Alerts once per newly detected stale telemetry, missing-stop/unknown risk or excessive planned risk; acknowledges recovery. Existing Telegram and separate paper dashboards stay intact.

## Server environment — do not paste secrets into chat

Keep `/opt/dkcapital/.env` at mode 0600. Add or update the settings below **without overwriting other variables**:

- `MT5_ACCOUNT1_LOGIN=<your confirmed GBP demo login>` — REQUIRED for telemetry acceptance and EA identity.
- `MT5_GATEWAY_TOKEN=<already configured secret>` — keep the existing token; same value is entered privately in MT5 EA inputs.
- `MT5_DEMO_FEED_ENABLED=true`, `MT5_GATEWAY_PORT=8765`.
- `MT5_DASHBOARD_CHANNEL_ID=<ID of your chosen Discord dashboard channel>` — optional.
- `MT5_ALERT_CHANNEL_ID=<ID of your alerts channel>` — optional.
- `MT5_XAU_PIP_SIZE=0.01` — **PROVISIONAL**, broker and provider pip convention must be confirmed.
- `MT5_ACCOUNT1_TELEMETRY_PATH=/app/data/mt5-account1.json` — shared Docker volume.

The Discord container and MT5 gateway container must be rebuilt after deploying the new source; do not restart `telegram-listener`, `signal-processor`, `paper-engine`, or unrelated projects as part of this change. Backend gateway must still be bound **only** to `127.0.0.1:8765`; neither token nor broker account credentials belong in Discord.

## MT5 Account 1 installation

1. Check account really is **GBP 1,000 demo, hedging**, correct login, and XAUUSD symbol specification. Existing observation: MetaQuotes-Demo, not Vantage. Read the correct broker's contract size, lot steps, stops level, margin and local pip convention before trusting any outcome.
2. Copy `mt5/DKcapitalV2.mq5` to **Account 1 only** in its terminal's `MQL5/Experts`. Compile with Account 1 MetaEditor. A GitHub CI pass is **NOT a MetaEditor compilation**; capture the actual error/warning counts and inspect broker Journal.
3. In MT5 `Tools > Options > Expert Advisors`, permit `http://127.0.0.1:8765` as WebRequest endpoint. Attach **only** the V2 EA to a single XAUUSD chart.
4. Set `ExpectedDemoLogin` to the account's login, `GoldSymbol` to the actual broker symbol, and `FeedToken` to the current gateway secret. Leave `EnableNewEntries=false`. Allow EA event processing for telemetry/stop management, but do not enable new trading.
5. Verify `POST /v2/demo/telemetry` is accepted (account data is visible via authenticated `GET /v2/demo/account1`), that the Discord dashboard says DEMO and shows the correct actual balance and that faults can be delivered to `#dkcapital-alerts`.
6. Test new entry execution, all three broker SLs/TPs, exact lots, TP1-fill-confirmed BE, edited targets, provider explicit closes, partials, 3-leg partial failure and restart recovery **on the demo account**. Also validate risk-limit tests and Friday/market-closed behaviour. Confirm no unapproved manual/EA account 2 positions are touched.
7. Sign off final concurrency limit, layering/reentry restrictions, broker-specific pip size and daily drawdown policy. Only then consider switching `EnableNewEntries` on for Account 1, with the other account unchanged.

## Known gating work and cautions

- The MQL5 source has **not been compiled in MetaEditor or deployed to the VPS** from this GitHub change. Broker execution should remain disabled.
- Discord report does not replace MT5 native SL. It is not a risk-controlling execution path. A separate independent VPS watchdog is needed to detect a dead Discord bot itself.
- Weekend positions are not automatically liquidated; XAUUSD gaps can exceed the intended £75/£150 planned risk limits. The risk cushion is a conservative estimate, **not a guaranteed loss ceiling**.
- A broker terminal auto-update under Wine 9 previously caused an Account 1 restart loop. Stage a Wine 10+ migration for Account 1 in isolation and verify MT5 updates before unattended use.
- Telegram was previously offline: current history has missing messages. We can postpone a backfill while there are no bot-owned positions, but **live history reconciliation for missed provider exits is mandatory before unattended trading**. Do not automatically trade recovered old entries.
- The pip convention and broker-deal timestamps (MetaTrader server timezone vs UTC, including DST) need verification with actual test fills before treating weekly pip numbers as audited.
- Discord will not yet deliver every possible EA print/log fault. Forwarding explicit EA broker errors, independent service watchdog and structured incident escalation remains acceptance work.

## Definition of operational

**Not operational** if any of the following are true: MQL5 has compile warnings/errors, MT5 feed/telemetry not healthy, V2 configuration unapproved, no native SL on all positions, three-leg fill not confirmed, wrong broker or currency, erroneous daily/weekly performance, missed provider exits unrecovered, or the Discord alert pipeline cannot report faults.

Keep `EnableNewEntries=false` until the above checks are completed and documented.
