# DK Capital V2.05: broker-confirmed reporting and execution diagnostics

**PR #58 only. Not deployed. Not compiled/accepted by MT5 broker.**
The code remains GBP DEMO Account 1 only. Do not use this EA on a Vantage
live USD account without a separate currency, account and risk review.

## Discord destinations
- Results: 1561095999139877046, MT5 broker-only complete trade results, per-TP-leg GBP realised P&L, opening and closing commissions, swaps, and manual closures.
- Changes: 1561099532467445961, broker entry/layer fills, native SL and TP amendments, manual position reductions, loss-lock and protection mismatch warnings.
- TP/SL alerts: 1561099506337063073, only MT5 closing deals with a verified DEAL_REASON_TP or DEAL_REASON_SL.
- All have environment overrides documented in .env.example. They do not control trading.

A persisted event queue and broker deal-ticket dedup prevent routine repeats.
The first valid telemetry baseline suppresses historical event floods.
There remains a narrow send-before-persist failure window where Discord may
accept an event and a retry may send a duplicate. Do not claim true distributed
exactly-once delivery.

## Provider messages and native stops
- GWS: SL ALSO, SL ONE MORE TIME, explicit BE, ALLOWED TO LAYER UNTIL MAX and ADDING NOW are distinct commands. A boundary alone never opens a layer.
- The EA must not worsen an existing protective native SL based on a provider update. The provider's less protective stop is NOT silently applied.
- A broker SL or TP update is confirmed only when MT5 position telemetry reflects it. Warnings for missing improvements are explicitly unconfirmed.
- TP1 broker execution moves surviving native SLs to BE under the existing DK Capital house rule, even if a provider commentary message differs.
- A three-leg fresh entry is refused when a required native TP1, TP2 or TP3 is absent or already invalid, rather than opening an unplanned runner.
- The 16 October GWS messages with duplicate TP3 numbers and missing TP2 are intentionally skipped until the provider clarifies or source parsing is reviewed. Do not invent a target.
- A 10% layer is floored to broker lot step, never rounded up. With 0.03 original lots and 0.01 minimum lots, 0.003 rounds down to 0, so an add cannot execute. Exact structured blocker is exposed.
- Never infer provider TP/SL hit from text or external gold quotes. All profit and stop hit cards use MT5 closing deals.
- Broker deal history and current positions must reconcile before declaring a trade fully closed. R multiple and exact before/after account balances are omitted if history does not support them.

## Entry rejection reasons
The 3-minute window remains. EA now reports observed account-specific and
broker-specific reasons including:
- entry disabled, account mismatch, gateway/telemetry unavailable, broker pending orders, terminal not authorised to trade
- locked 2% daily equity-loss threshold
- broker quote unavailable, spread too wide, stale broker tick or outside entry zone
- missing/invalid provider SL or required native target
- unavailable risk budget, minimum 3-leg size, insufficient broker margin
- broker order rejected or incomplete multi-leg execution, with MT5 retcode
- unavailable layer opt-in, non-improving price, missing original trio or TP1 already closed, insufficient 10% layer lot, risk and native stop failures.

Decisions carry MT5 broker BID/ASK, timestamp, signal age and retcode, when
available, and are limited to a bounded 24-hour telemetry window.
Old trades cannot be retrospectively diagnosed as if the new EA was running.

## Daily loss semantics
New input MaxDailyEquityLossPct=2.0. A terminal global per Isle of Man date
latches once actual MT5 account equity falls to 98% or below the first observed
GBP balance for that calendar day. That prevents new entries AND adds but does
not close existing positions, and therefore DOES NOT guarantee no more than
2% realised loss. A newly deployed EA mid-session has a first-observation
baseline rather than a reconstructed midnight baseline. Deposits and unusual
non-trading balance changes are not yet handled. Treat these as live rollout
blockers for the separately planned USD/Vantage account.

## Mandatory test and rollout gates
1. Pass CI Python tests for reporting, broker events, signal parser, gateway normalizer, and MQL source contracts.
2. Compile in matching MT5 MetaEditor with zero warnings and errors. Confirm the running EX5 is V2.05, not the preexisting V2.04. A GitHub CI pass is NOT MQL compilation.
3. Preserve EnableNewEntries=false and EnableLayerEntries=false until approved controlled tests in GBP DEMO Account 1.
4. Confirm correct account, native stops, correct TP1/TP2/TP3 and 30/30/40 fills. Verify actual broker deal reasons for both TP and SL.
5. Manually reduce and fully close an MT5 trade. Ensure changes channel and final result card both reflect genuine broker deals, including all leg profit/fees.
6. Exercise a tighter SL, BE after TP1, an unsuccessful invalid stop modification and manual modification. Confirm no false positive "changed" notification.
7. Exercise explicit GWS ADD message, permission-only message, price boundary, Friday restriction, one-layer-per-group, below-minimum lot and sufficient-size cases. Verify actual broker native SL/TP2 on the fourth ticket when filled.
8. Exceed the 2% threshold on a test account; verify no new entries/layers, persistence after restart and Isle of Man midnight reset. Check the rule against *equity*, not just realised trades.
9. Disconnect Discord/gateway and restart the bot to verify event persistence, deduplication and no protected-position interference.
10. Retain known working EA binary. Disarm both switches if any gate fails. Do not deploy to live USD account or reset account history as part of reporting rollout.

## Caveats
The EA polls every three seconds in normal operation and reports telemetry
every 30 seconds, with broker-event-triggered urgent reports where possible.
Discord may therefore lag execution; Discord is never part of broker stop
execution. The existing watchdog remains required.
