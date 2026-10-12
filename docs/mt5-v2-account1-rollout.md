# DKcapital V2 — MT5 DEMO Account 1 rollout (NOT YET LIVE)

This code is a first implementation candidate, not a declaration of successful broker execution. Never enable trading on a live account. The old `DKcapitalDemo.mq5` is V1 and must not be attached alongside V2.

> **PR #54 / V2.02 status:** New layering support is a code candidate, not a broker-approved EA. Keep `EnableNewEntries=false` and `EnableLayerEntries=false` until independent MetaEditor compilation and demo tests pass. A GitHub Actions Python test does not validate the MQL5 executable.


## What's implemented in this branch

- `mt5/DKcapitalV2.mq5`: three independently tagged MT5 BUY/SELL hedging positions for XAUUSD. **Entry SL must be the exact valid provider-supplied price; missing/invalid provider SL skips the entry. The EA never substitutes a house SL.** Native broker SL and TP1/TP2/TP3 are used; invalid/missing TP remains blank until a valid target is supplied. Broker-confirmed TP1 execution drives breakeven modification on surviving legs.
- Risk: normal total lots = `floor(GBP balance / 100) * 0.01`, volume-step-aware 30/30/40 allocation, Friday Isle-of-Man 0.01 × 3 maximum, £75 planned risk per root signal and £150 aggregate per Account 1, each including stop gap cushion/fees reserve. Both GBP DEMO hedging mode and exact demo login are hard gates. Unknown/manual XAUUSD positions block new entries without ever being closed by this EA.
- Telegram: `GET /v2/demo/feed` transmits signal updates and **explicit provider close/partial** action rows. Provider commentary/TP-hit statuses cannot force broker closes. Historic signals are available for weekend-held position management but are age-gated from new entry. Provider partial close intents record a durable target remaining volume to avoid replaying a percentage on restart. Layer execution is implemented only for explicit timestamped GWS add instructions, and is OFF by default behind a separate `EnableLayerEntries=false` switch. Re-entry execution remains unsupported.
- Telemetry: EA periodically POSTs broker account, native positions and recent deal history to `/v2/demo/telemetry` using existing local bearer authentication. Gateway enforces a configured allowed demo account login and GBP currency, then atomically stores verified snapshots for Discord. `GET /v2/demo/account1` provides the authenticated read-only broker snapshot.
- Discord: if optional channels are configured, edits a pinned Account 1 MT5 demo dashboard approximately every minute; uses actual balance, equity, floating P&L, broker execution history, volume-weighted weekly pip performance and native-stop risk. Alerts once per newly detected stale telemetry, missing-stop/unknown risk or excessive planned risk; acknowledges recovery. Existing Telegram and separate paper dashboards stay intact.


## Version 2.02: controlled GWS layering, demo-only

- **Opt-in switches:** Both `EnableNewEntries=true` and `EnableLayerEntries=true` must be set before an add can execute. The second switch defaults to false, so replacing the EA does not automatically arm layering.
- **Source and command:** An explicit GWS `ADDS` / `ADD` signal-management action is required. `ALLOWED TO LAYER UNTIL MAX <price>` sets a boundary but **does not place an order**. Elite Portfolios is not permitted to invoke this GWS-specific layer workflow.
- **Scope:** The add must resolve to exactly one original signal, not multiple tickets or an ambiguous reply. One 10% layer attempt is permitted across its GWS group. Terminal globals plus broker magic/comment history protect against repeats or restarts. Failed/indeterminate attempts do not retry automatically.
- **Size:** The add is `floor(original three-leg MT5 IN volume × 0.10, broker volume step)`. It must meet the broker minimum lot and is never rounded upward to force an entry.
- **Price:** BUY adds must improve the group's MT5 fill-weighted average entry; SELL adds must improve in the opposite direction. If a valid provider layering boundary is present, the new fill must also respect it. No add may occur once an original TP1 ticket has closed or any original leg is missing.
- **Risk:** Initial entries are limited to 85% of the unchanged £75 group budget, reserving at most 15% for a potential add. Each add must also fit the remaining £75 group / £150 portfolio caps with conservative SL risk and adequate margin. The existing provider SL / stop-improvement protections remain in effect. No adds on Fridays.
- **Exits and accounting:** One fourth ticket uses a native protective stop and TP2. It is included in broker telemetry, Discord trade cards, stop management, group close, and partial close. The initial three-leg 30/30/40 exit allocation is unchanged.
- **Fail closed:** Stale provider messages, incomplete history, unknown positions, missing broker SL, rejected risk valuation, missing or invalid TP2, spread spikes, connection issues, or uncertain broker results block a layer.

### Required V2.02 acceptance tests before activation

1. Compile **V2.02** in the correct Account 1 MetaEditor. Record **0 errors and 0 warnings** and confirm the attached chart is running the new EX5, not an older binary.
2. Leave `EnableLayerEntries=false` initially. Verify a new signal opens only the original three legs, all with native SL and the intended TPs.
3. In a separate controlled scenario, issue `ALLOWED TO LAYER UNTIL MAX` without `ADDS`. Confirm **no layer** is opened.
4. Issue one fresh, directly attributable `ADDS` at a better price. With both switches enabled on the demo, confirm precisely **one** additional broker ticket, volume equal to floored 10% of initial IN volume, its native SL, and TP2.
5. Replay the same Telegram action, restart MT5, then send a second `ADDS` targeting another signal in the same group. Confirm no additional position. Verify broker-history deduplication as well as terminal-global deduplication.
6. Demonstrate skipped layers for worse entry, past layering boundary, closed TP1, missing original ticket, missing SL, invalid TP2, excess margin, stale messages, exceeded £75 group / £150 portfolio risk, and unavailable quote/telemetry.
7. Exercise group full close, percentage partial close, amended stops, and TP1-confirmed BE **with the fourth layer present**. Verify all volumes and native orders reconcile after partial fills and EA restart.
8. Confirm `POST /v2/demo/telemetry` accepts magic-leg 4, account risk includes the layer, and the Discord card only shows the additional entry after an actual MT5 broker fill.
9. Intentionally interrupt Telegram or the gateway during a test. Verify no duplicate action, no unprotected open layer, and a clear operational alert.
10. Keep both entry toggles disabled if any acceptance check fails. Do not deploy this EA to a live account or leave demo layering unattended before broker evidence and risk signoff.

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

## Sunday 11 Oct 2026 -> Monday 12 Oct readiness (Account 1 only)

The original V2 `.ex5` attached to Account 1 may still be the **2.00** binary with an unsafe substitute/house SL. Source was corrected in GitHub to enforce **provider-only initial SL** and is now version **2.01**. **Updating a source file alone does not change the EA already running inside MT5.** Do not allow new orders from 2.00. On the next VPS session:

1. In Account 1's XAUUSD chart, open EA Properties (F7), set **EnableNewEntries=false**. It may remain armed otherwise despite a newer GitHub source file.
2. From `/opt/dkcapital`, run `bash scripts/prepare_monday_account1.sh`. This stages current source to Account 1 and builds/restarts **only** telegram-listener, signal-processor, Discord bot, and the independent watchdog. The script never arms trading, affects Account 2, or restarts MT5.
3. In Account 1's MetaEditor, **close and reopen** `C:\\Program Files\\MetaTrader 5\\MQL5\\Experts\\DKcapitalV2.mq5`, compile with F7 and verify 0 errors, 0 warnings and EA **version 2.01**. Reload the compiled EA on the XAUUSD chart, preserving the same FeedToken, login, native stop-risk settings and WebRequest URL.
4. Confirm the Discord dashboard still reports the correct £1,000 GBP DEMO login, live telemetry `feed_http=200`, and that `telegram-recovery-status.json` says `healthy=true`. Recoveries retain original message dates and the EA only admits new entries age <=180 seconds; after a long listener outage, stale entries correctly remain blocked.
5. Once version 2.01 is actually attached, new entries may be armed for controlled first-trade demo validation. Discord's watchdog will warn if the EA is disarmed, if MT5/gateway reporting becomes stale, if Telegram recovery stalls, or if Discord itself stops producing its heartbeat. The independent watchdog cannot guarantee delivery if Discord itself is unavailable.
6. First broker fill remains a required acceptance milestone: observe 3 separate native-protected positions, lot allocation, exact provider SL and all applicable TPs, group/portfolio stop-loss estimates, and TP1 broker-fill -> two BE updates. **No one has yet verified this on the demo broker.**

The Telegram recovery scan is intentionally bounded (historical entries cannot bypass the 180-second rule). It does not recover deletions that took place entirely during a disconnect, and extreme message volumes may exceed the catch-up window; incomplete scans generate alerts. These controls substantially reduce missing-post risk but cannot guarantee every provider action is observed under prolonged outages.
