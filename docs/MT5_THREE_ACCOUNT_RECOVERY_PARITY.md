# DK Capital: strict three-account recovery parity

All three terminals must use the same signal sources, strategy code, stop/target
management, risk percentages and recovery *design*. Broker account identity,
currency, execution symbol (Dan XAUUSD+), independent secrets and Linux/VNC
isolation necessarily differ.

## Verified recovery architecture (26 Oct 2026)

- Systemd on Account1/2/3: 120-second restart after process failure, if approved
  terminal executable and launcher permit starting. This does **not** detect
  a terminal that is hung but whose process remains alive.
- Account1: demo-only frozen-terminal autoheal timer every 60 seconds, with
  180-second persistence, 15-minute cooldown and 2/hour restart cap.
- Accounts2/3: existing account-specific demo timers explicitly block REAL
  trading. **Do not enable them on REAL accounts.**
- Shared supervisor: covers all commissioned profiles, but for REAL it requires
  explicit, tested `restart_reconciliation_supported=true` from the EA, plus
  broker-owned inventory and protective native stops. Current V2.11 live
  telemetry does not declare that capability. A live frozen-terminal restart
  must not be claimed or enabled before that implementation and tests exist.
- Account3: systemd service and VNC are currently started manually; whether
  they are enabled on reboot must be audited on VPS.

## Deploy the read-only parity check

After PR merge:

    cd /opt/dkcapital
    git fetch origin main
    git merge --ff-only origin/main
    python3 -m unittest discover -s tests -p 'test_mt5_fleet_recovery_parity.py' -v
    python3 scripts/mt5_fleet_recovery_parity.py

It reads authenticated snapshots from the Docker shared data volume and shows
independent terminal/VNC service state, boot, crash and freeze recovery evidence
for all three. Missing broker snapshots produce failed checks, not a green pass.

## Commission Dan for recovery after HOST REBOOT

Only after verifying Account3 is online, broker identity and protective stops,
run an initial read-only preflight:

    python3 scripts/enable_account3_boot_parity.py

If valid, the operator can explicitly enable boot recovery *without* restarting
live terminals or touching positions:

    python3 scripts/enable_account3_boot_parity.py --enable-boot

This enables just `dkcapital-mt5-3.service` and `dkcapital-vnc3.service`
for subsequent host boots. It cannot prove that the EA will reattach to its
chart, that login will persist or broker orders reconcile after a full reboot.
An actual controlled reboot acceptance test is still required at an appropriate
maintenance window. Enabling boot is a persistent choice: if Dan's EA is armed,
it may resume live order submission after a future reboot. The operator must
explicitly want this behaviour.

## Work remaining to achieve full live freeze-recovery parity

1. Implement restart reconciliation for EA-managed broker positions, pending
   orders, broker deal history, layered entries and processed signal IDs.
2. Confirm the corresponding REAL account telemetry flag is sourced from
   working runtime functionality, rather than simply hardcoding it to true.
3. Test in a segregated MT5 live-like environment for both zero exposure and
   open broker-native stops, including stale/partial broker inventory.
4. Only after that, commission Account2 and Account3 in the shared recovery
   registry with per-account monitor profiles and the bounded supervisor timer.
5. Verify restart actually restores the EA and fresh authenticated data, with
   alert and recovery events, without duplicate entry or missed SL updates.
6. Maintain one master EA source and identical account-normalised risk
   percentages. Account-specific volume denominators must be justified by
   FX conversion and XAUUSD contract specification rather than being
   cosmetically identical numeric settings.

Do not remove the reconciliation gate for REAL merely to make it appear
identical to the demo.
