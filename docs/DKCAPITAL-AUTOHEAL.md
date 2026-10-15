# GWS correction handling and controlled service recovery

## Scope

This change handles the 15 October 2026 GWS XAUUSD stop typo (original 4244, corrected 4144) without weakening the existing EA stop checks. The original Telegram signal identity and entry timestamp are preserved. A separate unqualified SL correction may repair exactly one recent GWS signal with an invalid stop. If multiple signals qualify, the correction is left unresolved rather than applying an arbitrary stop.

Explicit group-management messages such as "BOTH SL TO ..." retain existing semantics. For new entries, the EA still enforces its 180-second age limit, broker stop validation, entry price, spread, exposure and risk limits. Corrections do not restart the age clock. No MQL5 source or broker account settings are changed.

## Watchdog and recovery

The independent watchdog now detects an unconsumed event-log suffix when the processor heartbeat remains current and the event log has been quiet for at least 60 seconds. The separate host-side recovery script checks its own live conditions, including gateway health, processor, Telegram and Discord heartbeats, and MT5 telemetry.

The recovery script is read-only unless run with --apply. When enabled, it can restart only these Compose services: signal-processor, telegram-listener, discord-bot and mt5-gateway. It requires 180 seconds of persistent failure, limits service restarts to two attempts per issue per hour, enforces a 15-minute cooldown, and restarts at most one service per cycle.

MT5 terminal restarts, broker orders and EA parameter changes are NOT automated. Stale MT5 telemetry, disarmed entry state, a failed feed and incomplete Telegram catch-up remain alert-only. Broker exposure must be reconciled manually when execution state is uncertain.

The repair script automatically finds the host-side mount backing /app/data in the signal-processor container, so it works with the named Compose data volume and does not rely on /opt/dkcapital/data.

## Validation before deployment

Run from the repository root, with normal project Python dependencies available:

    PYTHONPATH=src python3 -m unittest tests.test_signal_parser tests.test_gws_correction_regression tests.test_watchdog tests.test_host_autoheal -v
    PYTHONPATH=src python3 -m unittest discover -s tests -v

After merging on GitHub, update the VPS using your existing Git deployment process. BEFORE restarting any running services, verify the branch, local changes, Compose config, MT5 open positions and native protective stops. Prefer a maintenance window or no-open-position state. Never delete the EA's persistent broker/order deduplication state.

If source files and tests have been verified against the VPS version, rebuild only the application containers whose Python source changed:

    docker compose up -d --no-deps --build signal-processor dkcapital-watchdog
    docker compose ps
    docker compose logs --since 3m --tail 50 signal-processor dkcapital-watchdog

A change on GitHub alone does not deploy to the VPS and does not enable recovery.

## Dry-run and enabling auto-recovery

From the host, while Docker Compose is installed and the processor container exists:

    python3 scripts/host_autoheal.py

The dry-run prints the detected data volume and issues; it never restarts anything. Review results and simulate each recovery case safely in a demo-only environment with broker state reconciled.

The unit and timer are examples to install after those checks:

    sudo cp deployment/dkcapital-autoheal.service deployment/dkcapital-autoheal.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now dkcapital-autoheal.timer
    sudo systemctl list-timers '*dkcapital-autoheal*'

Enabling the timer is the explicit opt-in to --apply and can restart selected containers. To disable it:

    sudo systemctl disable --now dkcapital-autoheal.timer

For the historic signal, the diagnostic can inspect the actual Compose data mount:

    python3 scripts/host_autoheal.py

Use the printed data_dir value with:

    python3 scripts/diagnose_gws_7050.py --data-dir /real/docker/mounted/path

The source archive alone cannot confirm the original corrected signal's ASK, spread and risk eligibility. A valid correction does not establish that a trade would have filled.

## Recovery limitations and rollback

This is a bounded worker recovery mechanism, not a guarantee against outages, stalled EA threads, or market-data faults. Keep broker-side protective stops and alerting active. Do not run live trades while reconciling an unknown exposure state.

If regression occurs, disable the timer first, restore the prior application revision using Git, rebuild the affected application images, inspect current broker positions and verify health before resuming new entries. Do not restart MT5 automatically with an unknown account state.
