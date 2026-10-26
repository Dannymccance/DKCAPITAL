# Dan Account 3: guarded known-working MT5 terminal compatibility test

**Finding, 26 October 2026.** The newly installed Dan executable has SHA-256
46b37c22cf9adf903d44ffb5590b892eeff1e5a6a5a852ebef95c6edd91f60be and has shown
"debugger found" in Wine 9. Account 1's previously validated build has SHA-256
2e3c9aea103e7f4444bb917454b0a740372400c7f7a6c0e0d36926e81098b9a1.
Account 1 is currently trading under a separate Linux identity; Account 2 is also active.
A Linux systemd 'active' result is NOT proof that an MT5 GUI or broker session is healthy.

**Scope:** Use one previously validated executable from Account 1 as a controlled compatibility
candidate for Dan's fresh and not-yet-broker-bound Wine prefix. This does not
copy broker profiles, logins, sessions, config, EAs or other MT5 files. Back up Dan's
current terminal64.exe. Stop Dan's MT5 and VNC only, verify no Dan Wine processes
or X display remain, then install candidate and pin only Dan's separate launcher.
Never replace or restart the running Account 1/2 launchers or Wine installations.

After merging reviewed PR with passing CI, on VPS as root:

    cd /opt/dkcapital
    git fetch origin main
    git merge --ff-only origin/main
    bash scripts/mt5_account3_known_good.sh audit

Then stop only Account 3:

    systemctl stop dkcapital-vnc3.service
    systemctl stop dkcapital-mt5-3.service
    systemctl is-active dkcapital-mt5-1.service dkcapital-mt5-2.service
    bash scripts/mt5_account3_known_good.sh stage

If the guarded stage blocks because Dan-owned Wine processes linger, investigate rather
than killing shared Wine processes. The stage backs up Dan's newer executable in
/var/lib/dkcapital-mt5-3/backups/, makes an atomic Account3 executable swap,
installs the reviewed independent /usr/local/bin/dkcapital-mt5-run-account3 launcher,
and pins the approved SHA in /etc/dkcapital/mt5-account3-approved.sha256.
It does not start any service or enable trading. The launcher validates the pinned
binary at startup and around LiveUpdate; an unauthorized update stops with
exit code 78, guarded by Account3's systemd RestartPreventExitStatus=78.
The /skipupdate argument is best effort and undocumented; it is not a guarantee.

Only after a successful stage, start **Dan's** services:

    systemctl reset-failed dkcapital-mt5-3.service dkcapital-vnc3.service
    systemctl start dkcapital-mt5-3.service
    systemctl start dkcapital-vnc3.service
    systemctl is-active dkcapital-mt5-1.service dkcapital-mt5-2.service dkcapital-mt5-3.service dkcapital-vnc3.service
    journalctl -u dkcapital-mt5-3.service -n 60 --no-pager
    sha256sum '/var/lib/dkcapital-mt5-3/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe'

Use existing Windows SSH tunnel localhost:5904 to inspect Dan's terminal visually.
An old MT5 executable might still fail to start due to other updated files,
or might be refused by the broker as unsupported. **Do not attach any EA or
enter a real brokerage account until the clean terminal GUI is verified**.
Do not run apt upgrades, copy complete Wine prefixes or disable checksum gates.

Rollback while Account 3 and its VNC are stopped and no Dan Wine process remains:

    bash scripts/mt5_account3_known_good.sh rollback

The rollback restores the original Dan executable from checksum-verified backup
and removes Dan's approval pin, leaving Account 3 unable to auto-launch with
unapproved binaries. It does not touch Accounts 1 or 2.
