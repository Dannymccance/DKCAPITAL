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


## Standard recovery and alert coverage (every commissioned MT5 account)

The **independent watchdog** already alerts for Telegram catchup, gateway, signal processor, and Discord health. With the new registry it additionally monitors **each opted-in MT5 account**, not just Account 1:

- stale broker telemetry (>150 seconds), feed not HTTP 200, unexpected EA arming/disarming based on the profile's intended setup/trading status
- wrong account login or account type/currency (when account login is explicitly configured)
- missing broker account-wide exposure counts or broker-native SL protection
- unavailable systemd service, recent restart, stale host monitoring
- recovery notifications when these faults clear, plus an invalid registry warning
- shared base watchdog notifications remain operational; messages identify the account, so no failure of Kyle's account is attributed to Dan or the demo.

The new root-side \`mt5_profile_audit.py\` probes only \`dkcapital-mt5-N.service\` via \`systemctl show\`. It **never starts/restarts/stops MT5, opens trades, or reads passwords**. The audit's short status file goes into the existing \`dkcapital-data\` Docker volume, where the container watchdog can read it. The watchdog alerts to the existing \`MT5_ALERT_CHANNEL_ID\`, and its alert/recovery deduplication persists.

### How to register an account after broker telemetry is working

Use \`deployment/mt5-monitor-profiles.example.json\` as a *template*, NOT a ready-to-use config: \`expected_login: 0\` is intentionally invalid. Provision only after you know the actual broker login, account mode, currency and whether new EA entries are expected. Store the completed file as \`/app/data/mt5-monitor-profiles.json\` in the shared Docker volume, with 0600 permissions. Account 1 must be included as enabled. **Accounts 2 and 3 remain disabled until their own EA telemetry and login restrictions work.** The registry contains no tokens, passwords or broker server credentials.

Account identity \`id=N\` always refers to \`/app/data/mt5-accountN.json\` and \`dkcapital-mt5-N.service\`. There are no arbitrary user-specified service paths or shell commands. Existing no-registry Account 1 checks continue unchanged.

For a deliberately disarmed account in setup mode use \`expect_entries=false\`; an unexpectedly armed EA generates an alert. Set to \`true\` only after the operator has approved entry activation. An account may be enabled for **monitoring** while still having unattended recovery disabled.

### Install the read-only host audit only (post-CI and merge)

First check the new account names/paths. This does not start any broker terminal or copy EAs:

\`\`\`bash
cd /opt/dkcapital
git pull --ff-only origin main
python3 -m py_compile scripts/mt5_profile_audit.py scripts/mt5_terminal_autoheal.py
python3 -m unittest discover -s tests -p 'test_mt5_profile*.py' -v

# Account 1's existing .env and all active terminals are left alone.
install -o root -g root -m 0644 deployment/dkcapital-mt5-profile-audit.service /etc/systemd/system/
install -o root -g root -m 0644 deployment/dkcapital-mt5-profile-audit.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now dkcapital-mt5-profile-audit.timer
systemctl start dkcapital-mt5-profile-audit.service
journalctl -u dkcapital-mt5-profile-audit.service --no-pager -n 25
\`\`\`

The watchdog **Docker image** must be rebuilt from the merged source to load new per-account alerts. Do so in a separate controlled rollout; the recovery template does not touch \`mt5-gateway\`, \`signal-processor\`, \`telegram-listener\`, \`discord-bot\`, any MT5 systemd terminal or VNC service. A profile registry is not required to keep historical Account 1 checks.

### Unattended recovery policy per profile

- **Paper/demo:** the existing Account 1 demo-only autoheal and LiveUpdate guarded recovery continue. The generic frozen-terminal script now also accepts \`--account-id N\`, with distinct persistent state and locking, after explicit registry opt-in and proof of GBP DEMO. The Account 1 legacy cooldown/state file is preserved. Future Account 2/3 **demo-only** timer/unit templates are staged in \`deployment/\`, but are not installed or enabled. Their timers may be enabled only after mode/login/currency validation, complete broker exposure telemetry, acceptance tests, and approval.
- **Live Vantage (Dan or Kyle):** automated frozen-terminal recovery remains **blocked**. Restarting an MT5 process while an unknown trade or pending order exists can duplicate actions, interrupt management, or strand exposure. A stale snapshot cannot prove current broker state. Monitor and alert; obtain an independent broker-side source of truth and a separately reviewed safe restart policy before allowing unattended live recovery. Existing systemd unit automatic restart behaviour, if any, must also be reviewed before funding.
- **Never** apply the demo-only LiveUpdate quarantine algorithm to a live account. The Account 1 approved terminal hash gate is not automatically configured for future accounts; that remains commissioning work.

This is an infrastructure **standard and implementation of monitoring**. It does not claim Dan or Kyle's accounts are configured, nor does it enable new trading entries. Their broker telemetry, independent gateway authentication and Discord result cards remain distinct future integration work. Verify alerts with a controlled test event before considering any account monitored end-to-end.
