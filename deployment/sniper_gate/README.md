# DK Capital Sniper Pro reversal gate (staging branch)

This directory contains the separately packaged sidecar filter, not a modification to the existing MT5 EA.

- Reject a signal if the prior 30 completed M1-bar range is at least $15.
- Pause BUY and SELL directions independently after two realised same-side SL closes in 120 minutes.
- Allow unrestricted overlap for accepted trades. Do not change SL, TP, entry or lot sizing.
- The M1 and trade-history export formats and broker time offset must be verified on the VPS before live activation.

## Download and probe using the VPS web terminal

```bash
set -e
cd /root
curl -fsSL 'https://raw.githubusercontent.com/Dannymccance/DKCAPITAL/feature/sniper-reversal-gate-20261112/deployment/sniper_gate/dk_sniper_gate.tar.xz.b64' -o dk_sniper_gate.tar.xz.b64
base64 -d dk_sniper_gate.tar.xz.b64 > dk_sniper_gate.tar.xz
echo '62ed5ce1946ee0538440ddccf3c924a8df7c7a5cf2c51f225539348999aafc0a  dk_sniper_gate.tar.xz' | sha256sum -c -
mkdir -p /root/dk_sniper_gate
tar -xJf dk_sniper_gate.tar.xz -C /root/dk_sniper_gate
cd /root/dk_sniper_gate
# Override the old archive's time-dependent test harness with the corrected GitHub version.
curl -fsSL 'https://raw.githubusercontent.com/Dannymccance/DKCAPITAL/feature/sniper-reversal-gate-20261112/deployment/sniper_gate/test_sniper_gate.py' -o test_sniper_gate.py
python3 test_sniper_gate.py -v
python3 install_gate.py probe
```

The probe does not modify the bridge or EA. Review the probe output before running `shadow`; **do not run `activate` until deal closure detection and M1-bar formats are verified**. The branch is deliberately separate from `main`.

The original archive contains a flaky test harness that uses :22 of the current minute as an artificial timestamp, which may be in the future. The source filter was not changed. The corrected, standalone GitHub test file uses a past timestamp and adds regression checks for future signals, missing deal history and shadow mode. This is why the command explicitly replaces the archive's test script before running tests. GitHub archive integrity can be verified using SHA-256 above.


## Read-only telemetry publisher

The existing trading EA exports deals without `DEAL_REASON` and may not
publish M1 bars because `ExportMarketTelemetry=false`. Do not activate the
gate with these missing data sources.

Compile and attach `SniperGateTelemetry.mq5` to an ADDITIONAL XAUUSD
chart in the SAME terminal/account as the live trading EA. It contains
no order-management code. It writes only:

- `discord_limit_xau_m1.csv`: completed one-minute candles
- `discord_limit_deals_enriched.csv`: four hours of XAUUSD deals with
  genuine MT5 deal reasons, filtered by magic number 26110430
- `discord_limit_gate_telemetry_identity.csv`: login, server, clock offset

Confirm the active trading EA magic number is 26110430. Verify the account,
server, clock offset, and real stop-loss reasons before any live rerouting.

Prepare downloaded Python sidecar scripts without touching live services:

```bash
cd /root/dk_sniper_gate
curl -fsSL 'https://raw.githubusercontent.com/Dannymccance/DKCAPITAL/feature/sniper-reversal-gate-20261112/deployment/sniper_gate/patch_gate_for_enriched_telemetry.py' -o patch_gate_for_enriched_telemetry.py
python3 patch_gate_for_enriched_telemetry.py
python3 patch_gate_for_enriched_telemetry.py --apply
python3 test_sniper_gate.py -v
python3 install_gate.py probe
```

This changes only the downloaded Python sidecar, not the EA or live bridge.
The Python gate refuses to forward signals if the enriched deals file goes
stale for more than 30 seconds. The new MQL5 publisher is not compiled or
deployed merely by committing its source to GitHub.
