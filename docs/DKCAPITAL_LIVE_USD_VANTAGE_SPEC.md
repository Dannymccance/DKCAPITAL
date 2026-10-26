# DK Capital live USD Vantage EA: production implementation contract

## Product objective

The GBP Account 1 demo is the **broker acceptance fixture**. The intended finished system is a distinct, explicitly approved **Vantage USD REAL** XAUUSD Expert Advisor, using the same target preservation and provider-derived 4%/2% risk policy. Do not use the GBP DEMO binary on the USD REAL account. Completing the current V2.11 demo release does not approve live trading.

## Hard requirements for a separate live release

1. **Account identity and separation:** compile a uniquely named LIVE EA (separate file and EX5) and require the exact approved MT5 account login, expected broker server, \`ACCOUNT_TRADE_MODE_REAL\`, \`ACCOUNT_CURRENCY=USD\`, retail-hedging mode and correct XAUUSD symbol. A demo or wrong live account must fail initialization. Default \`EnableNewEntries=false\`. Do not embed account secrets. Preserve existing demo and other accounts unchanged.
2. **Separate authenticated telemetry/feed:** create per-account endpoint and namespace, never overwrite \`/v2/demo/telemetry\` or \`mt5-account1.json\`; do not let live and demo share magic IDs, attempt journals, risk reservations or position matching. Validate account identity on every push/poll. Feed contains exact 1-6 provider numeric targets, original message ID, SL, opening timestamp, risk classification, and explicit close/update instructions.
3. **USD broker-valued risk:** derive planned loss from \`OrderCalcProfit\` at the broker's actual contract size, conversion/tick rules and provider SL, including commission/slippage padding in **account USD**, not functions calibrated to GBP. Every normal signal is limited to 4%; an explicitly flagged HIGH RISK signal to 2%, across all its legs/layers. Use the lower of unbonused cash balance and equity for risk sizing. Vantage broker CREDIT and promotional margin bonuses never increase permitted risk or target lot size. Verify real broker account BALANCE/CREDIT/EQUITY semantics before enabling.
4. **GBP-origin balance-lot rule:** the existing demo rule of 0.01 total lots per complete GBP 100 cannot be copied verbatim to USD. Preserve user-agreed balance scaling using a verified current USD/GBP reference or seek explicit USD denomination policy. Risk-based sizing and margin checks override any balance-lot target. If conversion is missing, fail closed for new entries rather than guessing. Do not increase lots because leverage, credit or free margin is higher.
5. **All native TP legs:** exactly one protected hedged broker position per numeric TP, equal lot allocation within broker's legal volume step, never multiplying the total signal lot size. If a minimum legal lot per TP cannot be afforded, skip the entire signal. If an order only partly fills, reconcile tickets, alert and unwind the group safely. Validate native SL and matching TP for every position. Retain explicit provider SL, but refuse stop widenings that exceed the risk budget and report the discrepancy.
6. **Risk-concurrent logic:** treat all open groups and layers as combined gross stop exposure, including pending reservations, not netted BUY vs SELL. Re-check broker identity, currency, margin, price, spread, quote freshness, session and accepted fills. Daily 2% is a warning only by existing preference, but group and signal planned risk limits are enforceable.
7. **Performance reporting:** all realised P&L in USD, commissions/swaps included; Vantage XAUUSD pip size must be checked from symbol/broker convention (0.01 gold price units if confirmed), and do not conflate money, points, pips or contract lots.

## Release and evidence required

- GitHub branch -> peer-review PR -> passing Python CI/static MQL5 checks -> isolated compiler **zero errors and zero warnings** -> separate compiled LIVE EX5 with a distinct SHA -> approved operator deployment.
- Test synthetic 1-6 targets, normal and high-risk sizing, partial fills, 2% and 4% stop caps, broker stop updates, missed updates/reconnect, BE, replay deduplication, closes, credit and balance distinction, Vantage quote/volume/tick/contract values, USD net P&L and pip calculation.
- Broker test on a segregated and controlled real account with minimum allowed exposure before any unattended live automation. No automatic activation, no silent credentials copying and no in-place reuse of the demo EA.
- Pre-production sign-off must confirm the correct **actual** USD REAL login/server, whether the reported $1,332.83 balance excludes all promotional credits, the account's margin/hedging behavior and new lot-sizing USD-equivalence rule.

## Current known figures (user reported 26 Oct 2026)

- Vantage USD cash balance: **$1,332.83** reported.
- Equity: **$2,916.33** reported, including promotional credit per user's explanation. Not a valid basis for increasing trading risk.
- Maximum planned normal signal risk at reported cash balance: **$53.31** (4%).
- Maximum planned HIGH RISK signal risk at reported cash balance: **$26.66** (2%).
- Live numbers are illustrative until broker telemetry confirms exact cash balance, equity, credit and available margin.
