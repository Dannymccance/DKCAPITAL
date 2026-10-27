# All-account MT5 trading permission alerts

The EA reads two native MT5 execution permission gates every telemetry cycle:
- `TERMINAL_TRADE_ALLOWED` — global terminal Algo Trading toggle.
- `MQL_TRADE_ALLOWED` — this EA's algorithmic trading permission.

The authenticated gateway normalizes them as `terminal_trade_allowed` and
`mql_trade_allowed`. They are **independent** of `entry_enabled`.
The Discord bot alerts in existing Demo Account1, Kyle Account2 and Dan Account3
alert channels when either flag is false. It reports the flag as UNVERIFIED if
an older EA has not yet supplied it, and emits a recovery only when the
authenticated, fresh flag explicitly becomes true. The REAL-account checks
already require recent authenticated telemetry; Demo reuses the existing
dashboard's stale-status gate. Alerts never submit, replay or close trades.

## Required rollout sequence

1. Merge CI-passing GitHub PR.
2. On VPS: `git fetch origin main && git merge --ff-only origin/main`.
3. Rebuild/recreate **only** gateway (to preserve new telemetry fields) and
   Discord bot (to run the new alert rules). Confirm the new alerts may initially
   say UNVERIFIED: old live EA binaries do not report these flags.
4. Rebuild/recompile each MT5 EA from updated approved source, preserving all
   account-specific private tokens, pinned broker identity, profile settings and
   correct symbol (Dan uses XAUUSD+). Use the existing private, audited MT5 EA
   deployment flow. Verify source/binary approval and do not restart any live
   terminal without checking existing broker positions/orders.
5. Inspect authenticated snapshots for both flags `true`. New signals should
   record broker-confirmed fills, not only enabled permissions.

This code alone **does not deploy revised EA binaries**. Until approved MT5
EAs are rebuilt and attached, PERMISSION_UNVERIFIED is the correct conservative
monitoring state. No secrets may enter GitHub or Discord.
