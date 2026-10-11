> **IMPORTANT - RULESET V2 SUPERSEDES THE CURRENT EA TRADE SETTINGS.** See [DKCAPITAL_RULESET_V2.md](DKCAPITAL_RULESET_V2.md). The existing `mt5/DKcapitalDemo.mq5` is a single-position 0.05-lot EA and **does not** implement the user's agreed three-leg sizing, Friday 0.01 x 3 allocation, fixed GBP75 group cap, or full provider-directed (and only provider-directed) weekend-exit handling. Ruleset V2 additionally confirms a **GBP150 aggregate planned stop-loss exposure ceiling on each GBP1,000 demo account**, independently of the GBP75 group cap. The current EA does not enforce this portfolio budget or fully support layers/re-entries. Leave `EnableNewEntries=false` on all MT5 terminals until a revised EA is built, compiled, and validated on a broker demo account. This document otherwise describes the older test EA, not the V2 trading rules.

# DKcapital MT5 two-account DEMO setup

The demo Expert Advisor is at mt5/DKcapitalDemo.mq5. It uses an opt-in authenticated local feed from the existing DKcapital MT5 gateway.

## Mandatory safety boundary

The EA refuses to initialise unless the broker account is DEMO, it is a hedging account, and the account login matches the explicitly entered ExpectedDemoLogin. Initial EnableNewEntries is false. No live-account execution path is provided.

Two MT5 installations already exist under separate Linux users, dkmt5 and dkmt5b, with independent virtual displays :150 and :151. Install and compile the same EA into both terminals, then configure each with its own demo account login.

## Default demo trading experiment

Target position: 0.05 XAUUSD lots per new eligible signal. Apply broker lot steps and reduce volume if the estimated loss from actual entry to protective SL exceeds 7.5% of the account equity (GBP 75 for a GBP 1,000 account). New orders stop if account equity falls more than 10% below the recorded start-of-UTC-day equity. Use a provider SL if structurally valid, otherwise the house temporary stop. Signals expire for entry after 180 seconds. Entry must be within USD 1.00 of provider zone. Max bid/ask spread is USD 1.50.

TP1/TP2/TP3 close approximately 30/30/40 percent of original volume, limited by broker lot increments. For 0.05 lots and 0.01-lot steps this can become 0.01, 0.01, 0.03. After a broker-confirmed TP1 close, attempt breakeven SL. Honor tightening provider SL updates only. Broker native SL is sent with each new order. Provider terminal status closes the position. Tag and persist signal IDs to prevent duplicate entry after restart.

Actual losses can exceed calculated limits because of slippage, gaps, commissions or failures. The daily guard blocks new entries only. TP partial management needs the EA online; protective stops are placed with the broker. This is a test implementation pending broker verification and end-to-end validation.

## Gateway installation on Ubuntu

Within /opt/dkcapital, configure .env with:
MT5_GATEWAY_TOKEN = a random secret from openssl rand -hex 32
MT5_DEMO_FEED_ENABLED=true
MT5_GATEWAY_PORT=8765

Start gateway only using:
docker compose --profile mt5-shadow up -d --build mt5-gateway

Health: curl http://127.0.0.1:8765/health

Endpoint: http://127.0.0.1:8765/v1/demo/feed
Use HTTP Authorization: Bearer SECRET. Server is bound on Docker-published host loopback only, never directly expose its port to the public internet.

## MT5 GUI steps on each demo terminal

Install mt5/DKcapitalDemo.mq5 under the portable terminal's MQL5/Experts folder. Compile with MetaEditor (F7) and confirm zero errors and the .ex5 output. Log in to the correct demo hedging account. Use Tools > Options > Expert Advisors > allow WebRequest for http://127.0.0.1:8765.

Attach the EA to one XAUUSD chart. Enter ExpectedDemoLogin as the exact broker demo login and FeedToken as the local gateway token. Set GoldSymbol to the broker's actual gold symbol. Leave EnableNewEntries=false until demo feed, broker details, SL and TP behavior have been verified. Enable MT5 Algo Trading when ready. Inspect the Experts and Journal logs for connectivity errors. After confirming both the correct account and new signal intake, separately activate new entries in each configured EA.

Do NOT attach the EA to multiple charts in the same MT5 account.

## Blockers

The Telegram listener was previously restarting because TELEGRAM_SESSION_STRING was unauthorized. Repair and verify it before expecting fresh signal delivery. Do not paste passwords or secrets into chat.

## Emergency controls

To pause new entries but retain management, turn off EnableNewEntries on both EAs while leaving the terminals running. Stopping the EA or disabling platform Algo Trading may stop software-managed partial exits and breakeven modifications, even though broker-side protective SL orders remain in place.

## Friday and weekend rule (V2, not in this EA)

The user has **withdrawn scheduled Friday liquidation**. There is no automatic clock-based Friday close-out, and no weekly close-only lock. Friday new entries are limited to **three 0.01-lot positions** on a sufficiently funded GBP 1,000 demo account, subject to the **GBP 75 combined planned stop loss ceiling**. EA-owned gold positions remain open across the weekend unless their broker-native protective TP/SL is triggered or an applicable **explicit close instruction** is parsed from the originating Telegram provider. Telegram commentary, provider-reported TP/SL events, feed outages and the approaching broker weekly market closure do not justify forcibly closing positions. A provider close instruction must be reconciled against actual broker fills; if received during market closure, persist the applicable intent and attempt execution on reopening with an alert. Refer to the V2 ruleset for scope and attribution.
