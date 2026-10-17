# MT5 Wine automatic-update recovery (Accounts 1 and 2)

## Incident

On 17 October 2026, after Hostinger restarted the VPS for node maintenance, MT5 Account 1 repeatedly exited with status 0 following a MetaTrader LiveUpdate handoff. The existing /usr/local/bin/dkcapital-mt5-run shell script treated the terminal exit as a complete shutdown, immediately killed Xvfb display :150, and systemd killed the remaining Wine children. Retrying the service repeated the handoff until the systemd restart limit was reached.

The VPS reboot was a hosting-provider event, not an EA crash. This patch targets the separate recovery failure.

## Fix

The new ops/mt5/dkcapital-mt5-run Python 3 supervisor:
- Preserves account users, Wine prefixes, X displays, and Account 1's existing MT5 profile.
- Refuses to run as the wrong Linux user or on an occupied display.
- Keeps Xvfb alive across fresh UTF-16 LiveUpdate startup log messages.
- Waits for the updater and either monitors MetaTrader's own relaunch or relaunches MT5 once it settles.
- Bounds update waiting to 10 minutes and repeated handoffs to 3, reporting failures to systemd instead of silently claiming success.
- Does not touch compiled EA binaries, chart settings, credentials, account exposure, risk limits, or trading permissions.

Limitations: MetaQuotes controls its updater and may change its process identifiers or log format. A successful offline test is not proof of a successful real-world update. Check Account 1's fresh authenticated broker telemetry after the deployment.

## Offline tests

    python3 -m unittest discover -s tests -p 'test_mt5_update_supervisor.py' -v
    python3 -m py_compile ops/mt5/dkcapital-mt5-run

## Safe VPS rollout: AFTER Hostinger node maintenance

Before restarting MT5: verify any existing broker positions and pending orders; save Account 1 EA inputs, profile, and broker state. Do not restart Account 2. The shared launcher affects Account 2 only on its next service restart.

After merging the PR and pulling main on the VPS:

    cd /opt/dkcapital
    python3 -m unittest discover -s tests -p 'test_mt5_update_supervisor.py' -v
    python3 -m py_compile ops/mt5/dkcapital-mt5-run
    sudo cp -p /usr/local/bin/dkcapital-mt5-run /usr/local/bin/dkcapital-mt5-run.pre-update-supervisor
    sudo install -o root -g root -m 755 ops/mt5/dkcapital-mt5-run /usr/local/bin/dkcapital-mt5-run
    sudo systemctl reset-failed dkcapital-mt5-1.service
    sudo systemctl start dkcapital-mt5-1.service

Allow the update handoff to settle (up to 10 minutes), then review:

    systemctl is-active dkcapital-mt5-1.service
    journalctl -u dkcapital-mt5-1.service -b --no-pager -n 80

Confirm fresh authenticated demo broker telemetry for login 5057220041, with daily_loss_guard_mode=ALERT_ONLY, signal_risk_cap_pct=4.0, and correct positions/orders, stops and entry/layer switches. Confirm the Experts log and watchdog. An active systemd unit is not enough; do not enable new entries without the agreed controlled broker tests.

## Rollback

Restore the original launcher:

    sudo install -o root -g root -m 755 /usr/local/bin/dkcapital-mt5-run.pre-update-supervisor /usr/local/bin/dkcapital-mt5-run

Restart Account 1 only after safeguarding broker exposure and chart configuration. Do not delete Wine prefixes or the liveupdate directory to work around an unresolved update. The MT5 updater itself may still need manual repair.
