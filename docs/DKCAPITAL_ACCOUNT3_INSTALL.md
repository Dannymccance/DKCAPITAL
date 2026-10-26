# Account 3 (Dan): isolated MT5 first-time host commissioning

The Account 1 demo is running V2.11 and Account 2 (Kyle) is running a separate terminal. The third Wine prefix is missing. **Never clone** Account 1/2 home, Wine prefix, broker login, MetaTrader config, VNC password or EA binaries to Account 3.

This procedure first creates Dan's *own* Linux user, private home and service definitions. It does not install the MT5 platform, authenticate to a broker or attach/enable any trading EA. Account 3 uses user dkmt5c, Wine prefix /var/lib/dkcapital-mt5-3/.wine, X display :152, and VNC over SSH-only localhost port 5904. The isolated compiler is separately on port 5903.

## A. Review and prepare host safely

After reviewed PR is merged with passing CI, run as root on VPS:

    cd /opt/dkcapital
    git fetch origin main
    git merge --ff-only origin/main
    bash scripts/prepare_mt5_account3.sh audit
    bash scripts/prepare_mt5_account3.sh prepare

The script deliberately does not start or enable a single service. It fails if another account is not active, Account 3 service/display/VNC is already running, the shared existing launcher lacks Account 3 support, there is an unexpected home owner, or any proposed installed unit conflicts with an existing file. Do not bypass failures or overwrite a different installed unit. No apt, Wine or Ubuntu upgrades are made.

Set the Account 3 **independent private VNC password** interactively without printing it in chat:

    runuser -u dkmt5c -- env HOME=/var/lib/dkcapital-mt5-3 x11vnc -storepasswd

The expected output path is /var/lib/dkcapital-mt5-3/.vnc/passwd. Ensure it exists, is owned by dkmt5c and is private. Then start *only the temporary account3 setup display* and private VNC (the MT5 trading service remains stopped):

    systemctl start dkcapital-mt5-3-installer-display.service
    systemctl start dkcapital-vnc3.service
    systemctl is-active dkcapital-mt5-3-installer-display.service dkcapital-vnc3.service
    ss -ltnp | grep ':5904'

On Windows, keep a separate PowerShell SSH tunnel open:

    ssh -N -L 5904:127.0.0.1:5904 root@31.187.72.71

Connect TigerVNC to 127.0.0.1::5904 with the new password. Never expose port 5904 publicly.

## B. Install MetaTrader 5 in the new Wine prefix only

Use an authentic, current MetaTrader 5 installer obtained directly from MetaQuotes or Dan's broker. MetaQuotes documents Wine-based Linux installation at https://www.metatrader5.com/en/terminal/help/start_advanced/install_linux . **Do not run its generic sudo/apt bootstrap script on this production VPS**, because the existing Wine/MT5 installations must remain undisturbed.

Put the trusted Windows installer at /var/lib/dkcapital-mt5-3/install/mt5setup.exe with owner dkmt5c, mode 0600. Record sha256sum and verify its provenance/signature as appropriate. If transferred as root, run:

    install -o dkmt5c -g dkmt5c -m 0600 /path/to/VERIFIED/mt5setup.exe /var/lib/dkcapital-mt5-3/install/mt5setup.exe
    sha256sum /var/lib/dkcapital-mt5-3/install/mt5setup.exe

Start the installer in the *temporary* account3 display, never as root:

    runuser -u dkmt5c -- env \
      HOME=/var/lib/dkcapital-mt5-3 \
      WINEPREFIX=/var/lib/dkcapital-mt5-3/.wine \
      DISPLAY=:152 \
      XAUTHORITY=/var/lib/dkcapital-mt5-3/.Xauthority \
      WINEDEBUG=-all \
      /usr/lib/wine/wine64 /var/lib/dkcapital-mt5-3/install/mt5setup.exe

Complete the GUI installation through the VNC screen. The installation target must be Account 3's own Wine prefix; do not use any other HOME/WINEPREFIX. Do not enter Dan's live broker credentials into the terminal yet; first verify a clean terminal install. After the installer exits, stop any auto-launched MT5 window normally through the UI. Check that no terminal64.exe still runs under dkmt5c:

    pgrep -u "$(id -u dkmt5c)" -af 'terminal64.exe|MetaEditor64.exe' || true
    test -s '/var/lib/dkcapital-mt5-3/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe'

If no process is left, hand display :152 over to the reviewed per-account MT5 launcher:

    systemctl stop dkcapital-vnc3.service
    systemctl stop dkcapital-mt5-3-installer-display.service
    systemctl start dkcapital-mt5-3.service
    systemctl start dkcapital-vnc3.service
    systemctl is-active dkcapital-mt5-1.service dkcapital-mt5-2.service dkcapital-mt5-3.service dkcapital-vnc3.service
    journalctl -u dkcapital-mt5-3.service -n 40 --no-pager
    bash scripts/mt5_v211_fleet_preflight.sh

Do not enable Account 3's MT5/VNC systemd services to auto-start until after a separate broker-safe recovery approval; do not install an EA or enable entries now.

## C. Broker binding, EA and gateway: separate acceptance phase

On VNC 5904, confirm Dan's actual broker login, DEMO/REAL mode, GBP/USD currency, exact broker server and hedging margin mode. Never paste password or private gateway tokens into a chat or GitHub. The approved Account 3 V2.11 generator can subsequently produce unique, **disarmed** Account 3 source after confirming the lot denominator in that account's currency. Compile in independent MetaEditor on 5903, require zero errors/warnings, then use stage_mt5_v211_profile.sh 3 to stage the binary. Only then configure distinct gateway ACCOUNT3 environment settings and token, attach EA on a flat broker account, verify HTTP 200 and authenticated Account3 snapshot, native TP1-TP6, 4% normal / 2% HIGH RISK stop-risk controls, SL edits and replay protections.

A REAL account is *not* approved for automated execution merely because MT5 connected. Broker-side stop protection, potential Vantage credits, mode/currency, contract size, minimum lots and risk calibration must all be tested independently first.
