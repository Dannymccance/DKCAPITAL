# DK Capital: three separate environments

## VNC/desktop isolation

| Purpose | Linux service/process | Display | VNC port | SSH-only connection |
| --- | --- | --- | --- | --- |
| Owner MT5 Account 1 | `dkcapital-mt5-1`, user `dkmt5` | `:150` | 5901 | `ssh -N -L 5901:127.0.0.1:5901 root@<VPS>` |
| Kyle MT5 Account 2 | `dkcapital-mt5-2`, user `dkmt5b` | `:151` | 5902 | `ssh -N -L 5902:127.0.0.1:5902 root@<VPS>` |
| Isolated MetaEditor compiler | Docker `dkcapital-mt5-compiler-vnc`, user compiler | `:99` | 5903 | `ssh -N -L 5903:127.0.0.1:5903 root@<VPS>` |

Never expose VNC on the VPS public interface. VNC terminals each require their existing locally stored password; the compiler port is unauthenticated at VNC level and depends entirely on localhost binding plus the SSH tunnel. Do not use a single MT5 terminal for two broker accounts.

### Install the Account 2 VNC-only service update

After PR merge and `git pull --ff-only origin main`, inspect the currently installed unit and port before updating:

```bash
systemctl cat dkcapital-vnc2.service
ss -lntp | grep -E ':(5901|5902|5903)\b' || true
```

If no other service owns loopback port 5902, install the VNC service file **only**, without restarting either MT5 terminal:

```bash
install -o root -g root -m 0644 ops/recovery/dkcapital-vnc2.service /etc/systemd/system/dkcapital-vnc2.service
systemctl daemon-reload
systemctl start dkcapital-vnc2.service
systemctl is-active dkcapital-mt5-1.service dkcapital-mt5-2.service dkcapital-vnc2.service
journalctl -u dkcapital-vnc2.service --no-pager -n 25
```

The unit waits for display :151. It no longer implicitly starts Account 2 on VNC start. If MT5 Account 2 was not already active, do not use VNC merely to force it to start.

### Compiler black-screen triage

A running compiler VNC does **not** prove MetaEditor is running. Check:

```bash
docker exec dkcapital-mt5-compiler-vnc sh -c 'pgrep -af "MetaEditor|metaeditor" || true'
tail -40 /opt/dkcapital-mt5-compiler/workspace/metaeditor.log
```

If MetaEditor is absent, it is safe to relaunch **within the isolated compiler** only:

```bash
docker exec -d -u compiler -w '/workspace/MetaTrader 5' dkcapital-mt5-compiler-vnc \
  sh -c 'wine ./MetaEditor64.exe /portable >>/workspace/metaeditor-v207-relaunch.log 2>&1'
```

Recheck after a few seconds; if it exits again, preserve the new log for diagnosis instead of restarting a trading terminal, replacing the EX5 or upgrading production Wine.

## Intended application profiles

1. Paper: `paper-engine` is a Python simulation. Isolate simulated account ledger, strategy settings and Discord paper dashboard; never treat simulations as broker fills.
2. Owner: existing MT5 Account 1 with its own Wine prefix, broker account, EA settings, broker-native orders and telemetry. Currently the V2 gateway uses account-one-only login and GBP DEMO validation.
3. Kyle: existing MT5 Account 2 has a separate Wine prefix and service, but independent account-two V2 authenticated gateway telemetry, monitoring, risk policy and Discord routing are **not implemented** merely by enabling VNC.

No common credentials or session-specific account settings should be stored in GitHub. Real-money activation requires currency/broker/hedging checks and separate protective-order acceptance. Do not enable Account 2 entries until the gateway and account-specific controls are actually built and tested.
