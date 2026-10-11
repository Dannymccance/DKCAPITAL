#!/usr/bin/env bash
# Single-purpose DKcapital Demo Account 1 preparation; never enables entries.
set -Eeuo pipefail
cd /opt/dkcapital
EA_DIR="/var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Experts"
SOURCE="$EA_DIR/DKcapitalV2.mq5"
BINARY="$EA_DIR/DKcapitalV2.ex5"

echo "=== UPDATE DKCAPITAL SOURCE ==="
git pull --ff-only origin main
git log -1 --oneline

echo "=== STAGE PROVIDER-ONLY SL EA FOR ACCOUNT 1 ==="
grep -q 'if(providerSL<=0 || !StopValid(buy,providerSL,q)' mt5/DKcapitalV2.mq5 ||
  { echo "ERROR: provider-only stop fix not present"; exit 3; }
test -d "$EA_DIR"
install -o dkmt5 -g dkmt5 -m 600 mt5/DKcapitalV2.mq5 "$SOURCE"
echo "Account 1 EA source installed."
echo "Important: MetaEditor F7 and reattach EA to chart are STILL REQUIRED."

echo "=== RESTART ONLY TELEGRAM INGESTION + DISCORD REPORTER ==="
docker compose --profile discord up -d --no-deps --build telegram-listener signal-processor discord-bot

echo "=== START INDEPENDENT WATCHDOG ==="
docker compose --profile mt5-shadow up -d --no-deps --build dkcapital-watchdog
echo "=== VERIFY OTHER CRITICAL COMPONENTS ==="
docker compose --profile mt5-shadow ps telegram-listener signal-processor discord-bot mt5-gateway dkcapital-watchdog
systemctl is-active dkcapital-mt5-1.service
echo "=== GATEWAY HEALTH ==="
curl --noproxy '*' -fsS --max-time 10 http://127.0.0.1:8765/health || true
echo

echo "=== TELEGRAM RECOVERY ==="
sleep 12
docker compose exec -T telegram-listener python - <<'PY'
import json
from pathlib import Path
p = Path('/app/data/telegram-recovery-status.json')
if not p.exists():
    print('PENDING: first recovery scan still running')
else:
    d = json.loads(p.read_text())
    print('healthy:', d.get('healthy'))
    print('last checked:', d.get('checked_at'))
    print('missing posts recovered:', d.get('recovered'))
    print('edits recovered:', d.get('amended'))
    print('scan source diagnostics:', d.get('sources'))
    print('error:', d.get('error'))
PY

echo "=== EA BINARY VERIFICATION ==="
if [ -f "$BINARY" ]; then
  stat -c 'EX5 compiled: %y (%s bytes)' "$BINARY"
else
  echo 'EX5 MISSING: compile source in MetaEditor F7'
fi
stat -c 'MQ5 source: %y (%s bytes)' "$SOURCE"
if [ ! -f "$BINARY" ] || [ "$SOURCE" -nt "$BINARY" ]; then
  echo 'ACTION REQUIRED: MetaEditor F7 + reload DKcapitalV2 on XAUUSD chart.'
fi

echo "=== NEXT STEP ==="
echo 'Do NOT assume the running EA changed: it must be compiled and reattached.'
echo 'Keep EnableNewEntries=false until new .ex5 is confirmed on Account 1.'
