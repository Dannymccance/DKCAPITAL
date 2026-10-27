#!/usr/bin/env bash
# Stage a reviewed TP1 EX5 into Dan's Experts folder. Does NOT attach or arm.
set -euo pipefail
umask 077
[[ "$(id -u)" -eq 0 ]] || { echo 'BLOCK: root required'; exit 2; }
[[ "${METAEDITOR_ERRORS:-unset}" == 0 && "${METAEDITOR_WARNINGS:-unset}" == 0 ]] ||
  { echo 'BLOCK: MetaEditor must be verified at zero errors AND warnings'; exit 2; }

COMPILER="/opt/dkcapital-mt5-compiler/workspace/MetaTrader 5/MQL5/Experts"
NAME="DKcapitalTP1_Account3_REAL_USD_Private"
SOURCE="$COMPILER/$NAME.mq5"
EX5="$COMPILER/$NAME.ex5"
TARGET="/var/lib/dkcapital-mt5-3/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Experts/$NAME.ex5"

[[ -f "$SOURCE" && -f "$EX5" && ! -L "$SOURCE" && ! -L "$EX5" ]] ||
  { echo 'BLOCK: missing or symlinked compiler-only artifacts'; exit 2; }
[[ ! -e "$TARGET" && ! -L "$TARGET" ]] ||
  { echo 'BLOCK: a target already exists; never overwrite audited release'; exit 2; }
[[ "$EX5" -nt "$SOURCE" && "$(stat -c %s "$EX5")" -ge 4096 ]] ||
  { echo 'BLOCK: suspect or outdated EX5'; exit 2; }
grep -Fq 'COMPILED_ACCOUNT_ID = 3;' "$SOURCE" ||
  { echo 'BLOCK: wrong broker account profile'; exit 2; }
grep -Fq 'DKACCOUNT3V5' "$SOURCE" ||
  { echo 'BLOCK: wrong gateway feed protocol'; exit 2; }
grep -Fq 'input double MaxSignalRiskPct = 8.0;' "$SOURCE" ||
  { echo 'BLOCK: per-trade 8% input missing'; exit 2; }
grep -Fq 'input double MaxPortfolioRiskPct = 8.0;' "$SOURCE" ||
  { echo 'BLOCK: aggregate 8% input missing'; exit 2; }
grep -Fq 'TP1_V1' "$SOURCE" ||
  { echo 'BLOCK: TP1-specific risk telemetry version missing'; exit 2; }
grep -Fq 'input bool EnableNewEntries = false;' "$SOURCE" ||
  { echo 'BLOCK: default arming changed'; exit 2; }
[[ -d "$(dirname "$TARGET")" ]] ||
  { echo 'BLOCK: Account 3 Experts destination missing'; exit 2; }

# Stage only. No Wine process or MT5 service interaction. An existing live EA
# must manage its six broker orders until independently reconciled.
install -o dkmt5c -g dkmt5c -m 0600 "$EX5" "$TARGET"
cmp -s "$EX5" "$TARGET" ||
  { echo 'CRITICAL: staged EX5 differs from compiler artifact'; exit 3; }
sha256sum "$SOURCE" "$EX5" "$TARGET"
echo 'STAGED: compiled TP1 binary is present but NOT attached/armed; MT5 unchanged'
