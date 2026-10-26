# DK Capital V2.11 fleet commissioning: Account 1, Kyle, Dan

**Purpose:** Prepare and deploy three independently broker-bound MT5 EAs. There are three separate trading terminals, not one position ledger. **Merging GitHub changes is not installing an EX5, attaching an EA, arming trading, or proving live readiness.** Always inspect and reconcile open positions before changing any attached EA.

## Current footprint

| MT5 | Linux identity | VNC port | EA source | Gateway | Status before this change |
| --- | --- | --- | --- | --- | --- |
| Account 1 GBP DEMO | dkmt5 | 5901 | mt5/DKcapitalV2.mq5 -> unique V2.11 demo EX5 | /v5/demo/feed & /v2/demo/telemetry | Existing, validated demo login 5057220041 |
| Kyle, Account 2 | dkmt5b | 5902 | generate_mt5_v211_profile.py --account-id 2 | /v5/accounts/2/feed & /v5/accounts/2/telemetry | Actual broker login/mode/currency/server not verified from this repository |
| Dan, Account 3 | dkmt5c | 5904 | generate_mt5_v211_profile.py --account-id 3 | /v5/accounts/3/feed & /v5/accounts/3/telemetry | Actual broker login/mode/currency/server not verified; account may not yet be provisioned |
| Isolated compiler | compiler | 5903 | never a trading terminal | none | Separate VNC MetaEditor |

**No copy of account 1's demo EA is allowed on Kyle or Dan.** Each generated EA enforces its distinct broker login, actual REAL or DEMO mode, account currency, exact broker server, and exact private feed and telemetry paths.

## Shared strategy contract

- Standard XAUUSD signal: up to **4% planned stop-loss risk**. Only an explicitly provider-labelled HIGH RISK signal uses **2%**.
- All 1-6 actual numeric TPs get separate broker-native hedged positions. Total lots are allocated among targets, never multiplied by the number of TPs. If broker volume minima, entry proximity, margin or stop risk cannot support every actual TP, do **not** substitute one large TP1 order.
- Each native position must have the actual broker-valid provider SL and a matching TP. TP1-confirmed BE and explicitly attributable provider updates are preserved. A provider SL widening is refused if it exceeds the permitted signal/group/portfolio stop-risk ceiling; report that divergence.
- Account-based lots use the approved per-account-currency denominator. The existing "0.01 lots per complete GBP 100" must NOT be silently applied to a USD REAL account. Choose the exact dollar-equivalent policy explicitly, validate current instrument specifications, and pass it to the source generator.
- For the USD REAL profile, \`ACCOUNT_CREDIT\` is excluded from the conservative balance/equity stop-risk basis. Bonus margin/free margin is not treated as deposited capital. Use broker \`OrderCalcProfit\` in actual deposit currency, plus stop-slip padding, for risk sizing. Planned risk ceilings cannot guarantee realised loss limits during gaps.
- Default \`EnableNewEntries=false\` for each EA. Optional layer, STORM conditional TP1 and open-runner remain disarmed pending independent acceptance.

## Step A: non-invasive fleet inventory

From the VPS, after GitHub PR merge and a reviewed \`git pull --ff-only origin main\`, run:

\`\`\`bash
cd /opt/dkcapital
bash scripts/mt5_v211_fleet_preflight.sh
\`\`\`

This script reads systemd, account installation directories and existing broker telemetry only. It never starts/stops terminals or accesses credentials. If Account 2 or 3 is missing or no authenticated snapshot exists, **do not claim it is trading ready.**

## Step B: configure independent broker identities

Verify inside each broker terminal's account details, NOT from an assumed name, each exact login, REAL/DEMO mode, currency, broker server and whether hedging is enabled. Account 1 remains login \`5057220041\` GBP DEMO. For Kyle and Dan, set \`MT5_ACCOUNT2_*\` and \`MT5_ACCOUNT3_*\` in the private VPS \`.env\` **only for independently verified, commissioned profiles**, including distinct bearer tokens at least 24 random characters each. Keep uncommissioned profiles entirely blank. Never add tokens, MT5 passwords or \`.set\` files to GitHub.

The MT5 gateway rejects an unconfigured account, wrong token, broker login/mode/currency/server and duplicate broker logins. It persists separate account 2/3 telemetry snapshots in \`/app/data/mt5-account2.json\` and \`mt5-account3.json\`. An Account 2 token cannot query Dan's feed and vice versa. These routes share Telegram signal *inputs*, not broker positions, stops, risk or accounting.

## Step C: source staging and compiling

Account 1:
\`\`\`bash
cd /opt/dkcapital
bash scripts/release_mt5_v211.sh status
bash scripts/release_mt5_v211.sh stage-compiler
\`\`\`

In isolated MetaEditor on VNC 5903, compile the **exact** unique Account 1 candidate and require 0 errors and 0 warnings. Then use \`verify-compiler\` and \`stage-demo\` only as directed by the reviewed release script, confirming the authenticated broker-flat gate for any backend rollout. Do not overwrite an older EX5 or remove an attached EA while a group remains open.

Kyle and Dan: **do not generate source until actual identity and lot denominator are verified**. These example commands are templates; replace angle-bracketed values locally without adding them to GitHub:

\`\`\`bash
cd /opt/dkcapital
python3 scripts/generate_mt5_v211_profile.py --account-id 2 \
  --login <KYLE_BROKER_LOGIN> --mode <DEMO_OR_REAL> \
  --currency <GBP_OR_USD> --server '<EXACT_KYLE_BROKER_SERVER>' \
  --lot-denominator <APPROVED_ACCOUNT_CURRENCY_AMOUNT> \
  --output-dir '/opt/dkcapital-mt5-compiler/workspace/MetaTrader 5/MQL5/Experts'

python3 scripts/generate_mt5_v211_profile.py --account-id 3 \
  --login <DAN_BROKER_LOGIN> --mode <DEMO_OR_REAL> \
  --currency <GBP_OR_USD> --server '<EXACT_DAN_BROKER_SERVER>' \
  --lot-denominator <APPROVED_ACCOUNT_CURRENCY_AMOUNT> \
  --output-dir '/opt/dkcapital-mt5-compiler/workspace/MetaTrader 5/MQL5/Experts'
\`\`\`

The same isolated MetaEditor VNC 5903 must compile EACH distinct \`.mq5\` source, producing the matching \`.ex5\`, with **0 errors, 0 warnings**. Manual visual verification and broker execution tests cannot be replaced by Python CI or file hashes.

To stage the reviewed compiled EX5 without touching MT5 processes:
\`\`\`bash
METAEDITOR_ERRORS=0 METAEDITOR_WARNINGS=0 \
  bash scripts/stage_mt5_v211_profile.sh 2
METAEDITOR_ERRORS=0 METAEDITOR_WARNINGS=0 \
  bash scripts/stage_mt5_v211_profile.sh 3
\`\`\`

The staging script requires root, the correct existing installation and approved compiler results, refuses symlinks/overwrites, checks account-bound source markers and verifies the installed binary matches compiler output. It does **not** attach the EA, alter broker orders or enable new trading.

## Step D: controlled backend rollout, individually

Once the current gateway is healthy, preserve/back up the active Telegram journal and broker telemetry; deploy only \`signal-processor\` if new parser code is not already deployed, and \`mt5-gateway\` to pick up the account-specific routes. Do not take down \`telegram-listener\`, \`discord-bot\` or the MT5 terminals. Confirm legacy Account 1 authenticated \`DKDEMO5\` remains unchanged. Then separately validate Account 2 \`DKACCOUNT2V5\` and Account 3 \`DKACCOUNT3V5\` with their private credentials, no real order requests, and each own authenticated POST telemetry.

**Never enable a profile solely because HTTP 200 is returned.** Verify broker-login proof from the attached EA, instrument tick/contract/min volume, broker-native TP/SL acceptance, per-target fills, fresh quote and spread gates, high risk 2% vs normal 4%, duplicate replay after restart, provider SL edits and broker history.

## Step E: attach only in controlled flat terminal

For each **individual** account, visually confirm correct login/mode/server/currency on its VNC screen, no open trades or pending orders that would be affected, and no other EA managing that broker account's signals. Attach only the correctly named EX5 and verify native settings, feed URL, telemetry heartbeat, \`entry_enabled=false\`, own-account broker position/order counts and actual broker test fills. Do not bulk restart units, delete Wine folders, reset signal journals or copy Account 1's login/session state.

**Live account restriction:** if Kyle or Dan is REAL, first test its source in a segregated broker environment with minimal risk, and only then authorize automatic entries on that exact funded account. Real-money execution is not approved by this repository release alone. The existing watchdog and automatic recovery opt-in policies must be separately verified for each account. This release does not enable live auto-recovery.

## Evidence before signoff

Require per-profile report of: exact compiled EX5 SHA256, MetaEditor compilation result, broker mode/server/login/currency, disarmed startup, authenticated feed status, all six TP test allocations, 2% labelled-high-risk trade, 4% standard risk trade, broker rejection recovery, native SL/TP for every leg and no other affected account. **Discord final P&L cards remain Account 1-centric until a separately tested account-specific reporting integration is completed; do not attribute those results to Kyle/Dan.**
