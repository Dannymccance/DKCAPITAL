# DKcapital Ruleset V2 - XAUUSD, demo-only

Status: agreed trading specification for the next MT5 EA implementation. **NOT YET ENFORCED BY THE CURRENT EA.** Enabling live or demo entries in the existing EA does not implement these rules. All new order execution remains disabled pending compilation and acceptance testing.

## 1. Confirmed decisions

- Instrument: broker's XAUUSD symbol only. Signals: GWS VIP and Elite Portfolios; independent provider identity required.
- Current deployment: MT5 **demo** only; each account uses its own equity, broker symbol, lots, stops, ledger and limits.
- Each accepted Telegram signal should create **exactly three separately tagged broker hedging positions**, one for TP1, one for TP2 and one for TP3. All three legs must carry broker-side stop-loss orders. Native TP orders are preferred where targets are supplied.
- Normal total signal lots: **0.01 lots per complete GBP 100 of account balance**, floored to broker lot increment and respecting three-leg minimums. For GBP 1,000, target 0.10 lots in total, e.g., 0.03 / 0.03 / 0.04 lots assigned to TP1 / TP2 / TP3.
- **Friday**: target **0.01 lots per leg, three legs, total 0.03 lots per signal**, provided normal balance allocation, stop-risk and broker constraints allow this volume. Never use Friday as a reason to increase the normal permitted total lot size.
- **GBP 75 maximum estimated planned loss per signal, shared by all three legs**, NOT GBP 75 per leg. Reduce total lots, preserving three minimum-volume positions, where necessary. If all three broker-minimum positions would exceed GBP 75 combined planned loss or cannot be afforded, do not enter. Risk must use each leg's proposed **broker fill price to broker SL**, contract size, FX conversion into GBP, commission estimate and a slippage cushion; recheck and reconcile after execution. Market gaps and execution costs can still exceed this intended cap.
- **No scheduled Friday/weekend forced liquidation.** Do not close EA-managed XAUUSD positions based solely on the clock, approaching weekly market closure, a Telegram outage, or a date change. Positions may remain open across the market weekend with broker-native protective SL/TP orders.
- Close any EA-owned position or affected signal group only if (a) the broker executes that leg's native TP or SL, or (b) DKcapital has parsed an **explicit, applicable trade-close instruction from its originating Telegram provider** and attributed it to the correct provider, signal, side, and scope. Explicit partial-close instructions affect only their requested volume. A generic weekend warning, commentary, or provider announcement that a TP/SL was hit is **not** an instruction to forcibly liquidate the MT5 trade.
- Provider-originated closes take priority over waiting for a TP. A close instruction must be persisted as an idempotent pending action until broker execution is confirmed; if the market is closed, alert, retain native protective orders, and execute the **still-applicable explicit close instruction** when the broker next permits trading. Never silently mark a position closed based only on a Telegram message. Cancel a pending close if a later unambiguous provider instruction supersedes it before execution, with an audit trail.
- **Friday remains a trading day** while broker XAUUSD trading is open and normal entry/spread/risk safeguards pass. Friday trades use the separately agreed 0.01 x 3 size; the previous proposed Friday 2-hour entry cutoff, 1-hour flatten, 30-minute zero-position deadline and weekly lock are **withdrawn**.

## 2. Friday and weekend position handling

1. Use the Isle of Man local date for the Friday reduced-sizing rule; respect the broker's available trading sessions for order placement and execution. Broker weekly market-close time is informational, **not** an automatic close trigger.
2. Continue managing provider SL/TP changes and broker fills normally on Fridays. All three separate MT5 legs maintain their broker-native protective SL orders; a valid TP order is attached to each leg when available.
3. If the originating Telegram source explicitly instructs **close, exit, close all gold, close all longs/shorts, or a scoped partial close**, parse the actual meaning and match it only to its own intended DKcapital-managed positions. Distinguish an explicit action from commentary and a provider-reported TP/SL result.
4. Confirm close or partial-close using **MT5 deal/position history**, with retries for transient trade errors and no duplicate close requests. An instruction to close must not accidentally open an opposite hedge.
5. When broker trading is unavailable, retain the explicit pending close instruction (with source, message ID, timestamp, applicable signal IDs and scope), alert that the position is still open, and attempt the close once trading resumes while the instruction remains applicable. Keep protective SL/TP in place. Do **not** liquidate just because the weekend ended.
6. If Telegram is disconnected, **never infer a provider exit**. Continue monitoring native broker TP/SL outcomes. Reconcile messages after reconnect, avoiding duplicate processing or stale new entries. Any explicitly received and still-applicable pending close instruction remains enforceable independently of a subsequent Telegram outage.
7. Never close unrelated/manual XAUUSD positions or those managed by other EAs. For existing DKcapital positions, provider close routing must account for their exact originating provider and signal group; a provider-wide instruction is limited to that provider's trades.

**Weekend risk:** Holding through a closed market can produce a gap beyond SL at reopening. The agreed GBP 75 combined stop-risk limit is a **planned loss cap, not a guaranteed maximum realised loss**.

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
- A weekend does not generate an exit on its own. Only broker-native TP/SL execution or a correctly parsed explicit Telegram exit can close EA-managed gold positions.

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
5. Friday close approaching: positions from earlier weekdays and Friday **remain open** absent native TP/SL fills or explicit valid provider close instruction. EA/manual ownership remains correctly scoped.
6. Telegram feed unavailable near weekend: no automatic Friday liquidation occurs, already broker-held TP/SL orders remain, and no inferred close is generated.
7. A source-specific Telegram 'close all longs' instruction closes only that provider's matching EA-managed BUY positions after MT5 execution confirmation; if the broker is closed, persist pending exit and alert until executable. Generic commentary or a provider saying 'TP hit' does not force a broker close.
8. TP1 must be confirmed in MT5 before remaining legs' BE stops change. SL and TP edits must route to the intended original signal and leg.
9. Account switches away from DEMO or expected login: **no new orders**.
10. Duplicate or delayed provider signals: no duplicate entry after crash/restart.
