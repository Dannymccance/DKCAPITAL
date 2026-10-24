# DK Capital paper V2 (parallel experiment; no broker orders)

This branch creates an **opt-in separate** \`paper-v2\` Compose service. It does not overwrite \`paper-engine\`, Account 1, Account 2 or the existing paper ledger. Its persisted state and event log live at:

- \`/app/data/paper-v2-trading.json\`
- \`/app/data/paper-v2-trade-events.jsonl\`
- \`/app/data/paper-v2-delay-state.json\`

The new service reads the existing \`telegram-events.jsonl\` and \`xauusd-market-tape.jsonl\` from the shared Docker volume; \`PAPER_MARKET_TAPE_WRITER=false\` ensures it does **not** duplicate or change the quote collector. Its delayed simulation is the configured 900 seconds, with independent cursor/baseline: messages existing when V2 first starts are only context, never a backlog of new paper orders. Old paper balance and P&L stay exactly as before. Never run the reset/backfill tools for the V2 ledger without deliberate review.

## Demo model (not broker parity)

- Provider message must be seen freshly for STORM (within 45s). A qualified, armed STORM entry remains eligible for five minutes to reach the **exact** bid/ask entry zone (e.g. BUY 4001–4003 when ask initially 3998, later reaches 4001). Other GWS provider messages retain a 180-second window and $1 tolerance.
- Provider SL mandatory, valid from the entry zone and actionable versus the executable quote; **no house temporary SL**. Native-like priced TP1/TP2/TP3 are required, except explicitly \`TP3: OPEN\` (which may leave the last 40% without a target).
- Scale USD virtual lots from \`floor(balance USD / 100)*0.01\`, with a maximum **4% of lower balance/equity** stop risk, plus account-size-scaled group and portfolio caps. The simulator uses XAUUSD contract \`PAPER_XAU_CONTRACT_OZ_PER_LOT\`. These **USD** illustrative caps are not a GBP-to-USD FX conversion. It does not check broker margin/commission/swaps, liquidity, slippage, MT5 hedging account compatibility, or real executable order-book fills. Therefore simulated performance is **not** equivalent to the EA's verified broker P&L.
- The simulated three exits use volume-step adjusted 30/30/40 quantities. For STORM the first 30% targets TP2 unless the recorded quote first moves 20 × $0.10 = $2 adverse from the fill, when it targets TP1. **The pip size is provisional**; this is a parameterized experiment, not a verified provider pip definition.
- The 2% daily drawdown is **recorded as a warning**, not used to block new V2 paper entries. Original \`paper-engine\` keeps its current settings and does not change its behaviour.
- Provider-reported \`TP HIT\`, \`SL HIT\`, and \`+LAYERS\` profits never manufacture fills. Paper TP and SL exits require observed delayed bid/ask quotes; provider-directed explicit closes/percentage partials are modelled at the observed quote. Explicit \`SET BE\` moves the remaining stop. \`NO BREAKEVEN YET\` suppresses only automatic TP1-BE. No protective stop already at or beyond BE is widened.
- GWS \`ADDING NOW\` / \`ADD NOW\` / \`LAYER NOW\` tied to an existing signal allow at most one 10%-size layer, only at a better price and within remaining stop-risk budget. A standalone layer permission, result or re-entry does not trigger an add. A single aggregate position approximates the original three MT5 legs and an added TP2 quantity: this is **not** a tick-accurate reconstruction of a separate broker fourth ticket.
- If a quote jumps over an entry zone entirely between polls, no order is simulated. No pending broker orders are modelled. Likewise adverse excursion between quote observations is not known.

## GitHub-first rollout (after green CI/merge)

From VPS at \`/opt/dkcapital\`, **do not** run \`docker compose down\` or recreate live Account 1/2 terminals.

\`\`\`bash
git pull --ff-only origin main
docker compose --profile paper-v2 config --quiet
# Read only: check existing paper account before adding a separate simulator:
docker compose ps paper-engine signal-processor
# Build/start ONLY the isolated V2 paper engine, and no other services:
docker compose --profile paper-v2 up -d --build --no-deps paper-v2
docker compose --profile paper-v2 logs --tail=80 paper-v2
\`\`\`

Verify the current paper-engine container remains running and untouched. The new V2 service must log \`mode=signal_follow_v2\` and its own baseline and must show no historical fills on initial start. The \`/app/data\` path is in the mounted named Docker volume. Inspect its state without printing secrets:

\`\`\`bash
docker compose --profile paper-v2 exec -T paper-v2 python - <<'PY'
import json
from pathlib import Path
for name in ('paper-trading.json', 'paper-v2-trading.json'):
    p = Path('/app/data') / name
    d = json.loads(p.read_text()) if p.exists() else {}
    print(name, 'balance=', d.get('balance_usd'),
          'mode=', d.get('strategy_mode'),
          'positions=', len(d.get('positions', [])),
          'sim=', d.get('simulation', {}).get('delay_minutes'))
PY
\`\`\`

The existing paper Discord dashboard and \`/paper_status\` command continue to describe the **historical V1 paper account**. V2's independent Discord presentation must be separately integrated and labelled; do not mix P&L or publish V2 as broker-confirmed.

## Operational caveats

This is a new simulated execution experiment, not a production-approved MT5 strategy. The existing V2.07 EA and both broker terminals remain untouched. Quote data can be delayed/missing, provider timestamps can vary, and the synthetic fill may differ materially from real spread/slippage and margin. Review a full week of V2 paper trades, entry misses and any premature fills before comparing it with MT5. Never reset legacy paper data to force comparability.
