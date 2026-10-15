#!/usr/bin/env bash
# Compile a PR's EA source in an isolated staging subdirectory.
# Never overwrites the active .mq5/.ex5, restarts MT5, or places orders.
set -euo pipefail

REF="$(git -C /opt/dkcapital rev-parse --verify --quiet FETCH_HEAD)"
[ -n "$REF" ] || { echo "ERROR: fetch the PR head first." >&2; exit 2; }
REPO=/opt/dkcapital
HOME_DIR=/var/lib/dkcapital-mt5
TERMINAL="$HOME_DIR/.wine/drive_c/Program Files/MetaTrader 5"
EXPERTS="$TERMINAL/MQL5/Experts"
EDITOR="$TERMINAL/MetaEditor64.exe"
ACTIVE_EX5="$EXPERTS/DKcapitalV2.ex5"

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root." >&2; exit 2; }
for f in "$EDITOR" "$ACTIVE_EX5" "$HOME_DIR/.Xauthority"; do
  [ -f "$f" ] || { echo "ERROR: missing file: $f" >&2; exit 2; }
done
[ -S /tmp/.X11-unix/X150 ] || {
  echo "ERROR: Account 1 display :150 is not available." >&2; exit 2;
}
STAGE_NAME="_stage_pr56_$(date -u +%Y%m%dT%H%M%SZ)"
STAGE="$EXPERTS/$STAGE_NAME"
install -d -o dkmt5 -g dkmt5 -m 0700 "$STAGE"
git -C "$REPO" show "$REF:mt5/DKcapitalV2.mq5" > "$STAGE/DKcapitalV2.mq5"
chown dkmt5:dkmt5 "$STAGE/DKcapitalV2.mq5"
chmod 0600 "$STAGE/DKcapitalV2.mq5"

echo "=== COMPILE PR SOURCE INTO ISOLATED STAGING FOLDER ==="
echo "Source revision: $REF"
echo "Staged file: $STAGE/DKcapitalV2.mq5"
echo "ACTIVE EA WILL NOT BE REPLACED: $ACTIVE_EX5"

SOURCE_WINE="C:\\Program Files\\MetaTrader 5\\MQL5\\Experts\\$STAGE_NAME\\DKcapitalV2.mq5"
INCLUDE_WINE="C:\\Program Files\\MetaTrader 5\\MQL5"
set +e
timeout --kill-after=5s 90s runuser -u dkmt5 -- env \
  HOME="$HOME_DIR" WINEPREFIX="$HOME_DIR/.wine" WINEDEBUG=-all \
  DISPLAY=:150 XAUTHORITY="$HOME_DIR/.Xauthority" \
  /usr/lib/wine/wine64 "$EDITOR" "/compile:$SOURCE_WINE" "/include:$INCLUDE_WINE" /log
RC=$?
set -e

python3 - "$STAGE/DKcapitalV2.log" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
if not p.exists():
    print("NO COMPILER LOG GENERATED")
else:
    raw=p.read_bytes()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        content=raw.decode("utf-16", errors="replace")
    elif raw[:100].count(b'\x00')>10:
        content=raw.decode("utf-16-le",errors="replace")
    else:
        content=raw.decode("utf-8",errors="replace")
    print("=== COMPILER LOG ===")
    print(content[-7500:])
PY

if [ "$RC" -ne 0 ]; then
  echo "ERROR: MetaEditor returned $RC; production EA unchanged." >&2
  exit 1
fi
if [ ! -s "$STAGE/DKcapitalV2.ex5" ]; then
  echo "ERROR: staged .ex5 not created; production EA unchanged." >&2
  exit 1
fi
echo "=== CHECKSUMS ==="
sha256sum "$STAGE/DKcapitalV2.ex5" "$ACTIVE_EX5"
echo "STAGED BUILD: $STAGE/DKcapitalV2.ex5"
echo "NO ACTIVE EA REPLACED. NO MT5 RESTARTED."
