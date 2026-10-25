#!/usr/bin/env bash
# Guarded V2.10 release. NEVER restart trading terminals or enable entries.
set -euo pipefail
umask 077
ROOT=/opt/dkcapital
SRC="$ROOT/mt5/DKcapitalV2.mq5"
NAME=DKcapitalV210_TP1to6_SignFix1
EXPERTS="/opt/dkcapital-mt5-compiler/workspace/MetaTrader 5/MQL5/Experts"
DEMO="/var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Experts"
MQ5="$EXPERTS/$NAME.mq5"
EX5="$EXPERTS/$NAME.ex5"
ARTIFACT="/opt/dkcapital-mt5-compiler/artifacts/$NAME.ex5"
TARGET="$DEMO/$NAME.ex5"
COMMAND="${1:-status}"
fail() { echo "BLOCKED: $*" >&2; exit 2; }
root_only() { [[ "$(id -u)" = 0 ]] || fail "Run as root on the VPS"; }
source_check() {
 [[ -s "$SRC" ]] || fail "missing reviewed source"
 grep -qx '#property version "2.10"' "$SRC" || fail "source is not V2.10"
 grep -q 'DKDEMO4' "$SRC" || fail "wrong EA feed handshake"
 grep -q 'const int TP6_LEG = 7;' "$SRC" || fail "TP6 native broker magic missing"
 grep -Fq 'magic%10<=(ulong)TP6_LEG;' "$SRC" || fail "unsigned broker magic fix missing"
 [[ "$(git -C "$ROOT" branch --show-current)" == main ]] || fail "VPS checkout not on main"
 git -C "$ROOT" diff --quiet HEAD -- mt5/DKcapitalV2.mq5 || fail "EA source uncommitted"
 git -C "$ROOT" diff --cached --quiet -- mt5/DKcapitalV2.mq5 || fail "EA source staged locally"
}
# With multiple unit names, systemctl is-active can return success when
# *any one* unit is active. Check the actual state of EACH MT5 unit.
terminals() {
 local service state failures=0
 for service in dkcapital-mt5-1 dkcapital-mt5-2; do
   state="$(systemctl is-active "$service" 2>/dev/null || true)"
   printf '%s: %s\n' "$service" "$state"
   if [[ "$state" != "active" ]]; then failures=1; fi
 done
 [[ "$failures" == 0 ]] ||
   fail "one or more MT5 terminals are not active; inspect systemctl status and journalctl before continuing"
}
flat_disarmed() {
 docker compose --profile mt5-shadow exec -T mt5-gateway python - <<'PY'
import json
from pathlib import Path
from datetime import datetime, UTC
x=json.loads(Path("/app/data/mt5-account1.json").read_text())
age=(datetime.now(UTC)-datetime.fromisoformat(x["received_at"].replace("Z","+00:00"))).total_seconds()
checks={
 "fresh broker telemetry":0<=age<120,
 "GBP DEMO login 5057220041":x.get("mode")=="DEMO" and x.get("currency")=="GBP" and x.get("login")==5057220041,
 "new entries disabled":x.get("entry_enabled") is False,
 "zero broker positions":x.get("broker_positions_total")==0,
 "zero pending orders":x.get("broker_orders_total")==0,
 "broker stops protected":x.get("broker_positions_protected") is True,
 "broker feed 200 or old-gateway 404 during V4 bootstrap":x.get("feed_http") in (200,404),
}
for key,ok in checks.items(): print(("PASS" if ok else "BLOCK"),key)
if not all(checks.values()): raise SystemExit("STOP: disarm EA on 5901, confirm flat and fresh")
PY
}
backend_probe() {
 docker compose --profile mt5-shadow exec -T signal-processor python - <<'PY'
import inspect
import dkcapital.signal_parser as p
s=inspect.getsource(p)
for marker in ("def _explicit_gold_entry","tp3_open","defer_breakeven"):
 if marker not in s: raise SystemExit("BLOCK: stale parser "+marker)
print("PASS latest GWS/OPEN/breakeven parser")
PY
 docker compose --profile mt5-shadow exec -T mt5-gateway python - <<'PY'
import os
from urllib.request import Request,urlopen
token=os.environ.get("MT5_GATEWAY_TOKEN")
if not token: raise SystemExit("BLOCK: gateway token absent")
r=Request("http://127.0.0.1:8765/v4/demo/feed",headers={"Authorization":"Bearer "+token})
with urlopen(r,timeout=8) as result:
 lines=result.read(300000).decode().splitlines()
 if result.status!=200 or not lines or lines[0]!="DKDEMO4":
  raise SystemExit("BLOCK: invalid V4 gateway")
 print("PASS authenticated DKDEMO4 feed; entries:",sum(1 for l in lines[1:] if l.startswith("S|") and "|ACTIVE|" in l))
PY
}
case "$COMMAND" in
 status)
  source_check
  echo "=== V2.10 reviewed main ==="; git -C "$ROOT" rev-parse --short HEAD
  sha256sum "$SRC"
  ls -lh "$MQ5" "$EX5" "$ARTIFACT" "$TARGET" 2>/dev/null || true
  terminals
  ;;
 stage-compiler)
  root_only;source_check;terminals
  [[ -d "$EXPERTS" ]] || fail "isolated compiler workspace missing (VNC 5903)"
  [[ ! -e "$MQ5" && ! -e "$EX5" && ! -e "$ARTIFACT" ]] || fail "candidate exists; no overwrite"
  install -o 11050 -g 11050 -m 0644 "$SRC" "$MQ5"
  cmp -s "$SRC" "$MQ5" || fail "compiler source differs"
  echo "STAGED: $MQ5"
  echo 'On VNC 5903 open Z:\workspace\MetaTrader 5\MQL5\Experts\DKcapitalV210_TP1to6_SignFix1.mq5'
  echo "Press F7, verify 0 errors AND 0 warnings; Account 1 is untouched"
  ;;
 verify-compiler)
  root_only;source_check
  [[ -s "$MQ5" && -s "$EX5" ]] || fail "compile via isolated MetaEditor VNC 5903"
  cmp -s "$SRC" "$MQ5" || fail "source differs from GitHub"
  [[ ! -L "$EX5" && ! -L "$MQ5" ]] || fail "symlink candidate blocked"
  [[ "$EX5" -nt "$MQ5" ]] || fail "compiled artifact predates source"
  [[ "$(stat -c %s "$EX5")" -gt 30000 ]] || fail "compiled artifact suspiciously small"
  install -d -m 0750 "/opt/dkcapital-mt5-compiler/artifacts"
  if [[ -e "$ARTIFACT" ]]; then
    cmp -s "$ARTIFACT" "$EX5" || fail "artifact already exists and differs"
  else
    install -m 0640 "$EX5" "$ARTIFACT"
  fi
  sha256sum "$SRC" "$MQ5" "$EX5" "$ARTIFACT"
  echo "Source parity/binary confirmed; MetaEditor errors/warnings require visual confirmation."
  echo "No broker EA installed."
  ;;
 check-backend)
  root_only;terminals;backend_probe
  ;;
 deploy-backend)
  root_only;source_check
  cd "$ROOT"
  terminals;flat_disarmed
  docker compose --profile mt5-shadow exec -T mt5-gateway python - <<'PY'
from pathlib import Path
from datetime import datetime, UTC
import shutil
root=Path("/app/data")
dest=root/"backups"/("pre-v210-"+datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
dest.mkdir(parents=True,exist_ok=False)
for name in ("telegram-events.jsonl","signal-state.json","mt5-account1.json"):
 p=root/name
 if p.is_file(): shutil.copy2(p,dest/name)
print("Backed up to",dest)
PY
  docker compose --profile mt5-shadow build signal-processor mt5-gateway
  flat_disarmed
  docker compose --profile mt5-shadow up -d --no-deps --no-build signal-processor mt5-gateway
  backend_probe
  echo "V4 gateway/parser running; both MT5 terminals and their EAs untouched"
  ;;
 stage-demo)
  # Copying an approved EX5 to Experts does not attach or run it.
  # Allow staging before a telemetry-producing EA is attached. The EA
  # must be attached with EnableNewEntries=false and produce fresh,
  # authenticated GBP DEMO broker telemetry before backend deployment.
  root_only;source_check;terminals
  [[ "${METAEDITOR_ERRORS:-unset}" == 0 && "${METAEDITOR_WARNINGS:-unset}" == 0 ]] ||
    fail "Verify GUI shows 0 errors/0 warnings, then explicitly set METAEDITOR_ERRORS=0 METAEDITOR_WARNINGS=0"
  [[ -s "$EX5" && -s "$ARTIFACT" ]] || fail "compiled artifact missing"
  cmp -s "$SRC" "$MQ5" || fail "MQ5 source not approved"
  cmp -s "$EX5" "$ARTIFACT" || fail "EX5 artifact mismatch"
  [[ -d "$DEMO" && ! -e "$TARGET" ]] || fail "demo Experts folder missing or candidate already installed"
  install -o dkmt5 -g dkmt5 -m 0644 "$ARTIFACT" "$TARGET"
  cmp -s "$ARTIFACT" "$TARGET" || fail "demo binary hash mismatch"
  sha256sum "$ARTIFACT" "$TARGET"
  echo "Candidate STAGED on demo filesystem ONLY. Nothing attached, restarted, or enabled."
  echo "On 5901: confirm GBP DEMO login and zero exposure in Trade tab; refresh Navigator and attach candidate with EnableNewEntries=false."
  echo "Old gateway may respond 404 to /v4/demo/feed. Entry remains disabled; new EA must still POST fresh authenticated telemetry for the backend preflight."
  ;;
 *) echo "Usage: $0 {status|stage-compiler|verify-compiler|check-backend|deploy-backend|stage-demo}" >&2;exit 2;;
esac
