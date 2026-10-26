#!/usr/bin/env bash
# Read-only DK Capital three-profile preflight. No MT5 process or broker action.
set -euo pipefail
cd /opt/dkcapital
printf '=== GIT ===\n'
git rev-parse --short HEAD
git status --short
printf '\n=== SYSTEMD / VNC ===\n'
for account in 1 2 3; do
  state="$(systemctl is-active "dkcapital-mt5-$account.service" 2>/dev/null || true)"
  printf 'account=%s service=%s\n' "$account" "$state"
done
for label in 1 2 3; do
  state="$(systemctl is-active "dkcapital-vnc$label.service" 2>/dev/null || true)"
  printf 'vnc=%s service=%s\n' "$label" "$state"
done
printf '\n=== WINE INSTALLATION / COMPILER ===\n'
for item in \
  "/var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5" \
  "/var/lib/dkcapital-mt5-2/.wine/drive_c/Program Files/MetaTrader 5" \
  "/var/lib/dkcapital-mt5-3/.wine/drive_c/Program Files/MetaTrader 5" \
  "/opt/dkcapital-mt5-compiler/workspace/MetaTrader 5/MQL5/Experts"
do
  [[ -d "$item" ]] && status=present || status=missing
  printf '%s: %s\n' "$item" "$status"
done
printf '\n=== BROKER REPORTED SNAPSHOTS (READ ONLY) ===\n'
docker compose --profile mt5-shadow exec -T mt5-gateway python - <<'PY'
import json
from pathlib import Path
from datetime import datetime, UTC
for account_id in (1, 2, 3):
    path = Path(f"/app/data/mt5-account{account_id}.json")
    if not path.exists():
        print(f"Account {account_id}: NO AUTHENTICATED BROKER SNAPSHOT")
        continue
    try:
        d = json.loads(path.read_text())
        stamp = datetime.fromisoformat(str(d["received_at"]).replace("Z", "+00:00"))
        age = (datetime.now(UTC) - stamp).total_seconds()
        print(
            f"Account {account_id}: login={d.get('login')} mode={d.get('mode')} "
            f"currency={d.get('currency')} server={d.get('server')} "
            f"age_s={age:.0f} positions={d.get('broker_positions_total')} "
            f"orders={d.get('broker_orders_total')} "
            f"protected={d.get('broker_positions_protected')} "
            f"feed_http={d.get('feed_http')} "
            f"entries={d.get('entry_enabled')}"
        )
        if age < 0 or age >= 120:
            print(f"Account {account_id}: STALE, not suitable for release authorization")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Account {account_id}: INVALID SNAPSHOT: {type(exc).__name__}")
PY
printf '\nPreflight only. NO EA compiled, attached, enabled, restarted or deployed.\n'
