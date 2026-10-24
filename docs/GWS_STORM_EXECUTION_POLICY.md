# GWS STORM and open-runner execution policy (demo-only, staged)

Basis: GWS - STORM Telegram pinned guidance posted 21 October 2026, and the four GWS chat exports of 24 October. The pinned message says STORM trades are fast-paced, manual traders may not have time to place TPs, and TP1 should be used only after at least 20 pips of drawdown, otherwise target TP2+. This is **discretionary advice**, not independently verified performance or an unconditional order to close in profit.

## Deployment status and modes

DKcapitalV2.mq5 V2.07 remains **GBP demo hedging account only**, with `EnableNewEntries=false` by default. Changing a GitHub file never installs a compiled EA on MT5. Do not activate new execution behavior without MetaEditor compilation, a clean demo trading run, and broker verification of stop/TP prices, realized fill times and state recovery.

* `EnableStormConditionalTP1=false` by default. When opted in, applies **only** to the verified STORM Telegram channel ID `-1004334637102`. At entry, the first 30% leg receives the provider's **TP2**, not TP1. The EA records observed adverse bid/ask excursion relative to that leg's broker fill on each timer poll. If excursion reaches `StormDrawdownPips*StormPipSizeUSD`, it durably switches that leg to the provider's **TP1**, but only if the broker still considers TP1 a valid native take-profit beyond the current quote. The second leg stays TP2, third leg stays TP3 unless explicitly OPEN. There are **no extra lots** from this TP policy.
* **Separate receipt and entry-zone clocks:** a STORM signal must reach the running, healthy, entry-enabled demo EA within `StormInitialFreshnessSeconds=45` seconds of the provider timestamp to be **armed** (the armed state is persisted in MT5 terminal globals). Once armed, the EA checks the broker price every poll and has until `StormEntryZoneWaitSeconds=300` seconds after the original message to enter the permitted zone. Thus a BUY at 4001–4003 that arrives while the ask is 3998 can still execute two minutes later when the ask enters the zone, subject to SL, TP, spread, 4% risk, exposure, provider cancellation and broker checks. This five-minute wait is a conservative DK Capital setting, **not** a time specified by GWS. If the message first reaches the EA after 45 seconds, it is not armed; old messages cannot be replayed into new positions by restarting the EA. The other GWS sources retain their 180-second new-entry window. Explicit layer instructions are independent and keep their existing 180-second age limit.
* `StormDrawdownPips=20.0`. `StormPipSizeUSD=0.10` is a **provisional example conversion**, consistent with some STORM message result labels, **not** verified broker/provider pip specification. Confirm provider convention and broker tick size before enabling. A 20-pip threshold corresponds to `$2.00` of adverse quote movement only **if** `1 pip = $0.10`.
* `EnableOpenRunnerEntries=false` by default. When enabled, the third 40% leg may have **no native TP** only if the source explicitly specified `TP3: OPEN`. The leg **must have the original provider's native SL** and remains subject to all standard stop-risk and portfolio checks. Missing, invalid or unspecified TP3 is **never** a license to create a runner. The runner can stay open until provider-directed exit or protective stop; discretionary "close when happy" has no machine-verifiable trigger, so open runners require close monitoring and demo tests.
* Explicit `NO BREAKEVEN YET` provider instructions are recorded and sent as `DEFER_BE`. They suspend only the EA's **automatic TP1-to-BE promotion** of surviving legs; they **cannot widen a stop already at or better than BE**. A subsequent explicit `SET BE` / `SL BREAKEVEN` / `SET ALL BREAKEVEN NOW` turns on provider-requested BE.
* Existing `EnableLayerEntries=false` stays untouched. If separately enabled after demo verification, the EA permits **at most one**, 10%-of-original, explicit and attributable layer per active signal group, at a better price and under the original provider native SL and all 4%/group/portfolio caps. STORM retrospective statements like "profit from layers" and `+LAYERS` recaps are not automatic entries.
* All providers retain their individually scoped signals and management. STORM behavior must **not** change CAPITAL, MONEY HOUSE, VIP or Elite Portfolios positions.

## Explicit limitations

1. **No guaranteed observation of intratick/intrapoll adverse excursions.** The timer samples bid/ask every 3 seconds by default; a transient dip/spike between polls may not trigger conditional TP1.
2. **No speculative profit-taking.** Phrases such as "close profit when happy", "close overall profit", "bring SL above" without explicit price or attributable position are not numerical exit conditions. Do not invent a 50% partial, a target or a stop.
3. **TP1/TP2/TP3 may be missing or wrong-sided.** The existing entry validator still skips invalid provider SL and requires native TP1/TP2 and TP3 unless TP3 explicitly OPEN and the opt-in is enabled.
4. **Broker-side exits take priority.** Historical/reported Telegram `TP HIT` is not treated as evidence of an actual broker fill. The source-to-EA feed contains no instruction to retroactively execute missed historical trades.
5. **No live roll-out from this PR.** Verify output in CI, build/compile MQL5 on isolated compiler, reconcile demo broker positions, confirm web-request telemetry, and inspect any open orders before authorising entries.

## Demo acceptance checklist

- Fresh STORM signal received at age 5s while BUY 4001–4003 has ask 3998: arm; remain unfilled; at age 120s with ask within entry zone, fill if all original broker safeguards pass.\n- First observed at 120s with no pre-existing armed state: reject as stale even if price subsequently touches the zone.\n- STORM signal is armed but never reaches its permitted entry zone: do not open after 300s.\n- STORM signal without drawdown: first leg exits at TP2; second leg TP2; third TP3.
- STORM signal with >=20 configured pips observed adverse excursion before TP1: first leg switches safely to TP1 with no new market order and no increase in lots.
- No adverse excursion threshold hit: never infer an excursion from historical candle lows.
- All other sources: the 30/30/40 three-target logic remains unchanged.
- CAPITAL `TP3 OPEN`: rejected when opt-in off; accepted as no-native-TP third leg only on valid demo broker and with actual protective SL.
- Missing TP3 with no explicit OPEN: rejected.
- VIP `TP1 HIT +45 PIPS; No breakeven yet`: surviving legs retain provider stop until explicit BE instruction, unless already BE-protected.
- Entry sizes, pending-order gates, Friday controls, spreads, 4% signal risk and group/portfolio caps remain in force.
- Restore from VPS repo/EA rollback if any demo results differ from expected behavior.
