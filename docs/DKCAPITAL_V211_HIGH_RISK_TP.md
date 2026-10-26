# DK Capital V2.11 high-risk and native target acceptance

**Scope:** Account 1 GBP DEMO login 5057220041 only. Account 2 and Account 3, and any USD REAL / Vantage account, must not receive this EA. Nothing in merging this PR attaches an EA or enables entries.

## Why this release

- Existing V2.10 source already allocates the total permitted lots equally among **all actual numeric provider take-profit targets**, 1 to 6. If 5 are received, 5 broker-native TP positions must fill, or the new group is rejected and incomplete fills reconciled/unwound.
- Broker alerts showing only a large TP1 position suggest an outdated EA/feed, missing parsed targets or an entry/reconciliation failure. The actual VPS version, native positions, per-signal parsed targets and Experts logs must be compared. Do not assert root cause from Discord result summaries alone.
- Explicit affirmative "HIGH RISK", "HIGH-RISK", or "RISK: HIGH" on the **original provider signal** sets high_risk=true. Generic discussion and "NOT HIGH RISK" do not. The flag is preserved on signal amendments and event-state rebuilds.
- An explicitly high-risk entry and any optional layer share the maximum 2% **planned stop-loss** exposure cap; normal signals keep the 4% maximum. Both caps use the lower of account balance/equity, broker-derived contract valuation, all positions, conservative stop slippage and padding. Group/portfolio limits still apply.
- SL widening is applied only if revised planned stop exposure remains within all relevant risk caps; otherwise existing native stop stays in place and a visible decision/alert is recorded. Breakeven and stop tightening continue.
- A 2% planned risk cap is not a guaranteed maximum realised loss during slippage or a gap. Provider labels arriving only after a position has already opened do not retroactively resize it.

## Protocol migration

V2.11 uses **/v5/demo/feed**, **DKDEMO5**, exactly 16 fields for signal rows. The penultimate field is HIGH or STANDARD; final field remains Unix opening time. A V2.11 EA rejects unknown flags and a non-DKDEMO5 header.

The older /v1, /v2, /v3 and /v4 feeds mark flagged HIGH RISK signals CANCELLED for entry (while retaining their action/SL management rows). This prevents old EAs that lack 2% sizing from executing high-risk entries at 4%. Other signals continue to be presented normally. This does not retrofit already running positions or older binaries.

## Deployment after passing GitHub checks

1. Ensure the merged GitHub code is on the VPS and collect non-secret evidence of the **currently attached EA version** and feed URL, parsed TP list for affected Money House signals, native MT5 position tickets, Experts entry decisions, and any broker rejection codes. Do not disable an existing EA on a live broker without appropriate controlled changeover.
2. On a **flat** and authenticated GBP DEMO Account 1, use the audited helper: \`bash scripts/release_mt5_v211.sh status\`, then \`stage-compiler\`. Compile only in isolated MetaEditor on **VNC 5903**, under filename \`DKcapitalV211_TP1to6_HighRisk.mq5\`, requiring **0 errors, 0 warnings**.
3. Run \`verify-compiler\`, verify checksums, and stage only the compiled binary with \`stage-demo\`. Inspect the broker-flat eligibility and approved Account 1 EA attachment process. Do not attach to any REAL account.
4. Deploy backend with \`deploy-backend\` when authenticated fresh telemetry, account identity, broker-flat position/order state and protections meet the script's preflight gates. Verify \`check-backend\` returns authenticated DKDEMO5. Never spoof telemetry or overwrite the Telegram event journal with stale backups.
5. Attach V2.11 only after detaching the older GBP DEMO EA in an approved **flat** changeover. Set FeedUrl to \`http://127.0.0.1:8765/v5/demo/feed\`, MaxSignalRiskPct=4.0, HighRiskSignalPct=2.0; keep \`EnableNewEntries=false\` until isolated demo tests pass. Layers, STORM conditional TP1 and open-runner remain disabled unless separately approved.
6. Controlled **synthetic test fixture only**: 1 through 6 native targets, exact total-volume preservation, small-lot rounding, missing target fail-closed, broker rejection/partial unwind, high-risk 2%, normal 4%, legacy feed CANCELLED behavior, provider SL tightening and excessive widening refusal, TP1 -> surviving-leg BE, no duplicate executions after restart, and broker reconciliation against native closed deals.
7. Compare the acceptance output to original Telegram alerts, the signal-state snapshot, feed rows, Experts logs and actual broker positions. Only then consider manually arming new DEMO entries.

**Not approved for live USD Vantage.** This script never compiles on a running MT5 trading terminal and cannot perform the required MetaEditor UI compilation, EA attach/detach, broker tests or trading approval.
