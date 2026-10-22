#!/usr/bin/env bash
# Build/run a separate MetaEditor VNC. NEVER stops/restarts a live MT5 service.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE="/var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5"
STAGED="$LIVE/MQL5/Experts/DKcapitalV205_SL_PR65_20261022T191833Z.mq5"
WORK="/opt/dkcapital-mt5-compiler/workspace"
INSTALL="$WORK/MetaTrader 5"
EA="DKcapitalV205_SL_PR65_20261022T191833Z"
IMAGE="dkcapital/mt5-compiler-wine-staging:pr65"
NAME="dkcapital-mt5-compiler-vnc"
COMMAND="${1:-status}"

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then echo "Run as root (existing VPS SSH session)." >&2; exit 1; fi
}
check_live() {
  echo "MT5 1: $(systemctl is-active dkcapital-mt5-1 || true)"
  echo "MT5 2: $(systemctl is-active dkcapital-mt5-2 || true)"
}
case "$COMMAND" in
  start)
    require_root
    command -v docker >/dev/null || { echo "Docker is required." >&2; exit 1; }
    docker info >/dev/null
    if docker ps -a --format '{{.Names}}' | grep -Fxq "$NAME"; then
      echo "Compiler container already exists. Use status or stop."; exit 1
    fi
    if ss -lnt | grep -Eq '(^|[[:space:]])127\.0\.0\.1:5903[[:space:]]'; then
      echo "Host localhost port 5903 is occupied; refusing to interfere."; exit 1
    fi
    test -s "$LIVE/MetaEditor64.exe"
    test -d "$LIVE/MQL5/Include/Trade"
    test -s "$STAGED"
    test -s "$ROOT/mt5/DKcapitalV2.mq5"
    cmp -s "$STAGED" "$ROOT/mt5/DKcapitalV2.mq5" || {
      echo "Staged EA differs from checked-in PR65 source. Refusing."; exit 1;
    }
    grep -q 'slAlreadyAtOrBeyondBE' "$STAGED"
    echo "=== PRECHECK: LIVE ACCOUNTS (READ ONLY) ==="
    check_live
    echo "=== SYSTEM CAPACITY ==="
    df -h /opt; free -h | head -3
    echo "=== CREATE COMPILER-ONLY WORKSPACE ==="
    install -d -m 0750 "$INSTALL/MQL5/Experts"
    cp "$LIVE/MetaEditor64.exe" "$INSTALL/"
    if [[ ! -d "$INSTALL/MQL5/Include" ]]; then
      cp -r "$LIVE/MQL5/Include" "$INSTALL/MQL5/Include"
    fi
    install -m 0644 "$ROOT/mt5/DKcapitalV2.mq5" "$INSTALL/MQL5/Experts/$EA.mq5"
    chown -R 11050:11050 "/opt/dkcapital-mt5-compiler"
    echo "=== VERIFIED CANDIDATE ==="
    sha256sum "$STAGED" "$INSTALL/MQL5/Experts/$EA.mq5"
    echo "=== BUILD ISOLATED WINE STAGING IMAGE ==="
    docker build -f "$ROOT/ops/mt5/isolated-compiler/Dockerfile" -t "$IMAGE" \
      "$ROOT/ops/mt5/isolated-compiler"
    echo "=== LAUNCH COMPILER (NO LIVE PREFIX MOUNTED) ==="
    docker run -d --name "$NAME" --init --memory=3g --cpus=2 --pids-limit=384 \
      --security-opt=no-new-privileges \
      -p 127.0.0.1:5903:5903 \
      --mount "type=bind,src=$WORK,dst=/workspace" \
      "$IMAGE"
    echo "Compiler VNC: SSH forward 5903 -> 5903; viewer 127.0.0.1::5903"
    echo "MT5 account sessions are not restarted or modified."
    ;;
  status)
    docker ps -a --filter "name=^/${NAME}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true
    docker logs --tail 25 "$NAME" 2>&1 || true
    if [[ -d "$INSTALL/MQL5/Experts" ]]; then
      ls -lh "$INSTALL/MQL5/Experts/$EA".* 2>/dev/null || true
    fi
    check_live
    ;;
  collect)
    require_root
    test -s "$INSTALL/MQL5/Experts/$EA.ex5" || {
      echo "No compiled PR65 EX5 found. Check MetaEditor F7 errors."; exit 1;
    }
    OUT="/opt/dkcapital-mt5-compiler/artifacts"
    install -d -m 0750 "$OUT"
    install -m 0640 "$INSTALL/MQL5/Experts/$EA.ex5" "$OUT/$EA.ex5"
    sha256sum "$OUT/$EA.ex5"
    ls -lh "$OUT/$EA.ex5"
    echo "Artifact retained ONLY in compiler artifacts. LIVE EA IS UNCHANGED."
    ;;
  stop)
    require_root
    docker stop "$NAME" >/dev/null 2>&1 || true
    docker rm "$NAME" >/dev/null 2>&1 || true
    echo "Only the isolated compiler container was stopped/removed."
    check_live
    ;;
  *)
    echo "Usage: $0 {start|status|collect|stop}" >&2
    exit 2
    ;;
esac
