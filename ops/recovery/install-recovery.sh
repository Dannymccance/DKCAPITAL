#!/usr/bin/env bash
# Install reboot recovery files without starting/stopping any running service.
# Must be invoked by an operator after provider maintenance and exposure review.
set -Eeuo pipefail
cd /opt/dkcapital

if (( $# != 1 )) || [[ "$1" != "--install" ]]; then
  echo "Usage: sudo bash ops/recovery/install-recovery.sh --install"
  echo "Enables future boot recovery only; does not restart running services."
  exit 2
fi
if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 2
fi
if [[ ! -f .env ]]; then
  echo "STOP: /opt/dkcapital/.env missing" >&2
  exit 2
fi
for unit in dkcapital-mt5-1.service dkcapital-mt5-2.service; do
  if ! systemctl cat "$unit" >/dev/null 2>&1; then
    echo "STOP: MT5 unit $unit not installed" >&2
    exit 2
  fi
done

for p in /var/lib/dkcapital-mt5/.vnc/passwd /var/lib/dkcapital-mt5-2/.vnc/passwd; do
  if [[ ! -s "$p" ]]; then
    echo "STOP: Set up the private VNC password file first: $p" >&2
    exit 2
  fi
done
for b in /usr/local/bin/dkcapital-mt5-run /usr/bin/docker /usr/bin/x11vnc; do
  if [[ ! -x "$b" ]]; then
    echo "STOP: required executable not found: $b" >&2
    exit 2
  fi
done

python3 -m py_compile ops/mt5/dkcapital-mt5-run ops/recovery/check_boot.py
python3 -m unittest discover -s tests -p 'test_mt5_update_supervisor.py' -q
python3 -m unittest discover -s tests -p 'test_reboot_audit.py' -q

stamp=$(date -u +%Y%m%d-%H%M%S)
backup="/var/backups/dkcapital-recovery/$stamp"
install -d -m 700 "$backup"
cp -p /usr/local/bin/dkcapital-mt5-run "$backup/dkcapital-mt5-run"
for unit in dkcapital-mt5-1.service dkcapital-mt5-2.service; do
  systemctl cat "$unit" > "$backup/$unit.current-definition"
done

install -o root -g root -m 755 ops/mt5/dkcapital-mt5-run /usr/local/bin/dkcapital-mt5-run
for unit in dkcapital-stack.service dkcapital-vnc1.service dkcapital-vnc2.service dkcapital-boot-audit.service dkcapital-boot-audit.timer; do
  install -o root -g root -m 644 "ops/recovery/$unit" "/etc/systemd/system/$unit"
done
for unit in dkcapital-mt5-1.service dkcapital-mt5-2.service; do
  install -d -m 755 "/etc/systemd/system/$unit.d"
  install -o root -g root -m 644 ops/recovery/mt5-restart.conf "/etc/systemd/system/$unit.d/10-reboot-recovery.conf"
done

systemctl daemon-reload
systemctl enable dkcapital-mt5-1.service dkcapital-mt5-2.service dkcapital-stack.service dkcapital-boot-audit.timer dkcapital-vnc1.service dkcapital-vnc2.service

echo "Boot recovery installed; backups: $backup"
echo "NO services were started or restarted."
echo "After host maintenance, perform a controlled Account 1 recovery and full reboot acceptance test."
