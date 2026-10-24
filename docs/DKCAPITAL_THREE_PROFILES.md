# DK Capital: three actual MT5 trading accounts

The agreed three-profile model is **three independent MT5 terminals and broker accounts** receiving the same four GWS channels from the existing Telegram listener, shared signal parser and MT5 gateway feed. This is **not** one Python trading ledger replicated three ways.

The original MT5 DEMO account under Account 1 remains the paper-trading broker profile. Account 2 remains Kyle's existing MT5 terminal (VNC now working; broker setup postponed). Account 3 is the reserved new Dan terminal (not provisioned yet). Preserve all current account files and never reassign broker profiles or logins automatically.

## VNC/desktop isolation

| Profile | MT5 Linux identity | X display | VNC loopback port | Broker setup |
| --- | --- | --- | --- | --- |
| Paper trading (existing MT5 demo) | Account 1, `dkmt5` | `:150` | 5901 | Existing demo; do not change its login |
| Kyle's account (existing terminal) | Account 2, `dkmt5b` | `:151` | 5902 | Deferred |
| Dan's account (reserved third terminal) | Account 3, `dkmt5c` | `:152` | 5904 | Deferred; files NOT installed |
| Isolated MetaEditor compiler, NOT a trading profile | Docker compiler | `:99` | 5903 | V2.07 compilation only |

All four VNC servers must be bound to 127.0.0.1 and accessed only through SSH tunnels. MT5 Account 1 and Account 2 VNC servers require their independent existing local VNC passwords; Account 3 requires its own password at provisioning.

**Account 3 installation is deliberately not automatic.** The new code merely stages a systemd terminal unit and VNC unit; this PR does not create `dkmt5c`, a Wine prefix, a trading account, or any broker credential. Account 1 and Account 2 trading terminals and their currently installed EAs are unchanged.

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

## Account 3 commissioning (future, separate approved work)

When the user is ready to configure Dan's third account:

1. Provision a distinct Linux service identity `dkmt5c`, home `/var/lib/dkcapital-mt5-3`, and a *new* isolated Wine/MetaTrader 5 installation without copying Account 1 or Kyle's browser/login/session state. The signed installer/binary should be verified independently before running. Set a private VNC password for this identity.
2. Deploy an account-three-capable version of the host launcher `ops/mt5/dkcapital-mt5-run` after CI. Install the unit `ops/mt5/dkcapital-mt5-3.service`, without enabling or starting it until the binary and the expected broker account are verified. The unit uses display :152 and X server authentication unique to `dkmt5c`.
3. Install `ops/recovery/dkcapital-vnc3.service` after provisioning, with loopback-only port 5904, only after the account 3 X display is up. It must not start the trading terminal as a side effect.
4. Extend authenticated gateway **per-account telemetry** and bot reporting: the current V2 reporting code hardcodes only MT5 Account 1, GBP, DEMO. Account 3 must have its own expected login, currency/mode policy, telemetry snapshot, magic/position namespace, order dedup state, result channel and alarms. Do not reuse Account 1 credentials or its telemetry path. Kyle's account requires the same independent route.
5. Only after controlled broker-mode, symbol, hedging, tick-size, provider SL, lot-step, TP management, heartbeat, and 4% risk checks: compile/install the correct EA to that terminal (entry disabled by default); authorize simulated/demo entry trials. **V2.07 refuses LIVE accounts and currently assumes GBP DEMO**, so live or USD account support would require a separate design, tests and explicit approval.

The current Python `paper-engine` container is a **legacy independent simulation**, not the requested broker MT5 paper profile; do not count its synthetic P&L with broker-confirmed MT5 demo results. Its file and historical results are left untouched until an intentional retirement plan is approved.

No changes to either already-running MT5 service should be made while preparing Account 3; specifically avoid bulk `systemctl restart`, `docker compose down`, resetting the existing MT5 Wine prefixes or replacing an installed EX5 with open broker positions.
