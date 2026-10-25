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

## 2. Stage the verified binary first (no EA currently attached)

If the GBP DEMO terminal currently has **no EA attached**, a new-entry
toggle cannot be used as a precondition to copying an EA into the filesystem.
The `stage-demo` action is therefore independent of a live gateway
probe and broker telemetry; it **only copies** the compiler-verified
EX5 into the demo terminal's Experts folder. It cannot attach an EA,
send an order, change a chart, or start/stop a service.

After separately confirming **0 errors and 0 warnings** in MetaEditor and
running `verify-compiler`:

```bash
cd /opt/dkcapital
METAEDITOR_ERRORS=0 METAEDITOR_WARNINGS=0 \
  bash scripts/release_mt5_v210.sh stage-demo
```

Do not use the older EX5 with the compiler warning. This command stages
only `DKcapitalV210_TP1to6_SignFix1.ex5` alongside historic demo EAs.

## 3. Attach safely on DEMO chart, with entries OFF

In VNC 5901: confirm GBP DEMO login `5057220041`, XAUUSD,
**zero open positions and zero pending orders** in the MT5 Trade tab,
and confirm no competing EA is attached to any chart.

Refresh Navigator, attach **V2.10 SignFix1**. Set these inputs before
confirming the dialog:

- `ExpectedDemoLogin=5057220041`
- `FeedUrl=http://127.0.0.1:8765/v4/demo/feed`
- `TelemetryUrl=http://127.0.0.1:8765/v2/demo/telemetry`
- `FeedToken`: existing private token from the local saved inputs/preset.
  Do not paste credentials into logs, chats, or GitHub.
- `EnableNewEntries=false`, `EnableLayerEntries=false`,
  `EnableOpenRunnerEntries=false`,
  `EnableStormConditionalTP1=false`.

Check Experts tab confirms `version=2.10` and no
`invalid configuration` errors. With the old gateway, the
V4 instruction request can temporarily log HTTP **404**, because
`/v4/demo/feed` is not deployed yet. That is **expected only during
this disarmed bootstrap period**, and it must not generate entries.

The attached, disarmed V2.10 EA reports fresh broker-side identity,
positions, orders, account state and its `entry_enabled=false` over
the **existing** `/v2/demo/telemetry` POST endpoint.

## 4. Deploy backend after disarmed broker telemetry is fresh

```bash
cd /opt/dkcapital
bash scripts/release_mt5_v210.sh deploy-backend
bash scripts/release_mt5_v210.sh check-backend
```

Both pre- and post-Docker-build preflight gates still **require**:
fresh authenticated telemetry (age under 120 seconds), GBP DEMO
login `5057220041`, no broker positions, no pending orders,
`entry_enabled=false`, and broker stops protected. The HTTP feed
status must be **200 or the pre-upgrade 404** during bootstrap.
A 404 **does not waive any trading or risk checks**. The
post-deployment `check-backend` requires authenticated
`DKDEMO4` from `/v4/demo/feed` with HTTP **200**.

`deploy-backend` backs up Telegram events, signal state and
broker telemetry, then rebuilds/recreates **only signal-processor
and mt5-gateway**. It does not restart either MT5 terminal or
automatically arm the EA. The existing paper-engine remains separate.

The old `/v2/demo/feed` remains for legacy EAs and safely marks
unsupported signals non-executable rather than silently truncating TPs.

## 5. Confirm the new gateway from attached V2.10

In VNC 5901 check the Experts log for a working V4 feed; refresh
the gateway telemetry and confirm `feed_http=200` and
`entry_enabled=false`. Leave new entries off through acceptance testing.

Do not delete old EAs, clear genuine broker telemetry or spoof snapshot
JSON to make release gates pass.

## 6. Controlled broker-demo acceptance tests

In a segregated TEST fixture rather than replaying live Telegram messages,
exercise **1, 2, 3, 4, 5, and 6 numeric TPs**.

- Exactly one native, provider-protected broker position per numeric TP.
- **Equal distribution of the total permitted lots**, rounded by broker
  volume step so that no legs differ by more than one step.
- No size inflation when adding targets; no synthetic exits.
- All positions use the actual provider SL; each has its own native TP.
- Native TP1-confirmed completion triggers protective BE as configured.
- Hard planned signal stop risk stays within 4%; spread, minimum lot,
  margin, total exposure and invalid stop gates remain in force.
- The 0.03 **Friday total** may prevent execution of a 4/5/6-TP
  signal under 0.01 broker minimum lot size; skip the **whole** trade.
- TP4/TP5/TP6 magic suffixes 5/6/7 remain separate from optional GWS
  layer suffix 4. Verify account broker history, Discord results
  and controlled recovery before considering any automated arming.

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
