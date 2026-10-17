# DK Capital V2.05: broker-confirmed reporting and execution diagnostics

**PR #58 only. Not deployed. Not compiled/accepted by MT5 broker.**
The code remains GBP DEMO Account 1 only. Do not use this EA on a Vantage
live USD account without a separate currency, account and risk review.

## Discord destinations
- Results: 1561095999139877046, MT5 broker-only complete trade results, per-TP-leg GBP realised P&L, opening and closing commissions, swaps, and manual closures.
- Changes: 1561099532467445961, broker entry/layer fills, native SL and TP amendments, manual position reductions, drawdown warnings and protection mismatch warnings.
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
- daily drawdown is a warning, never an entry rejection
- broker quote unavailable, spread too wide, stale broker tick or outside entry zone
- missing/invalid provider SL or required native target
- unavailable risk budget, minimum 3-leg size, insufficient broker margin
- broker order rejected or incomplete multi-leg execution, with MT5 retcode
- unavailable layer opt-in, non-improving price, missing original trio or TP1 already closed, insufficient 10% layer lot, risk and native stop failures.

Decisions carry MT5 broker BID/ASK, timestamp, signal age and retcode, when
available, and are limited to a bounded 24-hour telemetry window.
Old trades cannot be retrospectively diagnosed as if the new EA was running.

## Daily threshold

This setting is informational. The 2% equity threshold is reported through
telemetry and the changes channel. The 180-second entry window, provider
native stops, margin, spread, broker order state, position sizing and
group/portfolio limits remain unchanged. The threshold uses the first
observed Isle of Man daily balance, not a reconstructed opening balance.

## Balance-based lot sizing with 4% maximum risk at provider SL

The **target lots still scale from account balance** exactly as before:
`floor(balance / GBP 100) * 0.01` XAUUSD total lots, then 30/30/40 across
TP1/TP2/TP3, with the existing smaller Friday target. The EA iterates
downwards by broker lot steps when planned broker-stop risk would exceed its
budget. No additional lot sizing multiplier is applied.

A separate stop-risk ceiling is `MaxSignalRiskPct=4.0`. The maximum planned
stop-risk of all native TP1/TP2/TP3 legs plus any optional fourth GWS layer is
4% of `min(ACCOUNT_BALANCE, ACCOUNT_EQUITY)`, including the current stop
slippage/padding allowance. Existing balance-scaled GBP 75 group / GBP 150
portfolio limits and margin/stop protections remain additional ceilings.
The layer can only open when its full risk fits remaining signal headroom. The provider's original protective SL is NEVER widened to allow a larger position; tighter broker native stops are permitted by existing protective-stop logic.

No hard percentage is reserved from the 4% ceiling for a hypothetical future
layer; this maximizes eligible original entries. The optional layer may therefore
not fit at all after the original three legs open. This is deliberate.

**Minimum lots:** A GBP 1,000 account has a GBP 40 total signal-risk budget if
equity is not lower. Three 0.01-minimum XAUUSD legs with a meaningful provider
stop can exceed GBP 40 even at the lowest allowable broker volume. When the
minimum three-leg structure will not fit, the EA will NOT place an oversized
trade; it records `MINIMUM_THREE_LEG_SIZE_EXCEEDS_SIGNAL_RISK`.
This is not a daily loss lockout.

The GBP-based calculation is DEMO Account 1 only. The planned USD/Vantage
live account must be redesigned for its currency, hedging mode, gold contract,
broker min/step, stop-level, fee rates and risk validation. Broker slippage/gaps
can cause actual realised loss to exceed the planned 4% ceiling.
The separate paper engine retains its own settings.

## Mandatory test and rollout gates
1. Pass CI Python tests for reporting, broker events, signal parser, gateway normalizer, and MQL source contracts.
2. Compile in matching MT5 MetaEditor with zero warnings and errors. Confirm the running EX5 is V2.05, not the preexisting V2.04. A GitHub CI pass is NOT MQL compilation.
3. Preserve EnableNewEntries=false and EnableLayerEntries=false until approved controlled tests in GBP DEMO Account 1.
4. Confirm correct account, native stops, correct TP1/TP2/TP3 and 30/30/40 fills. Verify actual broker deal reasons for both TP and SL.
5. Manually reduce and fully close an MT5 trade. Ensure changes channel and final result card both reflect genuine broker deals, including all leg profit/fees.
6. Exercise a tighter SL, BE after TP1, an unsuccessful invalid stop modification and manual modification. Confirm no false positive "changed" notification.
7. Exercise explicit GWS ADD message, permission-only message, price boundary, Friday restriction, one-layer-per-group, below-minimum lot and sufficient-size cases. Verify actual broker native SL/TP2 on the fourth ticket when filled.
8. Exceed the 2% threshold on a test account; verify the warning, monitor reset at Isle of Man midnight, and that eligibility follows the independent per-trade checks.
9. Disconnect Discord/gateway and restart the bot to verify event persistence, deduplication and no protected-position interference.
10. Demonstrate the balance-based target lots remain identical, the 4% aggregate signal cap is calculated using the unchanged provider SL for all three legs and any optional layer, and every minimum-broker-lot failure is explicitly diagnosed.
11. Retain known working EA binary. Disarm both switches if any gate fails. Do not deploy to live USD account or reset account history as part of reporting rollout.

## Caveats
The EA polls every three seconds in normal operation and reports telemetry
every 30 seconds, with broker-event-triggered urgent reports where possible.
Discord may therefore lag execution; Discord is never part of broker stop
execution. The existing watchdog remains required.
