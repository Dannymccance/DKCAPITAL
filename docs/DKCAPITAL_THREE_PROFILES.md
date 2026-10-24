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


## Fleet-wide automated crash/freeze recovery and Discord confirmation (PR #77)

**Recovery contract:** MT5 account 1 (broker DEMO), account 2 (Kyle), and account 3 (Dan) are separate systemd-managed terminals. For every commissioned account:

1. **Unexpected MT5 process exit:** its own \`dkcapital-mt5-N.service\` has \`Restart=always\`; other accounts and containers are not restarted. Binary approval failure / LiveUpdate exit code 78 must not be bypassed without reviewing the binary.
2. **Frozen but running terminal:** the opt-in \`mt5_recovery_supervisor.py\` reads the last authenticated account-specific telemetry. It restarts **only that account** after >150s stale reports plus 180s persistence, with a 15-minute cooldown and at most two attempts in an hour.
3. **Broker protection / unknown exposure:** the supervisor requires matching broker login, mode, currency, intentional EA entry toggle, native SL on every reported position, reconciled account-wide position/order counts and no unmanaged orders/positions. Pending orders must be owned, have valid native stops and reconcile to the broker snapshot. Upstream gateway outages and unapproved terminal binaries **never trigger terminal restarts**.
4. **Live accounts:** last-known protected broker orders can survive terminal downtime. Restart is allowed only if the profile explicitly opts in to \`auto_recover=true\`, \`recovery_policy="broker_protected"\`, **and its authenticated EA telemetry includes \`restart_reconciliation_supported=true\`**. That capability must originate from a **tested EA implementation that re-reads broker positions/pending orders and history before permitting fresh entries**. The demo-only V2.07 EA DOES NOT implement or emit this live-account marker. Do not invent it in a JSON file or work around the gate to enable Dan's future live account.
5. **Broker position reconciliation on reconnect:** before accepting new trade instructions, the commissioned EA must verify login, protective native SL, all active positions/pending orders including open group magics, history-based prior attempt IDs, previously filled TP1 and broker/deal events, and successfully reconcile the feed. Missing/ambiguous evidence must block **new orders** without attempting to close broker-protected positions. Restarting MT5 does not itself submit a trade.
6. **Independent recovery confirmation:** the supervisor looks for fresh after-restart telemetry under the correct account identity with working HTTP 200 feed and verified broker protection. Missing verification after 150s generates a distinct \`WAITING_FOR_RECONNECT\` alert. Each attempted restart / failed restart / recovered state is kept as a durable account-tagged event for the independent watchdog to deliver to \`MT5_ALERT_CHANNEL_ID\`, even if the Discord bot itself is down.
7. **Repeated failure or live unknown exposure:** alert and stop retrying until the cooldown or safe evidence returns. No script disables SL/TP, opens, alters or closes broker orders; each restart is only \`systemctl restart dkcapital-mt5-N.service\` for the approved account unit.

**Safety limitation:** these checks use the last authenticated EA report, which becomes stale during an outage; they cannot independently prove the broker's instantaneous state. Native stops that were successfully accepted by the broker typically remain active during MT5 downtime, but EA-managed breakeven/trailing and future actions cannot run while disconnected. An independently authenticated broker-state recovery feed would strengthen live safety; confirm account/broker behaviour with controlled restart tests before funding.

### Repo-to-VPS staged deployment

**Pull merged GitHub code and run CI/test verification first.** Existing MT5 accounts must not be restarted as part of installation. New recovery units do not automatically enable Accounts 2 or 3.

\`\`\`bash
cd /opt/dkcapital
git pull --ff-only origin main
python3 -m py_compile scripts/mt5_recovery_supervisor.py scripts/mt5_profile_audit.py
python3 -m unittest discover -s tests -p 'test_mt5_recovery*.py' -v
# Install the single per-account supervisor timer, not the old account 2/3 templates.
install -o root -g root -m 0644 deployment/dkcapital-mt5-recovery-supervisor.service /etc/systemd/system/
install -o root -g root -m 0644 deployment/dkcapital-mt5-recovery-supervisor.timer /etc/systemd/system/
systemctl daemon-reload
# Safe dry-run: no broker account is restarted.
python3 scripts/mt5_recovery_supervisor.py
\`\`\`

**Explicit account commissioning:** Start from \`deployment/mt5-monitor-profiles.example.json\`, replacing the invalid placeholder login with the *actual* known Account 1 broker login. Do not copy credentials into the repository. Place the validated registry at \`/app/data/mt5-monitor-profiles.json\` in the Docker named data volume (resolved via \`scripts/host_autoheal.py\`). Set \`auto_recover=true\` and \`recovery_policy="broker_protected"\` **only** on a tested and ready account; keep Kyle and Dan disabled until their authenticated telemetry routes are available. Live accounts must additionally emit the verified reconciliation capability marker.

The legacy Account 1 \`dkcapital-mt5-autoheal.timer\` and the new \`dkcapital-mt5-recovery-supervisor.timer\` must **never concurrently restart the same demo terminal**. Before opting Account 1 into the new timer, inspect running timers and deliberately stop/disable the old one; leave the legacy LiveUpdate guard in place and never bypass its approved-binary checks. Record state/attempt history before switching timers. With the new registry and account-specific telemetry confirmed:

\`\`\`bash
systemctl list-timers --all | grep -Ei 'dkcapital.*(autoheal|recovery)' || true
# Only after confirming the new monitor is configured and the demo has zero
# unreviewed broker positions / pending orders, migrate one timer at a time:
# systemctl disable --now dkcapital-mt5-autoheal.timer
# systemctl enable --now dkcapital-mt5-recovery-supervisor.timer
# systemctl start dkcapital-mt5-recovery-supervisor.service
# journalctl -u dkcapital-mt5-recovery-supervisor.service --no-pager -n 40
\`\`\`

The updated \`dkcapital-watchdog\` Docker container requires a **separately scheduled** rebuild after merge to receive new recovery event handling. The \`mt5-profile-audit\` read-only timer is required for independent systemd state alerts. Neither rebuild should restart the two MT5 terminals or alter EA trade switches.

### Acceptance tests per new broker account

- Unexpected process exit: systemd restarts *only* the affected MT5 terminal; recent restart logged and Discord alert delivered.
- Inject frozen telemetry in a controlled demo without real exposure: no restart until persistence threshold, exactly one restart, then healthy post-restart telemetry and Discord recovery notice.
- With protected demo broker positions and orders: restart without closing them; verify broker-native stop and live ticket IDs survive, and the EA does not duplicate trades or retroactively reprocess TP1/BE.
- With live account: verify an authenticated \`restart_reconciliation_supported\` implementation on **that broker/login**, plus correct mode/currency/hedging and zero unmanaged exposure before enabling opt-in. Wrong login, missing native SL, unknown pending orders or changed terminal binary must block automatic restart and alert.
- Unplug gateway/Telegram: no mistaken MT5 restart for upstream failure. Reconnect and verify EA fetches new feed; no old signal replay.
- One account down: other MT5/VNC profiles remain stable, P&L stays independently attributed, and the alert includes the correct account ID.

**Deployment status:** code and tests can be merged without installing timers or restarting terminals. The two existing MT5 terminals keep their current services and EAs. The new live-account recovery contract remains unactivated until Dan's Vantage account is commissioned, and the existing demonstration account keeps its working guarded recovery in the meantime.


## V2.08 take-profit allocation: TP2 receives largest share

The existing GBP demo-only EA source `mt5/DKcapitalV2.mq5` is updated to version **2.08**. These are *targets*, not guaranteed exact volumes: the broker's symbol-specific minimum volume and lot step govern order sizing. **Do not copy or attach the updated EA to account 1 until compiled in the isolated MetaEditor, verified for zero errors/warnings, hash-recorded, and demo tested.** No source-code merge deploys a compiled EX5 automatically.

| Provider instructions | Native broker exit legs | Intended exit volumes | Minimum 0.01-lot steps example |
| --- | --- | --- | --- |
| Numeric TP1 and TP2, no TP3 | Leg 1 → TP1; legs 2 and 3 → TP2 | 25% at TP1 / 75% at TP2 | 0.03 lots: 0.01 TP1 and 0.02 TP2 (33%/67%) |
| Numeric TP1, TP2 and TP3 | Leg 1 → TP1; leg 2 → TP2; leg 3 → TP3 | 25% / 50% / 25% | 0.04 lots: 0.01 / 0.02 / 0.01 (25%/50%/25%) |
| Explicit `TP3: OPEN` | Leg 3 open runner, **only** with existing separately approved `EnableOpenRunnerEntries` | 25% / 50% / 25% plus runner policy | Unchanged opt-in restrictions |
| Fewer than two valid numeric targets | No entries | No synthetic targets | Safely skip |

For three numeric targets, if the trade cannot fund a **strictly larger TP2 exit** than either TP1 or TP3 using three broker-native legs at the group/signal risk cap, **skip the new trade** rather than silently revert to equal 1/1/1 distribution. On 0.01 min lots, this requires at least 0.04 lots in the trio; provider SL, margin and Friday rules can reduce permissible size. On two-target signals, three minimum broker legs still work because the third leg exits at TP2. The EA records this two-target assignment in MT5 terminal global variables before submitting any broker order so that terminal restarts and later signal amendments do not silently reassign that TP2 leg to a later TP3.

Broker-native SL on every leg, TP1 broker-confirmed BE rules, distinct magic numbers, exactly-once broker history checks, risk cap at the **actual provider SL**, strict entry freshness and the existing optional GWS STORM / OPEN rules remain in place. This update **does not** change the independent live-account EA. Do not use V2.08 on a USD/REAL Vantage account; the EA intentionally checks Account 1 GBP DEMO identity.

**Verification before arming V2.08:** test a fresh GWS `XAUUSD 4003 - 4000: Buy / TP1: 4107 / TP2: 4120 / SL: 3996`-format *synthetic* message in a controlled demo environment, confirm the 12-field DKDEMO2 wire format carries a numeric zero for absent TP3 (not OPEN), verify two TP2 broker tickets and one TP1 broker ticket at acceptable lot sizes, confirm TP1 close triggers BE only on broker-side confirmed fill, inspect broker ticket SL/TP and total planned risk, and restart the terminal with protected tickets to ensure TP mapping survives. Never backfill or auto-open the historical example as a new trade.
