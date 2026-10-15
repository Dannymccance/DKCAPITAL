# DK Capital MT5 Demo Account 1 sizing and guarded terminal recovery

## Why the two changes are needed

The existing MQL5 EA already targets floor(balance GBP / 100) * 0.01 total XAUUSD lots, allocated 30/30/40 across three legs (broker lot steps permitting). At £1,141.18 this is 0.11 lots total (typically 0.03 / 0.03 / 0.05). On Isle of Man Friday it intentionally caps *total* target at 0.03 lots. Broker spread, provider stop distance, free margin and aggregate exposure still can force a smaller allocation or no entry.

Prior caps of £75 per signal group and £150 per portfolio were fixed; the new code scales these two ceilings relative to a default RiskReferenceBalanceGBP of £1,000. It uses the *lower of balance and equity* and never guesses a missing stop. At £1,141.18 balance/equity, caps are £85.59 and £171.18 (85% of the group cap is initially allocatable; 15% remains reserved for one eligible layer). At £1,000 the old ceilings remain unchanged. These are *loss ceilings, not desired losses*; they correspond to large possible losses and should be explicitly risk-approved before live use. No leverage or lot overrides guarantee an entry.

## MT5 supervisor scope

Existing systemd unit dkcapital-mt5-1.service has Type=simple, Restart=always, RestartSec=20s, KillMode=control-group. That handles process exits, but not a frozen process whose service is still active. The new opt-in supervisor specifically watches Account 1 DEMO telemetry. It never restarts MT5 Account 2 or places/changes orders.

The *new compiled EA* includes account-wide proof fields:
- broker_positions_total: PositionsTotal across every symbol/EA
- broker_orders_total: OrdersTotal across the account
- unmanaged_positions_count: positions not managed by this XAUUSD EA
- broker_positions_protected: every open broker position has a native SL

The gateway normalizer accepts these fields only as a complete, internally consistent group. Older EA telemetry remains valid for dashboard reporting but is ineligible for unattended terminal recovery.

The supervisor verifies: expected login from MT5_ACCOUNT1_LOGIN in /opt/dkcapital/.env; DEMO account, GBP; gateway HTTP 200; active systemd unit; missing Account 1 telemetry for >150s; last known broker snapshot age <=600s; complete account-wide fields; no pending orders or unmanaged positions; native SL for every position; and no signal opened or managed after the last snapshot. It then requires 180s of persistent eligibility. Recovery is rate-limited to 2 attempted restarts per rolling hour and a 15-minute minimum cooldown. It writes the attempt to disk *before* invoking systemctl restart. Health recovery is verified by independent telemetry/watchdog monitoring; the script does not claim restart success means orders are safe.

CRITICAL LIMITATION: the account snapshot is stale when the EA is hung. It is NOT an independent live broker query, so a race remains possible between last telemetry and the freeze. The supervisor errs on the side of not restarting when in doubt. This demo-only approach requires a separate independent broker state channel and dedicated safety review before it is adapted to Vantage live execution.

## GitHub and VPS deployment

1. Merge only after GitHub Actions tests pass, and review the risk policy.
2. Pull changes on the VPS using git pull --ff-only origin main. Validate no unexpected local tracked changes.
3. Build and deploy *only* mt5-gateway and dkcapital-watchdog to get the new normalized telemetry support. If the signal processor has no changed code, do not restart it. Confirm gateway AUTH/feed/telemetry health after deployment.
4. Stage proposed source via `scripts/stage_compile_mt5_v2.sh` from the fetched PR commit, or use an existing `_stage_pr56_*` directory. On this VPS, MetaEditor **GUI F7** was confirmed to compile successfully on 11-12 October; CLI `/compile` under Wine exited without creating a log or EX5 on 15 October, even with `/portable`. To reach Account 1 GUI from Windows PowerShell, leave this SSH tunnel open: `ssh -N -o ExitOnForwardFailure=yes -L 5901:127.0.0.1:5901 root@31.187.72.71`, then connect TigerVNC to `127.0.0.1::5901`. In Account 1 MT5 press F4 (or open MetaEditor), File > Open `C:\\Program Files\\MetaTrader 5\\MQL5\\Experts\\<staged-folder>\\DKcapitalV2.mq5`, then F7 to compile. Confirm **0 errors** and `DKcapitalV2.ex5` inside the *staged folder* before installation. Copy the compiled .ex5 into the active MQL5/Experts path **only after backing up the previous binary**, checking open broker exposure, and a controlled EA reload. **Do not assume pulling .mq5 recompiles or reloads MT5.**
5. Verify exact demo login, accepted .ex5 version, entry-arm state, native TP/SL and current broker orders/positions. Never blindly replace a running EA with open positions. Preserve MetaTrader global variables, broker history and trade attempt deduplication state.
6. Verify the new fields are present in the server-side mt5-account1.json and reconcile against the MT5 Trade tab. The account-wide counts should report 0/0 and protected=True when flat.
7. Dry-run the host script:

       python3 scripts/mt5_terminal_autoheal.py

   It is read-only by default and prints why a restart is or is not eligible.

8. Controlled rehearsal: create a clean *demo-only* frozen-heartbeat simulation on a separate test instance, validate the 180s persistence, no restart when there are unmanaged/unprotected positions or pending orders, and journal/Discord alert recovery. Preserve actual broker state throughout.
9. Only after rehearsal explicitly install and enable the supervisor timer:

       sudo cp deployment/dkcapital-mt5-autoheal.service deployment/dkcapital-mt5-autoheal.timer /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl enable --now dkcapital-mt5-autoheal.timer
       sudo systemctl list-timers '*dkcapital-mt5-autoheal*'

   A timer not installed/enabled has no effect.

To disable unattended terminal recovery immediately:

    sudo systemctl disable --now dkcapital-mt5-autoheal.timer

The separate docker service recovery timer remains independent and is also disabled until explicitly configured.

## Validating lot sizing

Broker lot size depends on SYMBOL_VOLUME_STEP, SYMBOL_VOLUME_MIN, the provider's actual stop distance, broker currency conversion for risk, usable margin and the Friday cap. At £1,141.18 on a regular weekday, the targeted *total* 0.11 lots should be split to approximately 0.03 TP1, 0.03 TP2 and 0.05 TP3 if the broker supports 0.01-lot increments. If the caps cannot accommodate stop distance, the EA deliberately reduces volume rather than removing the stop. The optional 10% layer is separately gated and is not included in the initial 0.11 lots.

## Rollback

Disable dkcapital-mt5-autoheal.timer first. Restore the previous .ex5 from backup and prior Git revision only after checking broker positions and stops. Do not erase any MetaTrader terminal GlobalVariables, data directories or existing trade history. Do not reboot both demo accounts together. If telemetry is unavailable or account identity is uncertain, leave new entries disabled and investigate.
