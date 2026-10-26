# Account 2 (Kyle) — V2.11 installation and fleet acceptance

Goal: one DK Capital EA strategy, risk rules, signal feed, SL/TP management,
monitoring and recovery policy across Accounts 1, 2 and 3. Broker identity,
server, currency, trading symbol, private feed token, VPS user and account P&L
are necessarily isolated per account.

## Verify Kyle's existing MT5 terminal FIRST
- Account 2 runs as dkmt5b under /var/lib/dkcapital-mt5-2.
- VNC display :151, SSH-tunnel VNC port 5902; isolated compiler on 5903.
- In MT5 inspect login number, precise server, REAL/DEMO, USD/GBP, hedging mode,
  exact Market Watch gold symbol (XAUUSD or XAUUSD+), cash balance, equity,
  broker credit and all open positions/pending orders.
- Do not copy a logged-in Wine prefix, broker sessions, passwords or another
  account's EX5; keep all automatic entries OFF until validation passes.

Read-only VPS preflight:

    cd /opt/dkcapital
    bash scripts/mt5_v211_fleet_preflight.sh
    systemctl is-active dkcapital-mt5-1 dkcapital-mt5-2 dkcapital-mt5-3
    systemctl is-active dkcapital-vnc1 dkcapital-vnc2 dkcapital-vnc3
    sha256sum '/var/lib/dkcapital-mt5-2/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe'

Account2 currently uses a less restricted MT5 LiveUpdate policy than Accounts
1 and 3. Its known-working binary and recovery policy require separately
verified approval and controlled installation; do not change the current
terminal while it has live orders.

## Configure private Account2 broker identity in /opt/dkcapital/.env

    MT5_ACCOUNT2_LOGIN=<verified MT5 login>
    MT5_ACCOUNT2_MODE=<DEMO or REAL>
    MT5_ACCOUNT2_CURRENCY=<GBP or USD>
    MT5_ACCOUNT2_SERVER=<exact MT5 server>
    MT5_ACCOUNT2_TOKEN=<unique private 64-hex token>

Generate the new token locally using the OpenSSL random hex generator.
Never paste the token into ChatGPT or commit it to GitHub. Do not reuse an
Account1/Account3 login or token. Gateway validates uniqueness.

Recreate ONLY the MT5 gateway after checking other live positions' native
broker stops: the shared feed pauses briefly during restart.

    docker compose --profile mt5-shadow config --quiet
    docker compose --profile mt5-shadow up -d --no-deps --force-recreate mt5-gateway

## Prepare Kyle's private EA after merged GitHub PR

The Account2 source generator reads the five verified private values, inserts
the Account2 token only in the local compiler workspace and defaults new
entries and layers OFF. It is based on the same master template as Dan.

    python3 scripts/prepare_mt5_account2_private.py --lot-denominator VALUE --symbol EXACT_SYMBOL

VALUE must be explicitly approved in Kyle's account currency. For GBP, the
demo nominal baseline is 100 units per 0.01 lots. For USD, agree an equivalent
currency-normalised baseline. The broker-valuation SL risk ceiling is still
4% standard and 2% HIGH RISK; a nominal volume does not override it.
EXACT_SYMBOL must match Kyle's own broker Market Watch.

Compile source DKcapitalV211_Account2_MODE_CURRENCY_Private.mq5 in the
isolated MetaEditor (VNC 5903), using the real MODE/CURRENCY in the filename.
Require 0 errors and 0 warnings. Then run:

    METAEDITOR_ERRORS=0 METAEDITOR_WARNINGS=0 bash scripts/stage_mt5_v211_profile.sh 2 --private

Staging does not overwrite an existing EA, restart MT5, attach to a chart or
enable trades. On Kyle's terminal VNC 5902, attach the private EA to a chart
with the matching gold symbol and confirm entryEnabled=false and feed token
populated. MT5 Tools > Options > Expert Advisors must allow WebRequest to
http://127.0.0.1:8765.

## Automated fleet read-only acceptance (after Account2 is commissioned)

After verifying all three private profiles in /opt/dkcapital/.env and receiving
broker-authenticated snapshots, execute:

    python3 scripts/mt5_fleet_acceptance_readonly.py

This probes local authenticated feed and snapshot endpoints for all three
profiles; verifies exact protocol, login, REAL/DEMO mode, currency, server,
telemetry age, native SL proof, 4% planned standard risk, 2% alert-only
daily-loss warning and cross-token 401 rejection. It intentionally expects
Kyle's entry_enabled=false until explicitly commissioned. It does not
restart services or submit orders. A green result does NOT prove broker
fills or live freeze recovery.

## Final acceptance — no new live orders during verification

1. Kyle feed HTTP 200 and contract DKACCOUNT2V5 with Account2 token.
2. Kyle snapshot HTTP 200: EXACT MT5 login/server/mode/currency, fresh timestamp,
   broker positions/orders inventory, native SL protection, entries disabled.
3. Using a different profile's token is rejected with HTTP 401.
4. Account1 and Account3 stay healthy, with 200 feeds and fresh telemetry.
5. Verify broker account credit is excluded from risk calculations, and native
   XAUUSD/XAUUSD+ lot/contract/stop requirements satisfy 4%/2% ceilings.
6. Verify normal signal and HIGH RISK risk allocation, entry-zone tolerance,
   TP1-TP6 placement, SL amendments, breakeven, partial closes and deduplication
   first with simulated or DEMO signals. Do not use live real-money fills as an
   unapproved test.
7. Verify all three account-specific alert/recovery channels and watchdogs.
8. Verify systemd process failure, service enable-at-boot state, pinned MT5
   executable policy and independent VNC reachability.
9. LIVE frozen-terminal recovery is NOT commissioned until EA supports and
   proves position/order/deal-history reconciliation after restart. Do not
   disable this safety gate simply to claim identical recovery.
10. Only after acceptance, explicitly authorise the intended EA entry switches.
