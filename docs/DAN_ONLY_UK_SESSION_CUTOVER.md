# DK Capital: Dan-only live MT5 cutover and UK session risk

**Status: source change only. NOT deployed or compiled. Not a live trading approval.**

## New target topology

- Keep the Telegram listener, signal processor, authenticated MT5 gateway and Discord alerting.
- Keep **only Dan's broker terminal, Account 3**, on `dkcapital-mt5-3.service`, VNC `:152`, and its own broker-specific source/EX5. Retire Account 1 GBP demo and Kyle Account 2 after confirming both are flat. Do not rename Dan as Account 1 or copy another MT5 Wine prefix: login-bound magic state, historical tickets, tokens, telemetry and paths must remain isolated.
- The isolated MetaEditor/compiler is not a trading account. Historical demo/Kyle exports remain archived and are not to be deleted or added to Dan's performance results.
- `MT5_DAN_ONLY=true` activates a runtime cutover: the gateway accepts only the verified REAL Account 3 profile, returns HTTP **410** for all Account 1/demo and Account 2 feed/telemetry routes, and the shared monitoring registry selects only Account 3. Without the flag, the old code paths remain for rollback until both obsolete terminals are retired.
- Set the **existing** Dan Account 3 login, server, mode=REAL, currency=USD, token in the private VPS `.env`, never GitHub. An absent/mismatched identity prevents gateway startup or EA arming.

## Dan's planned broker stop risk (not leverage or margin)

- UK local wall time: **01:00 inclusive through 08:59:59 = 8%**, and **09:00 through 00:59:59 = 2%**, using `TimeGMT()` plus British Summer Time rules (last Sundays of March and October, 01:00 UTC). Both repeated 01:xx clocks at autumn fallback fall in the 8% window. Use the order-attempt time, not the Telegram posting time.
- These percentages are **ceilings**, not guaranteed actual risk or profitability. They apply to the **total open XAUUSD broker-native stop loss risk** AND each new standard signal's initial entries/layers. Broker stop risk is computed by `OrderCalcProfit` with stop slippage padding. It is NOT simply lots or leveraged notional.
- Risk capital is the lesser of balance and (equity minus broker bonus credit). For example, with deposited risk capital $2,500 and no open exposure, theoretical limits are $200 in the overnight window and $50 at other hours, before padding, spread, TP volume minimums and available margin.
- Provider-tagged **HIGH RISK** signals continue to have an additional conservative **2% per-signal** ceiling in either session.
- Dan's total lot request is now computed from *remaining stop-loss risk budget*, using minimum broker lot to infer loss per lot and the number of numeric TP legs to reserve fixed padding. Existing limit checks then reduce it further for minimum volume, margin, Friday size restriction, slippage, spread, direction, provider risk gates and available position slots. There is no fixed balance-to-lot ceiling on Dan's candidate.
- Initial entries are permitted to use up to 100% of the remaining group risk budget, rather than the old 85% group reservation. An optional 10% layer is accepted only if there is room under the current signal/group/portfolio limit. No layer or optional STORM feature is automatically enabled.
- **At 09:00, open earlier trades are not force-closed, and existing native SL/TP, provider actions and TP1-to-BE management continue.** If their current aggregate stop risk exceeds 2%, no new entries or increases in stop risk are permitted until exposure decreases. A falling limit does not retroactively shrink positions.
- A 2% or 8% *planned* stop-loss budget cannot guarantee realised loss limits in a gap, broker outage or slippage; 8% is substantial exposure.
- The EA still starts with `EnableNewEntries=false`; simply merging/restarting a gateway does not enable trading.

## GitHub-first changeover, in order

1. Before stopping either obsolete terminal, **verify from MT5 broker Accounts 1/2** that all positions and pending orders are closed or independently managed. Preserve their history, native trade/ticket exports, logs and snapshots. Never detach an EA while it is solely responsible for open positions. Record which profiles and recovery timers are actually installed:
   ```bash
   systemctl list-units --all --type=service --type=timer 'dkcapital*' --no-pager
   systemctl status dkcapital-mt5-1.service dkcapital-mt5-2.service dkcapital-mt5-3.service --no-pager
   ```
2. After merge and verified `git pull --ff-only origin main`, stage a *private* Dan-only monitor registry at the mapped Docker volume path `mt5-monitor-profiles.json`. Use `deployment/mt5-monitor-dan-only.example.json` as a template but **replace zero login** with the actual authenticated Dan login, `expect_entries` with the actual desired arming state, and ensure `auto_recover=false`. Do not modify the Telegram event journal, MT5 Account 3 Wine prefix or existing open trades.
3. Stop and disable **only** retired Account 1 and 2 MT5 terminal units, together with their VNC and legacy autoheal services/timers, **after Step 1 passes and the trading effects have been reviewed**. Use the actual installed unit names from the inventory; do not run bulk `systemctl stop dkcapital*` or `docker compose down`. Check no obsolete terminal is auto-respawned before enabling the new gateway mode.
4. Set `MT5_DAN_ONLY=true` in the VPS `.env`, and remove/blank `MT5_ACCOUNT1_*`, `MT5_ACCOUNT2_*` and `MT5_DEMO_FEED_ENABLED` only after obsolete units have stopped. Do not remove `MT5_ACCOUNT3_*`. Install the reviewed `deployment/dkcapital-mt5-profile-audit.service` and `deployment/dkcapital-mt5-recovery-supervisor.service` replacements only when the Dan-only registry is present: their units explicitly use `Environment=MT5_DAN_ONLY=true`. Keep live freeze recovery disarmed unless previously separately approved with independent broker reconciliation.
5. Rebuild/redeploy `mt5-gateway` and `dkcapital-watchdog` without taking the shared Telegram event store offline. Validate authenticated Account 3 `/v5/accounts/3/feed`, `/snapshot`, `/quality`, telemetry and monitoring. Check Account 1 and 2 routes refuse requests with HTTP 410. If Account 3 is not verified, revert `MT5_DAN_ONLY` and diagnose, but **do not restart obsolete broker EAs merely to repair gateway availability**.
6. Generate Account 3 REAL USD private candidate using `scripts/generate_mt5_v211_profile.py --account-id 3` and existing independently verified login/server/lot denominator and private feed token. The unchanged file naming deliberately retains the V2.11 profile staging contract; the source within is revised for Dan session risk. Compile in the isolated MetaEditor VNC 5903 and record **0 errors, 0 warnings**, compiled EX5 hash, and exact source. Review the final generated MQL manually, particularly UK date handling, credit exclusion, risk budget and broker symbol valuations. There is no automatic compilation in Python CI.
7. In a **flat or individually reconciled** Dan terminal only, perform a controlled EA replacement. Validate the verified login, Vantage server, REAL USD mode, hedging, feed token, TP 1-6 native positions, broker minimum lots/margin, stop-loss updates, slippage, TP1-to-BE and duplicate-restart protection. Validate synthetic conditions straddling 00:59/01:00 and 08:59/09:00 UK, plus March/October clock changes. Monitor that an open overnight position prevents new daytime risk when total open exposure is already over 2%.
8. Keep new entries disabled pending positive broker-side checks and explicit sign-off on the compiled version. **Do not assume a successful CI or merge proves live trading safety**.

## Repurposing the former demo reporting interface

The former demo **Discord dashboard message and channel** are the new **Live Account** display when `MT5_DAN_ONLY=true`. Account 3 remains the internal broker namespace so existing native tickets, per-signal magic values and deduplication records cannot be misattributed. User-facing cards no longer call it "Dan Account 3".

- The former demo dashboard, `#signals` broker status cards, broker-verified entry confirmations, native TP/SL and provider amendment notifications, final results, weekly record and weekly-average-pips counter all use the **authenticated Account 3 REAL USD snapshot**. If there is no valid live telemetry at cutover, the dashboard displays **unverified**, rather than presenting the last demo account balance.
- The former dashboard message reference `mt5-dashboard-config.json` is intentionally reused to edit the already-pinned embed in place. Do not delete it unless you intend to make a fresh pinned card.
- Financial figures and broker exposure in the renamed output are USD, not GBP. The EA reports its live local-session percentage, its dollar stop-risk limits, and broker credit. The risk limit is the combined planned SL loss; a 2% daytime cap may be below the risk of overnight positions without liquidating those positions.
- Demo and Kyle account telemetry are never read into new live performance records. Separate live state files include `mt5-account3-live-pip-history.json`, `mt5-account3-live-results-published.json`, `mt5-account3-live-broker-events-state.json`, `mt5-account3-live-weekly-record-state.json`, `mt5-account3-live-alert-state.json`, and `mt5-account3-live-entry-confirmation-state.json`. Existing historic demo/Kyle archives remain untouched.
- The old three-account entry-confirmation summary becomes **Live Account only**. Existing original demo result cards in Discord history are *not retroactively deleted or rewritten*, so they should be treated as archived and clearly distinguished from newly published cards.
- Existing live Account 3-only alert-loop delivery is disabled in Dan-only mode to avoid duplicating the repurposed main alert loop. Maintain the new main alert channel's permissions and authenticated watchdog separately. No new Discord channel ID is automatically provisioned.
- The legacy independent `paper-engine` simulator no longer starts in the default Docker Compose profile. Existing running paper-engine containers must be explicitly stopped after reviewing their state: this change of Compose profile alone does not prove old containers stopped. Its historical ledger is retained read-only; never merge those synthetic trades into broker statistics.

### Operational verification after redeployment

1. Confirm the former demo dashboard changes to **DK Capital | MT5 Live Account**, and its login/mode/currency/server match Dan's broker. Deliberately test the missing/stale-telemetry state and verify no GBP demo balance is displayed.
2. Confirm the feed's `signal_risk_cap_pct` and `portfolio_risk_cap_account_currency` are present and switch at 01:00 and 09:00 UK local time. These are broker-reported inputs, not assumed from the UI clock.
3. Confirm the first new broker-native closing deal appears in the USD-only results history, correctly attributed to Account 3, with the original Telegram provider and native TP/SL exit reason. Verify new live pip archive contains only Dan's broker tickets.
4. Confirm normal / HIGH RISK provider scenarios and 1-6 native TP targets on a **controlled disarmed trial**, and run regression checks after compiling. Retiring the demo must not alter Telegram parser input, forward provider SL amendments, or disable the existing same-account opposing-room entry restriction.

## Important known limitations

- Account 1/2 source, archived tests, historical feeds and reporting code are retained behind the opt-in flag for rollback/audit purposes; they are not deployed active trading profiles in Dan-only mode. Removing source files is separate from removing deployed processes.
- The code now reuses the old demo interface for live USD broker records when the Dan-only flag is active. Its old Discord messages are historical and retain their previous demo labelling; no historical backfill into Dan's archive is performed.
- The fixed broker-native `MaxGroupRiskAccountCurrency`/`MaxPortfolioRiskAccountCurrency` input fields remain in the derived source for legacy template compatibility but **do not override Dan's new percentage ceiling**. Do not use those legacy values to estimate Dan's current stop-loss risk.
- This repository cannot observe current VPS processes or compile MQL5. Broker-flat status, Vantage native order acceptance and the current attached EA are unverified until checked at the terminal.
