#!/usr/bin/env bash
# Only prepare Account 3 service definitions. No trading, OS upgrade or EA attachment.
set -euo pipefail
umask 077
cd /opt/dkcapital
mode="$(echo "$@" | awk '{print $1}')"
[[ -n "$mode" ]] || mode=audit
fail() { echo "BLOCKED: $*" >&2; exit 2; }
[[ "$mode" == audit || "$mode" == prepare ]] || fail "Usage: $0 audit|prepare"
[[ "$(id -u)" == 0 ]] || fail "Run as root"
for n in 1 2; do
 state="$(systemctl is-active "dkcapital-mt5-$n.service" 2>/dev/null || true)"
 echo "Existing MT5 $n: $state (untouched)"
 [[ "$state" == active ]] || fail "Existing account $n inactive"
done
[[ "$(systemctl is-active dkcapital-mt5-3.service 2>/dev/null || true)" != active ]] || fail "Account 3 already active"
[[ "$(systemctl is-active dkcapital-vnc3.service 2>/dev/null || true)" != active ]] || fail "Account 3 VNC active"
[[ ! -S /tmp/.X11-unix/X152 ]] || fail "Display :152 occupied"
[[ -x /usr/local/bin/dkcapital-mt5-run ]] || fail "MT5 launcher missing"
grep -q 'dkmt5c' /usr/local/bin/dkcapital-mt5-run || fail "Installed launcher has no account3 support"
for cmd in Xvfb x11vnc xauth runuser systemctl python3 openssl; do
 command -v "$cmd" >/dev/null || fail "Missing prerequisite: $cmd"
done
[[ -x /usr/lib/wine/wine64 ]] || fail "Wine64 missing; separate Wine review required"
home=/var/lib/dkcapital-mt5-3
if id dkmt5c >/dev/null 2>&1; then
 [[ "$(getent passwd dkmt5c | cut -d: -f6)" == "$home" ]] || fail "Home mismatch"
 [[ "$(id -gn dkmt5c)" == dkmt5c ]] || fail "Group mismatch"
 [[ "$(stat -c %U "$home")" == dkmt5c ]] || fail "Home owner mismatch"
else
 [[ ! -e "$home" ]] || fail "Home exists without owning user"
fi
check_target() {
 [[ -f "$1" && ! -L "$1" ]] || fail "Approved source missing: $1"
 if [[ -e "$2" || -L "$2" ]]; then
  [[ -f "$2" && ! -L "$2" ]] || fail "Unexpected target: $2"
  cmp -s "$1" "$2" || fail "Different existing target: $2"
 fi
}
check_target ops/mt5/dkcapital-mt5-3.service /etc/systemd/system/dkcapital-mt5-3.service
check_target ops/recovery/dkcapital-vnc3.service /etc/systemd/system/dkcapital-vnc3.service
check_target ops/mt5/dkcapital-mt5-3-installer-display.service /etc/systemd/system/dkcapital-mt5-3-installer-display.service
check_target ops/mt5/mt5-account3-installer-display /usr/local/bin/mt5-account3-installer-display
echo "AUDIT PASSED: no other account modified"
[[ "$mode" == prepare ]] || exit 0
[[ "$(git branch --show-current)" == main ]] || fail "Only merged main can be prepared"
git diff --quiet HEAD -- ops/mt5 ops/recovery/dkcapital-vnc3.service scripts/prepare_mt5_account3.sh || fail "Local deployment edits"
if ! id dkmt5c >/dev/null 2>&1; then
 useradd --system --user-group --create-home --home-dir "$home" --shell /usr/sbin/nologin dkmt5c
fi
install -d -o dkmt5c -g dkmt5c -m 0700 "$home" "$home/.vnc" "$home/install"
install -o root -g root -m 0755 ops/mt5/mt5-account3-installer-display /usr/local/bin/mt5-account3-installer-display
install -o root -g root -m 0644 ops/mt5/dkcapital-mt5-3.service /etc/systemd/system/dkcapital-mt5-3.service
install -o root -g root -m 0644 ops/recovery/dkcapital-vnc3.service /etc/systemd/system/dkcapital-vnc3.service
install -o root -g root -m 0644 ops/mt5/dkcapital-mt5-3-installer-display.service /etc/systemd/system/dkcapital-mt5-3-installer-display.service
systemctl daemon-reload
echo "PREPARED: Account 3 private user/home and inactive service definitions"
echo "Nothing installed into Wine, started, enabled or attached"