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
python3 test_sniper_gate.py -v
python3 install_gate.py probe
```

The probe does not modify the bridge or EA. Review the probe output before running `shadow`; **do not run `activate` until deal closure detection and M1-bar formats are verified**. The branch is deliberately separate from `main`.

Archive source was tested locally (6 unit tests passing). GitHub archive integrity can be verified using SHA-256 above.
