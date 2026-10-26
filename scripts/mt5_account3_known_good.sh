#!/usr/bin/env bash
# Restore known-working MT5 binary to Dan's isolated prefix only; back up old.
set -euo pipefail
umask 077
cd /opt/dkcapital
MODE="$(echo "$@" | awk '{print $1}')"
[[ -n "$MODE" ]] || MODE=audit
GOOD=2e3c9aea103e7f4444bb917454b0a740372400c7f7a6c0e0d36926e81098b9a1
OLD=46b37c22cf9adf903d44ffb5590b892eeff1e5a6a5a852ebef95c6edd91f60be
SRC="/var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe"
DST="/var/lib/dkcapital-mt5-3/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe"
BACKUP="/var/lib/dkcapital-mt5-3/backups/terminal64-original-$OLD.exe"
PIN=/etc/dkcapital/mt5-account3-approved.sha256
LAUNCHER=/usr/local/bin/dkcapital-mt5-run-account3
REVIEWED=ops/mt5/dkcapital-mt5-run
fail() { echo "BLOCKED: $*" >&2; exit 2; }
[[ "$MODE" == audit || "$MODE" == stage || "$MODE" == rollback ]] || fail "Usage: audit|stage|rollback"
[[ "$(id -u)" == 0 ]] || fail "Run as root"
for n in 1 2; do
  [[ "$(systemctl is-active "dkcapital-mt5-$n.service" 2>/dev/null || true)" == active ]] || fail "Account $n inactive"
done
[[ -f "$SRC" && ! -L "$SRC" && -f "$DST" && ! -L "$DST" ]] || fail "Executable missing or symlinked"
source_hash="$(sha256sum "$SRC" | cut -d' ' -f1)"
dan_hash="$(sha256sum "$DST" | cut -d' ' -f1)"
echo "Account1 source SHA: $source_hash"
echo "Account3 current SHA: $dan_hash"
[[ "$source_hash" == "$GOOD" ]] || fail "Account1 is not the verified working build"
[[ -f "$LAUNCHER" && ! -L "$LAUNCHER" && -f "$REVIEWED" ]] || fail "Dedicated launcher missing"
grep -Fq 'ACCOUNT3_APPROVAL_FILE' "$REVIEWED" || fail "Reviewed launcher has no Account3 pin"
systemctl cat dkcapital-mt5-3.service | grep -Fq 'dkcapital-mt5-run-account3 3' || fail "Account3 launcher mismatch"
for n in 1 2; do
  if systemctl cat "dkcapital-mt5-$n.service" | grep -Fq "$LAUNCHER"; then
    fail "Account$n incorrectly uses Account3 launcher"
  fi
done
echo "Account3 service: $(systemctl is-active dkcapital-mt5-3.service 2>/dev/null || true)"
echo "Backup: $BACKUP"
[[ "$MODE" != audit ]] || exit 0
[[ "$(git branch --show-current)" == main ]] || fail "Only deploy reviewed main"
git diff --quiet HEAD -- "$REVIEWED" scripts/mt5_account3_known_good.sh || fail "Deployment source changed"
[[ "$(systemctl is-active dkcapital-mt5-3.service 2>/dev/null || true)" != active ]] || fail "Stop Account3 MT5 first"
[[ "$(systemctl is-active dkcapital-vnc3.service 2>/dev/null || true)" != active ]] || fail "Stop Account3 VNC first"
[[ "$(systemctl is-active dkcapital-mt5-3-installer-display.service 2>/dev/null || true)" != active ]] || fail "Installer display running"
[[ ! -e /tmp/.X152-lock && ! -S /tmp/.X11-unix/X152 ]] || fail "Display 152 still locked"
if pgrep -u dkmt5c -af 'terminal64.exe|metaeditor64.exe|wineserver|wine64|winedevice|explorer.exe|liveupdate' >/dev/null; then
  pgrep -u dkmt5c -af 'terminal64.exe|metaeditor64.exe|wineserver|wine64|winedevice|explorer.exe|liveupdate' || true
  fail "Account3 Wine processes remain"
fi
[[ ! -L "$BACKUP" && ! -L "$PIN" ]] || fail "Unexpected symlink"
if [[ "$MODE" == stage ]]; then
  [[ "$dan_hash" == "$OLD" ]] || fail "Unexpected Dan executable"
  [[ ! -e "$BACKUP" && ! -e "$PIN" ]] || fail "Existing backup or pin; no overwrite"
  install -d -o dkmt5c -g dkmt5c -m 0700 /var/lib/dkcapital-mt5-3/backups
  install -o dkmt5c -g dkmt5c -m 0700 "$DST" "$BACKUP"
  [[ "$(sha256sum "$BACKUP" | cut -d' ' -f1)" == "$OLD" ]] || fail "Backup mismatch"
  install -o root -g root -m 0755 "$REVIEWED" "$LAUNCHER.next"
  python3 -m py_compile "$LAUNCHER.next"
  mv -T "$LAUNCHER.next" "$LAUNCHER"
  install -o dkmt5c -g dkmt5c -m 0755 "$SRC" "$DST.next"
  [[ "$(sha256sum "$DST.next" | cut -d' ' -f1)" == "$GOOD" ]] || fail "Candidate mismatch"
  mv -T "$DST.next" "$DST"
  install -d -o root -g root -m 0755 /etc/dkcapital
  printf '%s\n' "$GOOD" > "$PIN.next"
  chmod 0644 "$PIN.next"
  mv -T "$PIN.next" "$PIN"
  echo "STAGED: Account3 only; original backed up and launcher pinned. No services started."
else
  [[ -f "$BACKUP" && "$(sha256sum "$BACKUP" | cut -d' ' -f1)" == "$OLD" ]] || fail "Original backup invalid"
  [[ "$dan_hash" == "$GOOD" ]] || fail "Candidate changed"
  install -o dkmt5c -g dkmt5c -m 0755 "$BACKUP" "$DST.next"
  mv -T "$DST.next" "$DST"
  [[ "$(sha256sum "$DST" | cut -d' ' -f1)" == "$OLD" ]] || fail "Rollback mismatch"
  rm -f -- "$PIN"
  echo "ROLLED BACK Account3 binary. Account3 remains stopped; pin removed."
fi