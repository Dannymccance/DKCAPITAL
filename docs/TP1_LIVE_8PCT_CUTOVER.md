# DK Capital production TP1 strategy: STORM + Capital

Status: **source candidate, not compiled and not deployed.** The Dan REAL USD
broker terminal (internal Account 3) must retain its identity and trade history.
There is no requirement for a 1% demo or forward-trading period.

## Configuration

Select `MT5_DAN_ONLY=true` and `MT5_STRATEGY_MODE=tp1_live` in the **private VPS**
environment only when replacing the existing Account 3 EA. The generator
`scripts/generate_mt5_tp1_live.py` creates an independent 2.12 source file for
the isolated compiler and requires the verified REAL USD login, Vantage server
and 64-character feed token from the existing private .env.

Inputs in the generated EA (not a simulator):

| MQL input | Default | Behavior |
| --- | --- | --- |
| `MaxSignalRiskPct` | **8.0** | Broker-valued maximum initial SL loss per signal |
| `MaxPortfolioRiskPct` | **8.0** | Configurable aggregate percentage ceiling |
| `HighRiskSignalPct` | **8.0** | Same per-signal ceiling in TP1 mode |
| `MaxGroupRiskAccountCurrency` | **80.0** | Retained effective absolute-group ceiling at $1,000 reference |
| `MaxPortfolioRiskAccountCurrency` | **80.0** | Retained effective aggregate ceiling at $1,000 reference |
| `RiskReferenceBalanceAccountCurrency` | **1000.0** | Existing scaled-cap reference |
| `EntryToleranceUSD` | **1.0** | Quoted-price deviation guard, NOT an entry-zone wait |
| `MaxSpreadUSD` | **1.5** | Existing spread safety guard |
| `MaximumSignalAgeSeconds` | **180** | Maximum age *since first complete executable instruction*, no three-minute waiting |
| `EnableNewEntries` | **false** | Explicit broker-side activation only after release |
| `EnableLayerEntries` | **false** | TP1 code disables layering regardless |

The previous live template used $75 of group cap per $1,000 reference balance
(7.5%), so it could NOT size a full 8% single position even when no other
trades were open. The previous $150 portfolio input scaled to 15%, which by
itself would not enforce the new 8% aggregate ceiling. The TP1 candidate
explicitly changes both input defaults to **$80 per $1,000 reference balance**
while keeping both existing limit checks active. This is a proposed source
configuration, not an adjustment to any running EA.

These retained absolute caps remain **active** in addition to 8% percentage
limits. At the default values both ceilings equal 8% of risk capital. Operators
changing the input caps must reconcile both, and the smaller limit prevails.
Risk capital is the lower of balance and equity less broker credit. Conservative
stop padding is included. The requested 8% is a stop-risk ceiling, not margin
allocation or a guaranteed worst loss; slippage, gaps and commissions can
increase realised losses. Friday size restrictions remain and may prevent
the full 8% size.

The only approved Telegram **origin** IDs for new entries are:

- GWS STORM: `-1004334637102`
- GWS Capital: `-1004406990046`

Forwarded messages with a different or hidden origin are rejected. The parser
retains original message IDs, per-message edits, reply-linked management,
historical records, and other provider integrations. Non-NOW / LIMIT signals
are not converted to market instructions. The new entry gateway cancels only
new-entry rows; it retains S management rows and provider A close actions.

Entry window: **00:00:00–14:59:59 Europe/Isle_of_Man** (GMT/BST). At 15:00
no new entries, but broker SL/TP, provider closes and allowed tightening
continue. The gateway and EA enforce the trading window separately.

A qualified NOW enters at current broker ask/bid with the original provider
SL and TP1 attached in **one position**, sized with `OrderCalcProfit`,
`OrderCalcMargin`, broker minimum lot and increments. Existing entry
deduplication journals, native tickets and broker response verification remain.
A provider SL amendment cannot widen beyond group, signal or portfolio caps.
No automatic layering or TP1-to-breakeven.

**Missing initial SL**: The current approved live V2.11 EA has no configured,
audited temporary-stop distance. This candidate therefore **does not invent
an SL or open without one**. The signal remains unexecuted until a valid
provider SL arrives. A separate approved temporary-stop policy would be needed
to cover a missing-SL-first-entry execution without guessing risk.

**Risk of old open positions**: The user has six pre-existing SELL positions
with native TP/SL and has asked that their broker orders be left untouched.
The two unmanaged positions remain a new-entry blocker until reconciled.
Do not detach or replace an EA managing those positions. Disabling Algo Trading
preserves broker-side exits, but the EA cannot amend/close them automatically
until enabled. No service process, .env value or broker order is changed by
this GitHub branch.

## Compilation and cutover (after successful CI and controlled approval)

1. Verify REAL USD Account 3 login, Vantage server, broker credit, all open
   positions and pending orders in MT5. Record legacy EA hash and backups.
2. Pull the merged commit by `git pull --ff-only origin main`.
3. Generate the private EA in the **isolated MetaEditor staging directory**
   (do not use a tracked source directory):
   ```bash
   python3 scripts/generate_mt5_tp1_live.py \
     --account-id 3 --login "$VERIFIED_DAN_LOGIN" \
     --mode REAL --currency USD --server "$VERIFIED_DAN_SERVER" \
     --lot-denominator "$APPROVED_LOT_DENOMINATOR" \
     --feed-token-env-file /opt/dkcapital/.env \
     --output-dir "$ISOLATED_COMPILER_EXPERTS_DIR"
   ```
   The variables must be independently verified locally. Never publish
   a private MQ5/EX5 containing the embedded feed token.
4. Compile with actual MetaEditor, inspect errors and warnings, record source
   and EX5 SHA-256. GitHub Python CI **does not compile MQL5**.
5. First reconcile any surviving legacy positions: retain their original EA
   or wait for native TP/SL exits, and check native protection and ownership.
   Do not run two EAs that can operate the same orders concurrently.
6. Switch the private `MT5_STRATEGY_MODE=tp1_live` and recreate **only**
   `mt5-gateway`, `dkcapital-watchdog`, `discord-bot` with
   `docker compose --profile mt5-shadow --profile discord up -d --no-deps
   --build mt5-gateway dkcapital-watchdog discord-bot`. Do not stop Telegram
   listener, signal-processor, journal, Account 3 terminal, or old trades.
7. With the new compiled EA installed and entries disabled, verify fresh
   Account 3 `TP1_V1` telemetry, correct dollar 8% cap, exact broker identity,
   management instructions, feed authentication, entry diagnostics, and
   withdrawn broker credit excluded from sizing.
8. Activate `EnableNewEntries` **only via explicit broker-side operator
   authorisation**, then verify a genuine broker-native fill and Discord
   acknowledgement before claiming live operation.

## Rollback

Stop new submissions by disabling Algo Trading in MT5, retaining existing
broker-native SL and TP; reconcile any open TP1 trades. Set private
`MT5_STRATEGY_MODE=legacy` only when the matching original legacy V2.11
Account 3 EA, `UK_V2` telemetry and management are restored. Recreate the
gateway, watchdog and Discord services only, verify authenticated feed and
original broker history, and leave new entries disabled until operator
sign-off. Never restart retired Account 1/2 EAs to recover a dashboard.
