# DKcapital Ruleset V2 - XAUUSD, demo-only

Status: agreed trading specification for the next MT5 EA implementation. **NOT YET ENFORCED BY THE CURRENT EA.** Enabling live or demo entries in the existing EA does not implement these rules. All new order execution remains disabled pending compilation and acceptance testing.

## 1. Confirmed decisions

- Instrument: broker's XAUUSD symbol only. Signals: GWS VIP and Elite Portfolios; independent provider identity required.
- Current deployment: MT5 **demo** only; each account uses its own equity, broker symbol, lots, stops, ledger and limits.
- Each accepted Telegram signal should create **exactly three separately tagged broker hedging positions**, one for TP1, one for TP2 and one for TP3. All three legs must carry broker-side stop-loss orders. Native TP orders are preferred where targets are supplied.
- Normal total signal lots: **0.01 lots per complete GBP 100 of account balance**, floored to broker lot increment and respecting three-leg minimums. For GBP 1,000, target 0.10 lots in total, e.g., 0.03 / 0.03 / 0.04 lots assigned to TP1 / TP2 / TP3.
- **Friday**: target **0.01 lots per leg, three legs, total 0.03 lots per signal**, provided normal balance allocation, stop-risk and broker constraints allow this volume. Never use Friday as a reason to increase the normal permitted total lot size.
- **GBP 75 maximum estimated planned loss per signal, shared by all three legs**, NOT GBP 75 per leg. Reduce total lots, preserving three minimum-volume positions, where necessary. If all three broker-minimum positions would exceed GBP 75 combined planned loss or cannot be afforded, do not enter. Risk must use each leg's proposed **broker fill price to broker SL**, contract size, FX conversion into GBP, commission estimate and a slippage cushion; recheck and reconcile after execution. Market gaps and execution costs can still exceed this intended cap.
- **GBP 150 maximum combined planned stop-loss exposure across all simultaneously open DKcapital signal groups on EACH GBP 1,000 demo account** (15% of the stated GBP 1,000 balance). This covers every native TP leg plus any permitted layer or re-entry, from either Telegram provider. It is not GBP 150 per provider or across both MT5 accounts. Existing exposure plus the entire proposed three-leg order group may not exceed GBP 150. Retain the GBP 75 group cap simultaneously. Limits for different account balances or automated scaling of this GBP 150 figure are **not yet agreed**.
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

## 3A. Combined stop-loss exposure and admission control (confirmed for GBP 1,000)

- **Apply both limits** to each account's intended open XAUUSD portfolio: maximum GBP 75 planned stop loss for any root signal group (including its additional layers), and maximum GBP 150 planned stop loss **summed across all DKcapital-owned groups** on that MT5 account. It is permissible to open two groups at GBP 75 each, or more smaller-risk groups if subsequent concurrency rules permit.
- Count all THREE broker-held legs, EA-owned outstanding entry requests, order fills awaiting reconciliation, and every active layer/re-entry. **Reserve risk before the first leg is submitted**, and do not release reservation for a rejected or uncertain order until reconciled with broker history. No double-spend of a shared risk allowance across simultaneous Telegram signals.
- Group risk is conservatively calculated from each remaining leg's **actual broker entry** to its protective SL, measured as the maximum estimated loss relative to entry (no positive risk credit from profitable legs or opposite-direction hedges), including expected execution costs and an allowance for slippage. Stops tightened to genuine breakeven can free planned loss budget only after MT5 confirms modification, while maintaining any required fee/slippage reserve.
- Re-query and recalculate limits immediately before each entry and after each confirmed fill, stop modification, close or reconnect. All exposure is summed **without offsetting BUY against SELL**. A group may enter at reduced lots if there is remaining risk allowance, but must still satisfy the required three separately closable legal legs. Otherwise skip or wait for a **fresh** signal; never queue an expired market entry to execute later.
- Example on a GBP 1,000 account: GBP 75 already reserved + GBP 75 proposed = GBP 150 (permitted); GBP 110 already reserved + GBP 75 proposed = GBP 185 (new group may use at most GBP 40, if three legal broker lots still fit); GBP 150 already reserved -> no new signal or layer.
- If an open portfolio exceeds a planned-risk limit due to broker slippage, stop edits, gap, conversion moves or an unexpectedly filled order, **block new exposure, log and alert**. Do not automatically liquidate positions merely to force the modeled budget back below GBP 150; existing exit instructions and broker-held protective stops still apply.
- If broker positions, volume, protective SL status, ledger ownership, currency conversion or open-order state cannot be reconciled, fail closed for **new entries** but keep existing position management and explicit provider exits operating.
- Available margin and protective order feasibility are independent admission requirements. The GBP 150 risk cap is not a margin or guaranteed maximum drawdown cap.

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

## 4A. Proposed concurrency, layering and re-entry policy (NOT YET APPROVED)

These are implementation recommendations to discuss next. They do not override the confirmed GBP 75 / GBP 150 caps:

1. **Concurrent groups:** Allow up to **three** independently active root signal groups per MT5 account (from either or both sources), provided their combined planned stop exposure is within GBP 150. Thus two GBP 75 groups can coexist; a third is eligible only if remaining budget and broker lot sizes permit. No provider gets automatic priority: process eligible fresh signals in stable Telegram event-time/receipt order, and reject duplicates.
2. **Signal independence and hedging:** A GWS BUY and an Elite Portfolios SELL may coexist, as may two distinct GWS signals. Keep provider, root message ID, side and group ID separate. Don't net their risks, coalesce independent signals or cancel an existing provider trade merely because a new one arrives.
3. **Layer add:** Allow **one explicit provider-requested layer** per active root signal group, only if all three original positions can be identified and reconciled, TP1 has not filled yet, and the layer's broker fill is still within an allowed fresh entry zone. Propose layer volume at **50% of the root group's original target volume**, divided among three additional TP legs if broker minimums allow. **GBP 75 covers original positions + layers together**, and the GBP 150 portfolio ceiling still applies. If no remaining budget or a three-leg minimum cannot fit, skip the layer.
4. **No Friday layers (proposed):** Keep Friday exposure simple at 0.01 x 3 per signal. Provider-requested Friday layers are tracked and reported but don't produce extra MT5 positions.
5. **Re-entry:** Allow **one explicit re-entry per root signal** after its previous three legs are entirely closed and reconciled. Do not infer re-entry simply from a price revisit, duplicate Telegram message or a closed stop. Require a fresh attributable provider re-entry command, valid current entry/SL and stop-risk checks. Treat it as a new three-leg execution with a unique child ID and full admission checks; the original signal's prior executed orders are not duplicated. For budgeting, it remains attached to the same root group and does not bypass group/account exposure caps.
6. **Execution failure:** If a layer/re-entry's trio of orders is incomplete, reconcile filled legs, protect each with SL and prevent duplicate retries. Alert if unable to complete or unwind to a safe agreed state. Never open an opposite-direction order as a substitute for closing a failed leg.

**Outstanding approvals:** maximum three concurrent groups, one half-size layer per group, banning Friday layers, and one re-entry only after prior positions fully close. No live/demo order-enabling change is authorised by these proposed rules.

## 5. Risk constraints requiring further discussion

- Daily equity drawdown threshold: previously **proposed at 10%**, not yet explicitly confirmed in this ruleset.
- Aggregate XAUUSD **planned stop-loss exposure of GBP 150 for the GBP 1,000 demo account** is agreed; formula for scaling this cap at other balances has not been decided.
- Maximum simultaneous signal groups, provider priority, the permitted size/count of layers and re-entries: **proposals in section 4A, not yet agreed**.
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

## 6A. Discord operational dashboard and alerts (confirmed requirement; NOT YET IMPLEMENTED)

The existing Discord bot and Telegram/paper dashboards remain operational, but they do **not** yet report authoritative MT5 broker execution for Account 1. Add a dedicated **Account 1 MT5 DEMO** dashboard and separate issue/alert feed. Display explicit DEMO and account identifier labels; never conflate the paper engine, provider-reported signal outcomes, or other MT5 accounts with Account 1 broker results.

### Permanent account dashboard

- Show broker-confirmed **account balance, equity, free margin, margin used, floating GBP P&L**, and last refreshed time. Use actual MT5 account-currency values (GBP for the current GBP 1,000 demo account). Display a clearly labelled disconnected / stale state if MT5 snapshots expire, not old numbers as if they are live.
- Show current **open XAUUSD signal groups**, originating provider, BUY/SELL side, actual average/leg fill prices, active volume, actual native SL and TP1/TP2/TP3, current broker quote, native-leg TP progress, unrealised **pips for each leg**, and floating GBP P&L. Show each root group once with its separately identifiable legs and any extra layers/re-entries.
- Show **realised pips and realised net GBP P&L today and this week**, plus win/loss counts and optional monthly figures. This week's boundaries are Monday 00:00 (Europe/Isle_of_Man) through now, with broker deal timestamps converted to that timezone. Broker-deal history, not the provider's Telegram TP/SL announcements, is authoritative.
- Show current planned stop-risk used **in GBP against GBP 150** and individual groups against **GBP 75**. Unreconciled broker positions and missing SL are marked explicitly as risk uncertainty and trigger alerts; never report a falsely precise risk figure.
- Update the existing pinned/permanent Discord message approximately every 30-60 seconds (plus on significant broker events) rather than posting a new message every minute. Discord updates must be rate-limited and resilient to missing permissions, downtime and restarts. Preserve separate existing Telegram and paper dashboards.

### Precise pip reporting

- Display the **configured XAUUSD pip convention** alongside reported pips; existing paper engine default is price increment **0.01 USD per pip**, but the broker/provider convention must be verified before adopting that setting for MT5 account reporting. Contract point size and pip size are not necessarily identical. Do not silently use one provider's pip convention for the other.
- BUY leg pips = (broker exit or current bid minus actual broker entry) / configured USD-per-pip. SELL leg pips = (actual broker entry minus broker exit or current ask) / configured USD-per-pip. Broker price ticks and actual fills control values, not guessed TP distances.
- Avoid **three-leg double-counting** in the account's weekly pip headline. For each execution trio, compute realised pip-equivalent as the volume-weighted sum of realised leg pips relative to that trio's total opened lots: sum(closed_lot_volume / trio_initial_total_lots * realised_leg_pips). Add completed and partly closed trios' contributions within the reporting window. If the three full-size legs all gain 100 pips, the trio contributes **100**, not 300 headline pips. Display per-leg pips separately if users want to audit every leg. Count a new explicit layer/re-entry trio separately, with its own reference lot total.
- Currency P&L must be calculated from **MT5 account/deal profit plus swap and commissions/fees**, not by multiplying displayed pips by a generic pip value. Clearly distinguish floating (mark-to-market) from closed/realised trades. Handle entry/exit commissions without double-counting across days.

### Operational alerts and acknowledgements

- Send immediate/high-priority Discord alerts for Account 1 MT5 connection failure, broker account mismatch/non-demo mode, missing/unconfirmed native SL, EA heartbeat gap, gateway or processor unavailability, missed or delayed Telegram ingestion, failed/rejected or partially filled orders, reconciliation mismatches, and risk calculation uncertainty. Entries must remain blocked when required safety checks cannot be satisfied. Existing positions still need risk management and explicit provider exit processing.
- Send warning alerts for rejected entries due to £75 group / £150 account exposure ceilings, stale/invalid signal values, and unfulfillable three-leg lot minimums. Send information messages on **broker-confirmed** order fills, TP1, BE changes, TP2/TP3, provider-directed closes, and recoveries.
- Deduplicate and rate-limit identical alerts, with escalation if a fault persists; record incident start and resolution separately. Alerts must specify source/component, observed time, affected signal/group/ticket where relevant, and whether automated trading is blocked.
- An independent systemd/health watchdog is needed to alert when the **Discord bot itself** is down, since a stopped Discord bot cannot send its own notifications. Discord is observability, not a dependency for safety-critical stops or broker order management.
- Do not send API tokens, account passwords or Telegram sessions to Discord. Send no order or entry command from Discord until separately authorised and secured.

### MT5 telemetry integration and rollout

- A future **read-only, authenticated, localhost-only telemetry route** must carry broker account properties, open position tickets, native protective orders, broker deal events, execution diagnostics and a heartbeat from Account 1 to a durable snapshot/history store used by Discord. The current `GET /v1/demo/feed` provides parsed Telegram instructions only; it cannot supply true MT5 balances, fill prices or P&L. The currently configured gateway is not enabled and must not be misrepresented as operational.
- Implement and acceptance-test broker telemetry separately before publishing any performance figures as actual account results. For missing data display **Unavailable / Awaiting MT5 telemetry**, not £0 or synthetic performance. Before enabling entries, verify a real demo-account sample shows correct amounts and pip convention, WTD rollover, native TP/SL and disconnect alarms.
- Suggested Discord channel layout: `#dkcapital-dashboard` for the pinned view and `#dkcapital-alerts` for exceptions/trade notifications. Proposed slash commands: `/mt5_status`, `/mt5_trades`, `/mt5_week`, and `/mt5_health`. Naming/permissions can be agreed at implementation.

## 7. Additional acceptance tests for aggregate risk and expansion (V2)

11. On a GBP 1,000 hedging demo account, two root groups each with GBP 75 verified risk may coexist. A third GBP 75 group is skipped until enough confirmed protective-stop budget is released.
12. With GBP 110 already reserved, a proposed GBP 75 group can use at most GBP 40 additional planned risk, provided all three broker minimum lots fit; otherwise skip.
13. Opposite-direction BUY/SELL groups from different Telegram providers are counted gross; no hedge netting against the GBP 150 aggregate limit.
14. An explicit layering attempt cannot create a fresh GBP 75 allowance in the existing root group. Account and group allowances are both checked before all three proposed layer orders.
15. After TP1 fills and surviving stops are actually modified on the broker, recalculate remaining group risk and only then release confirmed capacity. Unconfirmed stop modifications release no capacity.
16. A reconnect, partially accepted three-leg order, manual position edit, or exchange uncertainty causes risk reservations to remain until reconciliation, rather than duplicate entries.
17. Re-entries cannot be inferred from normal signal status updates; explicit provider instruction and persistent child ID are required. Friday layer suppression and concurrency count need user approval before being considered executable rules.
