#!/usr/bin/env bash
# Non-trading Kyle Account2 identity probe only. Never restart or arm an EA.
set -euo pipefail
umask 077
[[ "$(id -u)" == 0 ]] || { echo 'BLOCK: root VPS required' >&2; exit 2; }
[[ "${1:-}" == "prepare" || "${1:-}" == "stage" ]] && [[ "$#" == 1 ]] || {
  echo 'Usage: bash scripts/kyle_identity_probe.sh prepare|stage' >&2
  exit 2
}
cd /opt/dkcapital
SRC=/opt/dkcapital/mt5/DKcapitalAccountIdentityProbe.mq5
COMPILER='/opt/dkcapital-mt5-compiler/workspace/MetaTrader 5/MQL5/Scripts'
TERMINAL='/var/lib/dkcapital-mt5-2/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Scripts'
NAME=DKcapitalAccountIdentityProbe
if [[ "$1" == prepare ]]; then
  [[ -s "$SRC" && -d "$COMPILER" && ! -L "$COMPILER" && ! -L "$SRC" ]] || {
    echo 'BLOCK: missing verified probe or compiler Scripts directory' >&2; exit 2;
  }
  [[ ! -e "$COMPILER/$NAME.mq5" && ! -L "$COMPILER/$NAME.mq5" ]] || {
    echo 'BLOCK: compiler probe source already exists' >&2; exit 2;
  }
  install -o 11050 -g 11050 -m 0600 "$SRC" "$COMPILER/$NAME.mq5"
  cmp -s "$SRC" "$COMPILER/$NAME.mq5"
  echo "PREPARED: compile $NAME.mq5 in isolated compiler VNC 5903."
  echo "This is a read-only script, not an Expert Advisor."
else
  [[ "${METAEDITOR_ERRORS:-}" == 0 && "${METAEDITOR_WARNINGS:-}" == 0 ]] || {
    echo 'BLOCK: compiler results must be 0 errors and 0 warnings' >&2; exit 2;
  }
  [[ -s "$SRC" && -s "$COMPILER/$NAME.mq5" && -s "$COMPILER/$NAME.ex5" && -d "$TERMINAL" ]] || {
    echo 'BLOCK: compiled probe or Kyle Scripts directory unavailable' >&2; exit 2;
  }
  cmp -s "$SRC" "$COMPILER/$NAME.mq5" || {
    echo 'BLOCK: compiled script source differs from reviewed source' >&2; exit 2;
  }
  [[ "$COMPILER/$NAME.ex5" -nt "$COMPILER/$NAME.mq5" && ! -L "$COMPILER/$NAME.ex5" ]] || {
    echo 'BLOCK: compiled EX5 is stale or symlinked' >&2; exit 2;
  }
  [[ ! -e "$TERMINAL/$NAME.ex5" && ! -L "$TERMINAL/$NAME.ex5" ]] || {
    echo 'BLOCK: a probe already exists; no overwrites' >&2; exit 2;
  }
  install -o dkmt5b -g dkmt5b -m 0644 "$COMPILER/$NAME.ex5" "$TERMINAL/$NAME.ex5"
  cmp -s "$COMPILER/$NAME.ex5" "$TERMINAL/$NAME.ex5"
  echo 'STAGED: Kyle Account2 Scripts only. No MT5 restart, orders or EA changes.'
fi
