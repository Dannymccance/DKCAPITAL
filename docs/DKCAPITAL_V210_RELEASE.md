# DK Capital V2.10 controlled demo rollout

This procedure upgrades **Account 1 GBP DEMO only**. Account 2 (Kyle) and
Account 3 (Dan) remain untouched. VNC 5903 is the compiler; VNC 5901 is the
existing MT5 demo broker account.

## Target policy — 1 to 6 native take-profits

The latest instruction supersedes the previous TP2-largest policy. The
**total permitted position volume is split equally across the number of actual
numeric targets**. It is never multiplied by the number of TPs.

| Provider targets | Intended share | Broker legs |
| --- | --- | --- |
| 1 | 100% at TP1 | 1 |
| 2 | 50% each | 2 |
| 3 | 33.33% each | 3 |
| 4 | 25% each | 4 |
| 5 | 20% each | 5 |
| 6 | 16.67% each | 6 |

Every leg has the actual provider's SL and its own numeric native TP.
Real broker minimum lots and volume step may require one-step remainders:
for example, with 0.01 volume steps and 0.12 total lots, six exits
have 0.02 each, while five exits have 0.03/0.03/0.02/0.02/0.02.
The total never exceeds the calculated risk/margin/balance cap.
If the maximum affordable lot size cannot fund one minimum-volume
position per TP, the EA rejects the **whole new trade**, never omits a TP.

Native magic suffixes: 1, 2, 3, 5, 6, 7 for TP1 through TP6.
Suffix 4 remains the existing optional GWS layer, with its own limits.
The previous historical two-target three-leg entries and four-target
suffix5 entries remain recognizable to the EA through broker history and
their persisted entry flags.

The existing explicit TP3 OPEN runner is a separately guarded opt-in,
not a numeric six-target format. The prior 2% daily loss **warning-only**
policy, group/portfolio risk limits, 4% signal risk at actual provider SL,
Friday maximum total lots, entry freshness, broker SL/TP and recovery
checks remain in force.

The new authenticated endpoint is `/v4/demo/feed` with `DKDEMO4`.
Prior `/v2/demo/feed` and `/v3/demo/feed` stay available for older
attached EAs; signals beyond each version's capacity are marked
non-executable on that older feed so no trade silently loses a target.

More than six numeric targets and gaps in TP numbering are not supported
and are skipped. Do not invent or collapse targets.

## 1. Pull merged GitHub code and stage source in compiler

```bash
cd /opt/dkcapital
git pull --ff-only origin main
python3 -m unittest discover -s tests -p 'test_mt5_v210*.py' -v
bash scripts/release_mt5_v210.sh status
bash scripts/release_mt5_v210.sh stage-compiler
```

Inside **isolated MetaEditor on VNC 5903**, open:

`Z:\workspace\MetaTrader 5\MQL5\Experts\DKcapitalV210_TP1to6_SignFix1.mq5`

Press F7 and require **zero compiler errors and zero warnings**. Never
compile on live Account 1. Then run:

```bash
bash scripts/release_mt5_v210.sh verify-compiler
```

This checks source parity, compilation timestamp, binary size and EX5 digest;
the operator must still visually verify the MetaEditor compiler result.
The reviewed source/binary is only staged in the compiler at this point.

## 2. Disarm existing V2.07 before touching backend

In VNC **5901** open old EA F7 Inputs and set `EnableNewEntries=false`.
Leave the EA attached and existing broker-native stops intact. Verify no
open positions or pending broker orders. Wait for authenticated Account 1
telemetry with `entry_enabled=false`, HTTP 200 and age under 120 seconds.

The legacy signal processor reconstructs Telegram state at startup. A
second safety gate after Docker image build exists to prevent retroactive
reprocessing while the EA is armed. On a market-closed weekend, telemetry
may remain stale; **do not bypass the release gate or fabricate snapshots**.

## 3. Back up state and update only backend processor/gateway

```bash
cd /opt/dkcapital
bash scripts/release_mt5_v210.sh deploy-backend
bash scripts/release_mt5_v210.sh check-backend
```

The script backs up Telegram events, signal state and Account 1 broker
telemetry inside the shared Docker volume at `/app/data/backups/pre-v210-*`.
It rebuilds/recreates **only signal-processor and mt5-gateway**. It does
not restart Telegram listener, Discord, legacy Python paper-engine, VNC,
or either MT5 terminal.

The new authenticated endpoint `/v4/demo/feed` must return
`DKDEMO4`. The old `/v2/demo/feed` continues for legacy V2.07 but marks
four-target ACTIVE signals as non-executable, avoiding silently omitted TP4
while old EAs are still running.

## 4. Stage compiler-verified EX5 alongside the existing demo EA

After inspecting zero errors/warnings in isolated MetaEditor:

```bash
cd /opt/dkcapital
METAEDITOR_ERRORS=0 METAEDITOR_WARNINGS=0 \
  bash scripts/release_mt5_v210.sh stage-demo
```

The script copies the new EX5 **alongside** the original. It prints both
hashes, and it **never attaches, enables, disarms or restarts MT5**. The
candidate name on Account 1 is `DKcapitalV210_TP1to6_SignFix1`.

## 5. Human-controlled 5901 EA changeover

Confirm Account 1 is disarmed and broker-flat. Save a local input `.set`
file from the current V2.07 EA (it includes a private gateway token; never
paste, upload or send it). Detach the old EA *before* attaching the new one.

Refresh the Navigator, attach the candidate with all new entries OFF and
verify these **locally**:

- Demo broker login 5057220041, currency GBP, mode DEMO, symbol XAUUSD.
- `FeedUrl=http://127.0.0.1:8765/v4/demo/feed`
- `TelemetryUrl=http://127.0.0.1:8765/v2/demo/telemetry`
- Existing private FeedToken restored from the local settings.
- `EnableNewEntries=false`, `EnableLayerEntries=false`,
  `EnableOpenRunnerEntries=false`, `EnableStormConditionalTP1=false`
  during validation.
- Original MaxSignalRiskPct=4.0, original provider SL, 2% warning-only
  daily loss, fixed group/portfolio limits.
- Experts log reports `version=2.09`, fresh telemetry, HTTP 200 feed,
  account-wide positions/orders and correct native stop protection.

## 6. Controlled broker-demo acceptance testing

Test synthetic one/two/three/four-target messages **only in a controlled
non-production signal fixture**, never by injecting an old Telegram message
with a new timestamp into the active processor:

- Native stop and take profit for every leg; no made-up TP4.
- Two target exits use two TP2 broker tickets.
- Three/four-target allocations actually place TP2 at the largest volume.
- Magic suffix 5 for TP4 is distinct from optional layer suffix 4.
- TP1 broker-confirmed fill, subsequent BE/proposed provider deferral.
- Closed trades use actual MT5 deals and PnL, not Telegram TP claims.
- Group planned stop risk stays within 4%; invalid stops, wide spreads,
  insufficient lot steps, stale instructions and poor margin are skipped.
- Existing positions, broker stops, and executed-trade deduplication survive
  controlled restart; Discord alerts and recovery report correct identity.

Only after broker tests pass should Account 1 be approved for new entries.
V2.10 is **GBP DEMO only** and MUST NOT be attached to Dan's future USD REAL
Vantage account; that requires a distinct, broker-tested live EA.

## Rollback and constraints

Code merge or candidate staging does not modify the running EA. If candidate
was attached, leave new entries disabled, reconcile open broker exposure
and native SL/TP, and restore the previous EA/settings only during an
approved flat/controlled changeover. Backed-up Telegram events/state must
**not** be overwritten over active listener updates or risk historical replay.

The release script intentionally **cannot** execute the VNC GUI compile,
detach/attach chart EAs, certify native broker fills, or approve live trading.

## MQL5 compiler warning fix, 25 October 2026

The initial V2.10 candidate compiled with **0 errors, 1 warning**: `sign mismatch` at `Owned()`, line 197. The expression compared an unsigned broker `magic` suffix to signed integer `TP6_LEG`. The suffix and range limit are now cast to `ulong` without changing the set of owned broker magic numbers. The previously compiled `DKcapitalV210_TP1to6_Candidate.ex5` is **not approved**. The corrected release uses a new, distinct compiler filename `DKcapitalV210_TP1to6_SignFix1.mq5` (and corresponding EX5). Do not overwrite or install the old compiler candidate. After pulling the approved commit, run `stage-compiler`, compile **the new exact filename** on VNC 5903 and visually verify **0 errors and 0 warnings**; then `verify-compiler`. Account 1 remains unchanged throughout staging.
