# Account 1: guarded MT5 recovery after incompatible October 2026 LiveUpdate

Account 1 originally ran a terminal executable with SHA-256
`2e3c9aea103e7f4444bb917454b0a740372400c7f7a6c0e0d36926e81098b9a1`.
It opens successfully under the Ubuntu Wine 9.0 runtime in a network-isolated
Xvfb lab. An October 18 update replaced it with a 121055472-byte executable
which shows a "debugger has been found" modal under both Wine 9.0 and 10.0.

## Protection policy

* **Account 1 only:** launch with best-effort `/skipupdate`, which is
  **undocumented** and may be ignored. It is not a guaranteed update block.
* Before each Account 1 launch and after terminal exit, compare
  `terminal64.exe` to the root-controlled pinned SHA-256 at
  `/etc/dkcapital/mt5-account1-approved.sha256`. If the pin is missing,
  invalid or the executable differs, exit **78**, and do not auto-restart.
* If Account 1 announces a fresh LiveUpdate handoff, exit 78 rather than
  relaunching an untested build; systemd `RestartPreventExitStatus=78`
  prevents an endless restart loop.
* Account 2 continues to use its existing Wine 9.0 command line and unmodified
  LiveUpdate monitoring; its service is not modified or restarted.
* Account 1 VNC no longer starts the trading terminal as a side effect.

The guard is a fail-closed **recovery mechanism**, not a way to stop MetaQuotes
from downloading updates. It cannot guarantee an already-running process will
never launch a new build, or that a broker will accept an old version. The
broker might require a newer version, in which case stage and test a supported
Wine/MT5 combination before approving that SHA. Keep broker-native SL on any
open positions and require fresh signed broker telemetry before enabling new
trades. Watchdog should report stale telemetry; an external VPS monitor is also
advisable.

## Installation on existing VPS (do NOT restart Account 2)

Before deploying, confirm Account 1 service and VNC are stopped, there are no
Account 1 terminal processes, and Account 2 is active. On Oct 18 the operator
disabled Account 1 boot startup temporarily and preserved its full Wine prefix.
The current known-good executable was manually restored to Account 1.

```bash
cd /opt/dkcapital
git pull --ff-only origin main
python3 -m py_compile ops/mt5/dkcapital-mt5-run
python3 -m unittest discover -s tests -p test_mt5_update_supervisor.py -v

# Confirm that the previously validated executable remains installed.
echo '2e3c9aea103e7f4444bb917454b0a740372400c7f7a6c0e0d36926e81098b9a1  /var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe' | sha256sum -c -

install -d -o root -g root -m 755 /etc/dkcapital /etc/systemd/system/dkcapital-mt5-1.service.d
printf '%s\n' '2e3c9aea103e7f4444bb917454b0a740372400c7f7a6c0e0d36926e81098b9a1' > /etc/dkcapital/mt5-account1-approved.sha256
chown root:root /etc/dkcapital/mt5-account1-approved.sha256
chmod 644 /etc/dkcapital/mt5-account1-approved.sha256

# Backup launcher and VNC definition before replacing.
cp -a /usr/local/bin/dkcapital-mt5-run /usr/local/bin/dkcapital-mt5-run.pre-account1-guard
cp -a /etc/systemd/system/dkcapital-vnc1.service /etc/systemd/system/dkcapital-vnc1.service.pre-account1-guard
install -o root -g root -m 755 ops/mt5/dkcapital-mt5-run /usr/local/bin/dkcapital-mt5-run
install -o root -g root -m 644 ops/recovery/mt5-account1-approval.conf /etc/systemd/system/dkcapital-mt5-1.service.d/20-account1-approval.conf
install -o root -g root -m 644 ops/recovery/dkcapital-vnc1.service /etc/systemd/system/dkcapital-vnc1.service
systemctl daemon-reload
```

The deployment **does not** start Account 1, enable Account 1 on boot, or
change Account 2's running process. Do not re-run `install-recovery.sh` during
this incident: that installer re-enables both MT5 and VNC services.

Once exposure is independently checked, `systemctl unmask --runtime` if a
runtime mask remains, then `systemctl reset-failed` and start Account 1 manually.
First ensure the service does not fall into LiveUpdate, and verify correct login
5057220041, DEMO, GBP, EA V2.05, correct feed HTTP 200, live telemetry
freshness (<180 seconds), broker positions/orders, stop protection, and risk
inputs. Do not re-enable automatic trading on stale telemetry. Only after
successful acceptance tests, enable Account 1 and VNC boot units.

## Tested promotion of a future MT5 build

1. Preserve original executable and full Wine prefix before any update.
2. Test candidate MT5 build inside an offline separate-prefix, separate-display
   container/namespace with **no broker network access and no active EAs**.
3. Check screenshots and terminal logs, not just that a Wine process survives.
4. Test broker compatibility safely without placing unapproved orders.
5. Update the pinned SHA-256 **only** after acceptance testing, and verify the
   intended binary is already installed. Test the entire update handoff path.
6. Perform a controlled Account 1 service start, verify native broker stop-loss
   protections and EA telemetry, and restore alerts/boot recovery.

To revert the software change, stop Account 1 and VNC first, restore the
backed-up launcher and VNC unit, remove the Account 1 drop-in, then
`systemctl daemon-reload`. Never blindly restart Account 2.
