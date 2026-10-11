# DKcapital Ruleset V2 - XAUUSD, demo-only

Status: agreed trading specification for the next MT5 EA implementation. **NOT YET ENFORCED BY THE CURRENT EA.** Enabling live or demo entries in the existing EA does not implement these rules. All new order execution remains disabled pending compilation and acceptance testing.

## 1. Confirmed decisions

- Instrument: broker's XAUUSD symbol only. Signals: GWS VIP and Elite Portfolios; independent provider identity required.
- Current deployment: MT5 **demo** only; each account uses its own equity, broker symbol, lots, stops, ledger and limits.
- Each accepted Telegram signal should create **exactly three separately tagged broker hedging positions**, one for TP1, one for TP2 and one for TP3. All three legs must carry broker-side stop-loss orders. Native TP orders are preferred where targets are supplied.
- Normal total signal lots: **0.01 lots per complete GBP 100 of account balance**, floored to broker lot increment and respecting three-leg minimums. For GBP 1,000, target 0.10 lots in total, e.g., 0.03 / 0.03 / 0.04 lots assigned to TP1 / TP2 / TP3.
- **Friday**: target **0.01 lots per leg, three legs, total 0.03 lots per signal**, provided normal balance allocation, stop-risk and broker constraints allow this volume. Never use Friday as a reason to increase the normal permitted total lot size.
- **GBP 75 maximum estimated planned loss per signal, shared by all three legs**, NOT GBP 75 per leg. Reduce total lots, preserving three minimum-volume positions, where necessary. If all three broker-minimum positions would exceed GBP 75 combined planned loss or cannot be afforded, do not enter. Risk must use each leg's proposed **broker fill price to broker SL**, contract size, FX conversion into GBP, commission estimate and a slippage cushion; recheck and reconcile after execution. Market gaps and execution costs can still exceed this intended cap.
- On Friday **close all remaining EA-managed XAUUSD positions before the broker's weekly trading session closes**. Applies to signals opened any day of the week, both BUY and SELL, all three legs, all EA-owned re-entries or layers. **Never close unrelated/manual positions or positions owned by another EA.**
- Once the Friday close-out begins, block all **new** entries and layering until the next weekly gold trading session. Cancel any unfilled EA-owned entry orders to prevent weekend execution. Do not reopen an old signal on Sunday/Monday; require a new, fresh signal.
- Stop loss and position management continue normally before close-out. Close-out is a higher-priority risk action than TP monitoring or Telegram connectivity.

## 2. Friday weekly close procedure

Implementation requires broker-session-aware scheduling: query MT5 `SymbolInfoSessionTrade(symbol, FRIDAY, index, from, to)`, account for multiple trade sessions and an end-time crossing midnight, and use current **broker server time** for comparisons. Do **not** hard-code an Isle of Man or UTC clock hour; symbol and broker session schedules may change for DST and holidays. Independently monitor exceptional early closes and broker close-only modes. If an authoritative weekly-close deadline cannot be resolved, disable Friday new orders, alert the operator and flatten while trading is still available.

Suggested initial adjustable offsets (not yet confirmed by the user):
- No new Friday entries within **120 minutes** before the broker's weekly XAUUSD close.
- Begin Friday flattening **60 minutes** before that close and target **zero EA-managed XAUUSD positions by 30 minutes before close**.

At flatten start:
1. Obtain fresh broker position and pending-order inventories, not just the Telegram feed.
2. Stop accepting new signals for the week. Cancel all unfilled EA-owned XAUUSD entry orders.
3. Enumerate each account's EA-owned XAUUSD leg using recorded ownership tags/magic IDs plus durable broker-ticket reconciliation.
4. Close remaining volumes using a market close and confirm actual broker deal history. Re-query broker positions to verify all three legs are zero.
5. Retry failures at a rate-limited interval while trading remains open. Log order retcodes and alert immediately if positions remain after the target deadline.
6. Persist a weekly lock across terminal restarts and reconnects. Never retry **opening** positions while locked. Repeat flattening when terminal reconnects before the close.
7. If broker trading is already closed, do not claim successful flattening. Alert the operator and retain broker-native protective SLs; reconcile on reopening, without trading on stale signals.

The independent Friday/weekend close timer **must not depend on the Telegram parser or HTTP signal feed being online**. The EA must manage Friday flattening even if the feed is stale, unreachable, or returns an error.

**Friday size clock**: apply the smaller 0.03 total as soon as either the broker or Isle of Man local calendar enters Friday; use broker trading sessions for Friday closing deadlines.

## 3. Entry and lot allocation

Evaluate one unique signal ID per Telegram provider message, and maintain an idempotent persistent journal including account login, signal ID, leg number, broker order and position tickets, and confirmed fills.

Normal total volume is `floor(GBP_balance / 100) * 0.01` lots before broker volume rounding, available-margin and loss-cap checks. The first 2 legs approximately target 30% each and final leg 40% of volume; volume-step-aware rounding must preserve three independently closable legs and **never exceed** the total permitted volume. Example GBP 500 -> target 0.05 total -> 0.01 / 0.02 / 0.02. Friday GBP 1,000 -> 0.01 / 0.01 / 0.01.

Risk overrides fixed lots: calculate total stop loss estimate for all proposed legs, iteratively reduce volume to a legal three-leg combination while at or below GBP 75 planned loss. Skip if infeasible. Do not silently open fewer than three legs contrary to this configuration. Size from account **balance**, not leveraged notional or margin. For non-GBP demo currencies, GBP-equivalent balance and risk calculations require a fresh verified conversion; otherwise block.

For normal entries, only take fresh signals with valid instrument, direction and source, correct zone and acceptable spread. Never chase a signal outside the configured entry tolerance. Every opened leg must have a broker-side protective stop, with immediate remediation/closure if an order arrives without one. Initial three-leg fills must be reconciled; never assume an entry has filled because a request returned success.

## 4. Leg management

- TP1 leg: exit at provider TP1 when valid; after the broker confirms TP1 fill, tighten surviving legs to their **individual actual fill prices** (breakeven), except when already protected by a tighter stop. No stop should be widened.
- TP2 leg: exit at provider TP2.
- TP3 leg: exit at provider TP3 and finish the signal.
- Provider SL/TP edits: map edited targets to the correct leg, validate direction, and modify broker orders. Missing TP is tracked as awaiting provider update; never create a fictitious target or mark it complete.
- An unfilled signal cancellation prevents entry; a post-fill explicit provider closing instruction closes the owned legs when the instruction identifies that trade. Track which provider each leg belongs to.
- Provider result messages are **not** evidence of actual TP/SL fills. Broker deal/position history is authoritative. Explicit provider close instructions are separate actions.
- Layering or re-entry requests must have distinct unique IDs, separate caps and clear attribution; no automatic position duplication from edits, restarts or signal replay.
- Broker-native TP/SL is preferred over software-only price polling. Handle transient disconnections, quote age, request retries, partial fills and terminal restarts.

## 5. Risk constraints requiring further discussion

- Daily equity drawdown threshold: previously **proposed at 10%**, not yet explicitly confirmed in this ruleset.
- Aggregate outstanding XAUUSD stop-loss exposure across independent signals/providers: **not yet agreed**.
- Maximum simultaneous signal groups and provider priority: **not yet agreed**.
- Provider missing-SL temporary stop and TP-missing handling: needs acceptance tests and rules sign-off.
- Exceptional market conditions (news, spreads, slippage, Friday short sessions): needs acceptance tests and rules sign-off.

## 6. Acceptance tests before demo order activation

1. A GBP 1,000 Monday signal opens TP1=0.03, TP2=0.03, TP3=0.04 (assuming 0.01 increment and enough stop-risk budget), and each has a native SL and assigned TP.
2. A GBP 1,000 Friday signal opens TP1=0.01, TP2=0.01, TP3=0.01 **only when** the three-leg GBP loss estimate is at most GBP 75.
3. A wide-stop signal is reduced to a valid three-leg volume or skipped; **never** risks GBP 75 per leg.
4. GBP 200 with 0.01 broker minimum: skip (cannot satisfy three separate legal positions).
5. At Friday flatten deadline, positions from Tuesday and Friday, BUY and SELL, all three legs, are closed; manual XAUUSD positions are untouched.
6. Telegram feed unavailable: weekly flatten still works; terminal restart does not clear weekly no-new-orders lock.
7. Broker close failure or early holiday close: no false success; immediate alert and retry if trade session permits, otherwise retain stops and reconcile after reopen.
8. TP1 must be confirmed in MT5 before remaining legs' BE stops change. SL and TP edits must route to the intended original signal and leg.
9. Account switches away from DEMO or expected login: **no new orders**.
10. Duplicate or delayed provider signals: no duplicate entry after crash/restart.
