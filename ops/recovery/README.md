# DK Capital: reboot and outage recovery

## Scope

This covers the DK Capital installation at /opt/dkcapital, NOT every
application installed on the Hostinger VPS (for example, Alpha Foundry).

| Component | Mechanism | Verification |
| --- | --- | --- |
| Core Telegram listener, signal processor, paper engine, Discord bot, MT5 gateway and watchdog | Existing Docker Compose restart: unless-stopped plus dkcapital-stack.service at boot | docker inspect status, independent heartbeat/watchdog |
| MT5 Account 1 | Existing dkcapital-mt5-1.service plus LiveUpdate-aware launcher and 120-second systemd retry interval | fresh authenticated GBP DEMO telemetry, login 5057220041 and V2.05 risk fields |
| MT5 Account 2 | Existing dkcapital-mt5-2.service plus same launcher and retry override | systemd active, then account-specific broker checks |
| VNC for Account 1 | dkcapital-vnc1.service, reconnecting to X display :150, bound to loopback port 5901 | localhost VNC + SSH tunnel |
| VNC for Account 2 | dkcapital-vnc2.service, reconnecting to X display :151, bound to loopback port 5903 | localhost VNC + SSH tunnel |
| Read-only audit | dkcapital-boot-audit.timer at boot and every 5 minutes | journalctl -u dkcapital-boot-audit.service |

Only explicit core Docker services are started by the boot unit; optional
bybit-demo and maintenance/tool profiles are not activated.

**Limitations:** A VPS cannot restart or report an outage while its underlying
Hostinger node is powered off. Use an external uptime monitor for host-level
alerts. A corrupted MetaTrader/Wine installation or expired broker
authentication may still require operator intervention. Never describe MT5
as recovered solely because systemd says active: verify broker telemetry.

The audit does not place orders, restart MT5, alter risk limits, rearm
previously disarmed entries, or modify any broker position. Docker's own
restart policies and systemd handle automatic recovery from process exits.
The Docker stack boot unit explicitly starts the six core services again,
including any core service intentionally stopped before the previous reboot.

## Before deployment

Wait until Hostinger confirms VPS maintenance is over. Ensure Account 1 broker
positions and pending orders have been checked directly, save its EA inputs
and profile (including gateway token without disclosing the token), and
preserve snapshots. Check Account 2 open exposure separately; its currently
running process must not be restarted in the rollout.

A password file MUST exist for both VNC endpoints. Never commit either
password file, and never expose ports 5901/5903 publicly. Account 1 already
uses /var/lib/dkcapital-mt5/.vnc/passwd. Account 2 may need to be configured
under its own service user; make sure to preserve a working access method
and coordinate any existing VNC listener on port 5903.

To create Account 2's secret, after confirming its VNC requirements, run:

    install -d -o dkmt5b -g dkmt5b -m 700 /var/lib/dkcapital-mt5-2/.vnc
    runuser -u dkmt5b -- x11vnc -storepasswd /var/lib/dkcapital-mt5-2/.vnc/passwd

This requires interactive password entry. Do not send the password to ChatGPT.

## Deployment, with operator approval

Merge PR #59 after review and after the provider maintenance window.
On the VPS, pull main, run tests and inspect the unit files BEFORE
running the installer. The installer requires both VNC secrets and
preexisting MT5 service units.

    cd /opt/dkcapital
    git pull --ff-only origin main
    python3 -m unittest discover -s tests -p 'test_mt5_update_supervisor.py' -v
    python3 -m unittest discover -s tests -p 'test_reboot_audit.py' -v
    sudo bash ops/recovery/install-recovery.sh --install

The installer backs up the old launcher in /var/backups/dkcapital-recovery,
installs the new launcher and systemd units, and ENABLES the six boot units.
It intentionally does NOT start or restart MT5, Docker, or VNC at install time.

Before the NEXT reboot check for conflicting, independently managed x11vnc
services: the two newly enabled VNC units will want ports 5901 and 5903.
Move/disable a former VNC unit only with explicit operator approval; do
not kill Account 2's current VNC process while it is being used.

## Recover failed Account 1 when maintenance is complete

After safeguarding MT5 exposure and account settings, intentionally retry
Account 1 only:

    systemctl reset-failed dkcapital-mt5-1.service
    systemctl start dkcapital-mt5-1.service
    journalctl -u dkcapital-mt5-1.service -f

The launcher may need up to 10 minutes for each bounded LiveUpdate
handoff. A repeat loop emits an explicit error. Check its real broker
telemetry and Experts logs. Do not make trading-rule changes or
blindly re-enable entries or layers.

## Acceptance checks before calling recovery complete

1. Confirm Hostinger maintenance is complete and backup/snapshot access works.
2. Confirm Docker, six core Docker containers, both MT5 systemd services
   and both protected VNC endpoints are active.
3. Confirm Account 1 is GBP DEMO login 5057220041 with fresh telemetry,
   V2.05 risk cap 4.0%, daily loss mode ALERT_ONLY, and correct
   native stop protection and pending order counts.
4. Check Account 2 login, chart EA state and protection without modifying
   or resetting its existing positions.
5. Confirm Telegram catch-up, processor heartbeat, Discord bot, watchdog
   and any intended signal/result channels are working. Recovery of process
   state does not certify broker executions.
6. Execute a CONTROLLED reboot during a maintenance window, with open
   broker exposure separately assessed and safely protected.
7. After boot, allow 4 minutes for audit initialisation and read the audit
   output. Repeat checks after at least 10 minutes. Do not consider the
   system resilient until this actually passes.

Audit commands:

    python3 /opt/dkcapital/ops/recovery/check_boot.py --require-vnc
    systemctl list-timers --all | grep dkcapital-boot-audit
    journalctl -u dkcapital-boot-audit.service -n 30 --no-pager

A failed audit logs a degraded status. The Docker watchdog separately sends
Discord alerts for stale telemetry and component heartbeats if it is online.
If the whole VPS is down, no on-box process can notify you; use Hostinger
status and an outside uptime monitor.

## Rollback

Installer backups are under /var/backups/dkcapital-recovery/<UTC timestamp>.
Disable only the new boot units if needed; do not stop currently trading
services without evaluating broker exposure. Restore the original launcher
from the dated backup before running a deliberate maintenance restart.
Retain the existing MT5 profiles, EA binaries, Wine prefixes and data volume.
