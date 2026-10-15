#!/usr/bin/env bash
# Stage PR #56 EA with a DISTINCT name so F7 cannot silently rebuild/overwrite
# the installed DKcapitalV2.ex5. Does not invoke MetaEditor or restart MT5.
set -euo pipefail
umask 077

REPO=/opt/dkcapital
EXPERTS="/var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Experts"
BACKUPS="/var/lib/dkcapital-mt5/ea-backups"
ACTIVE_MQ5="$EXPERTS/DKcapitalV2.mq5"
ACTIVE_EX5="$EXPERTS/DKcapitalV2.ex5"

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root." >&2; exit 2; }
[ -s "$ACTIVE_EX5" ] && [ -s "$ACTIVE_MQ5" ] || {
  echo "ERROR: installed EA source or binary missing. STOP." >&2; exit 2;
}
REF="$(git -C "$REPO" rev-parse --verify --quiet 'FETCH_HEAD^{commit}')"
[ -n "$REF" ] || { echo "ERROR: fetch PR #56 before running." >&2; exit 2; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CANDIDATE="DKcapitalV203_PR56_$STAMP"
STAGED_MQ5="$EXPERTS/$CANDIDATE.mq5"
STAGED_EX5="$EXPERTS/$CANDIDATE.ex5"
[ ! -e "$STAGED_MQ5" ] && [ ! -e "$STAGED_EX5" ] || {
  echo "ERROR: candidate collision. STOP." >&2; exit 2;
}
SOURCE="$(mktemp)"
trap 'rm -f "$SOURCE"' EXIT
git -C "$REPO" show "$REF:mt5/DKcapitalV2.mq5" > "$SOURCE"
grep -q '^#property version "2.03"$' "$SOURCE" || {
  echo "ERROR: wrong source version; expected v2.03." >&2; exit 2;
}
grep -q 'RiskReferenceBalanceGBP = 1000.0' "$SOURCE" || {
  echo "ERROR: proposed dynamic-risk source not present." >&2; exit 2;
}

# Back up the currently installed EX5 as it exists now. It may already have
# been overwritten by an accidental GUI F7; this is NOT the Oct 12 binary.
install -d -o dkmt5 -g dkmt5 -m 0700 "$BACKUPS"
install -o dkmt5 -g dkmt5 -m 0600 "$ACTIVE_EX5" \
  "$BACKUPS/DKcapitalV2-before-pr56-$STAMP.ex5"
install -o dkmt5 -g dkmt5 -m 0600 "$ACTIVE_MQ5" \
  "$BACKUPS/DKcapitalV2-before-pr56-$STAMP.mq5"
install -o dkmt5 -g dkmt5 -m 0600 "$SOURCE" "$STAGED_MQ5"

echo "=== NO ACTIVE EA FILE REPLACED, NO TERMINAL RESTARTED ==="
echo "Source commit: $REF"
echo "Compiled EA backup:"
echo "  $BACKUPS/DKcapitalV2-before-pr56-$STAMP.ex5"
echo "Candidate source:"
echo "  $STAGED_MQ5"
echo
echo "In MetaEditor (Account 1), choose File > Open and enter EXACTLY:"
printf 'C:\\Program Files\\MetaTrader 5\\MQL5\\Experts\\%s.mq5\n' "$CANDIDATE"
echo "Before F7, verify active editor tab says $CANDIDATE.mq5."
echo "Then F7 and verify 0 compilation errors."
echo "Expected separate binary (must exist after F7):"
echo "  $STAGED_EX5"
echo
echo "CRITICAL: Do not compile DKcapitalV2.mq5 or attach this candidate EA."
echo "Do not restart Account 1 or Account 2."
