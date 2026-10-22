#!/usr/bin/env bash
set -euo pipefail
export DISPLAY=:99
export WINEPREFIX=/home/compiler/.wine
export HOME=/home/compiler

Xvfb :99 -screen 0 1280x800x24 -nolisten tcp > /workspace/xvfb.log 2>&1 &
X_PID=$!
trap 'kill "$X_PID" 2>/dev/null || true' EXIT
ready=0
for _ in $(seq 1 40); do
  if xdpyinfo -display :99 > /dev/null 2>&1; then ready=1; break; fi
  sleep 0.25
done
if [[ "$ready" != 1 ]]; then
  echo "Compiler Xvfb did not start"; cat /workspace/xvfb.log; exit 1
fi
x11vnc -display :99 -rfbport 5903 -forever -shared -nopw -noxdamage -quiet \
  > /workspace/vnc.log 2>&1 &
VNC_PID=$!

echo "=== ISOLATED COMPILER ==="
wine --version
echo "This environment has no live MT5 account or production Wine prefix."
echo "Connect to SSH-forwarded 127.0.0.1::5903."

if ! wineboot -u > /workspace/wineboot.log 2>&1; then
  echo "Wine prefix startup failed. See /workspace/wineboot.log."
else
  cd "/workspace/MetaTrader 5"
  wine "./MetaEditor64.exe" /portable > /workspace/metaeditor.log 2>&1 &
  echo "MetaEditor process spawned (PID $!). Check VNC and /workspace/metaeditor.log."
fi

# Leave VNC available even if MetaEditor refuses to start. Never touch live terminals.
wait "$VNC_PID"
