#!/usr/bin/env bash
# Stage one individually compiled EA binary. Do NOT attach, arm or restart.
set -euo pipefail
umask 077
ACCOUNT="${1:-}"
case "$ACCOUNT" in
  2) USERNAME=dkmt5b; ROOT=/var/lib/dkcapital-mt5-2 ;;
  3) USERNAME=dkmt5c; ROOT=/var/lib/dkcapital-mt5-3 ;;
  *) echo "Usage: $0 2|3" >&2; exit 2 ;;
esac
[[ "$(id -u)" == 0 ]] || { echo 'BLOCK: requires root'; exit 2; }
[[ "${METAEDITOR_ERRORS:-unset}" == 0 && "${METAEDITOR_WARNINGS:-unset}" == 0 ]] ||
  { echo 'BLOCK: human verification of 0 errors AND 0 warnings required'; exit 2; }
COMPILER="/opt/dkcapital-mt5-compiler/workspace/MetaTrader 5/MQL5/Experts"
DEST="$ROOT/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Experts"
shopt -s nullglob
SOURCES=( "$COMPILER/DKcapitalV211_Account${ACCOUNT}_"*.mq5 )
[[ ${#SOURCES[@]} -eq 1 ]] || { echo 'BLOCK: expected exactly one compiler source'; exit 2; }
SOURCE="${SOURCES[0]}"
BINARY="${SOURCE%.mq5}.ex5"
TARGET="$DEST/$(basename "$BINARY")"
[[ -f "$SOURCE" && -s "$BINARY" && -d "$DEST" ]] ||
  { echo 'BLOCK: missing isolated source, compiled EX5 or terminal installation'; exit 2; }
[[ ! -L "$SOURCE" && ! -L "$BINARY" && ! -e "$TARGET" && ! -L "$TARGET" ]] ||
  { echo 'BLOCK: symlink or existing binary, never overwrite'; exit 2; }
[[ "$BINARY" -nt "$SOURCE" ]] || { echo 'BLOCK: EX5 older than source'; exit 2; }
[[ "$(stat -c %s "$BINARY")" -ge 4096 ]] ||
  { echo 'BLOCK: suspect compiled binary size'; exit 2; }
grep -Fq "COMPILED_ACCOUNT_ID = $ACCOUNT;" "$SOURCE" ||
  { echo 'BLOCK: wrong account identity'; exit 2; }
grep -Fq "DKACCOUNT${ACCOUNT}V5" "$SOURCE" ||
  { echo 'BLOCK: wrong feed contract'; exit 2; }
grep -Fq "EnableNewEntries = false;" "$SOURCE" ||
  { echo 'BLOCK: entry default not disarmed'; exit 2; }
grep -Fq "HighRiskSignalPct = 2.0;" "$SOURCE" ||
  { echo 'BLOCK: high-risk policy missing'; exit 2; }
# No broker commands, no service restarts and no EA chart attachment.
install -o "$USERNAME" -g "$USERNAME" -m 0644 "$BINARY" "$TARGET"
cmp -s "$BINARY" "$TARGET" || { echo 'BLOCK: staged binary mismatch'; exit 2; }
sha256sum "$SOURCE" "$BINARY" "$TARGET"
printf 'STAGED only in Account %s Experts. No EA attachment or trading enabled.\n' "$ACCOUNT"
