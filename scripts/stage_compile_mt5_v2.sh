#!/usr/bin/env bash
# Prepare a proposed EA for GUI compilation under Account 1's portable Wine MT5.
# The supported workflow on this VPS is MetaEditor GUI -> F7, not CLI /compile.
# Does not replace the current EA, invoke Wine, restart MT5 or place orders.
set -euo pipefail

REPO=/opt/dkcapital
ROOT="/var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5"
EXPERTS="$ROOT/MQL5/Experts"
LIVE="$EXPERTS/DKcapitalV2.ex5"

[ "$(id -u)" -eq 0 ] || { echo "Run as root for staged ownership."; exit 2; }
[ -f "$LIVE" ] || { echo "Missing installed EA."; exit 2; }
REF="$(git -C "$REPO" rev-parse --verify --quiet 'FETCH_HEAD^{commit}')"
[ -n "$REF" ] || { echo "Fetch PR #56 head first."; exit 2; }

STAGE_NAME="_stage_pr56_$(date -u +%Y%m%dT%H%M%SZ)"
STAGE="$EXPERTS/$STAGE_NAME"
install -d -o dkmt5 -g dkmt5 -m 0700 "$STAGE"
git -C "$REPO" show "$REF:mt5/DKcapitalV2.mq5" > "$STAGE/DKcapitalV2.mq5"
chown dkmt5:dkmt5 "$STAGE/DKcapitalV2.mq5"
chmod 0600 "$STAGE/DKcapitalV2.mq5"

echo "Prepared source revision: $REF"
echo "Staged source: $STAGE/DKcapitalV2.mq5"
echo "Active EA: $LIVE (NOT CHANGED)"
echo
echo "Open the Account 1 TigerVNC desktop (localhost::5901 via SSH tunnel)."
echo "Press F4 in MetaTrader to open MetaEditor if not already open."
echo "Choose File > Open, and enter:"
printf 'C:\\Program Files\\MetaTrader 5\\MQL5\\Experts\\%s\\DKcapitalV2.mq5\n' "$STAGE_NAME"
echo "Press F7 to compile the STAGED FILE and confirm zero compiler errors."
echo "Expected compiled output: $STAGE/DKcapitalV2.ex5"
echo "DO NOT overwrite the active EA or restart either MT5 terminal yet."
