#!/usr/bin/env python3
"""Generate an independently identifiable, live-capable TP1 EA for Dan's REAL USD MT5.

The existing V2.11 multi-TP EA, account profile, secrets, compiled binaries,
broker order history and terminal remain untouched. Compile with MetaEditor,
verify and attach only by the normal release procedure.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.generate_mt5_v211_profile import (
    generate, token_from_private_env, write_compiler_source,
)


def tp1_source(account_id: int, login: int, mode: str, currency: str,
               server: str, lot_denominator: float,
               feed_token: str | None = None) -> str:
    if account_id != 3 or mode != "REAL" or currency != "USD":
        raise ValueError("Production TP1 source requires the verified Dan Account 3 REAL USD identity")
    source = generate(account_id, login, mode, currency, server,
                      lot_denominator, feed_token=feed_token)

    def patch(old: str, new: str) -> None:
        nonlocal source
        if source.count(old) != 1:
            raise RuntimeError(f"TP1 base contract changed: {old[:90]!r}")
        source = source.replace(old, new, 1)

    patch('// DKcapital V2.11: profile 3, verified REAL USD account',
          '// DKcapital TP1 v1: production-capable account 3 REAL USD, entry opt-in')
    patch('#property version "2.11"', '#property version "2.12"')
    patch('input double MaxSignalRiskPct = 4.0;',
          'input double MaxSignalRiskPct = 8.0; // Live TP1 per-trade SL risk\n'          'input double MaxPortfolioRiskPct = 8.0; // Live TP1 aggregate ceiling')
    patch('input double HighRiskSignalPct = 2.0;',
          'input double HighRiskSignalPct = 8.0; // TP1 signal ceiling; configurable')
    patch('input double MaxGroupRiskAccountCurrency = 75.0; // Legacy, unused on Dan.',
          'input double MaxGroupRiskAccountCurrency = 80.0; // Active 8% at reference $1,000')
    patch('input double MaxPortfolioRiskAccountCurrency = 150.0; // Legacy, unused on Dan.',
          'input double MaxPortfolioRiskAccountCurrency = 80.0; // Active 8% at reference $1,000')

    # The two old absolute ceiling inputs are STILL enforced in addition to
    # the 8% percentage gates. Never make these inputs silently inert.
    patch('double GroupRiskLimitAccountCurrency()\n{\n   return RiskBalanceAccountCurrency()*SessionRiskPct()/100.0;\n}',
          'double GroupRiskLimitAccountCurrency()\n{\n'
          '   return MathMin(RiskBalanceAccountCurrency()*SessionRiskPct()/100.0,\n'
          '      MaxGroupRiskAccountCurrency*RiskBalanceAccountCurrency()/RiskReferenceBalanceAccountCurrency);\n}')
    patch('double PortfolioRiskLimitAccountCurrency()\n{\n   return RiskBalanceAccountCurrency()*SessionRiskPct()/100.0;\n}',
          'double PortfolioRiskLimitAccountCurrency()\n{\n'
          '   return MathMin(RiskBalanceAccountCurrency()*MaxPortfolioRiskPct/100.0,\n'
          '      MaxPortfolioRiskAccountCurrency*RiskBalanceAccountCurrency()/RiskReferenceBalanceAccountCurrency);\n}')
    patch('int hour=UKTradingHour();\n   return hour>=1 && hour<9 ? 10.0 : 4.0;',
          'return MaxSignalRiskPct; // 8% default, no legacy session cap')
    patch('json+="\\\"risk_schedule\\\":\\\"UK_V2\\\",";',
          'json+="\\\"risk_schedule\\\":\\\"TP1_V1\\\",";')
    patch('   if(!EnableNewEntries) {RecordDecision(id,"ENTRY","ENTRY_DISABLED",age);return;}',
          '   if(!EnableNewEntries) {RecordDecision(id,"ENTRY","ENTRY_DISABLED",age);return;}\n'
          '   if(UKTradingHour()>=15)\n'
          '   {RecordDecision(id,"ENTRY","UK_TRADING_SESSION_CLOSED",age);return;}')
    patch('   if(!StormEntryArmed(id,age))',
          '   if(false && !StormEntryArmed(id,age)) // TP1 NOW: no zone wait')
    patch('   // STORM uses the exact provider entry zone: for BUY 4001-4003\n'
          '   // with broker ask 3998, keep armed until ask first reaches >=4001.\n'
          '   // The other providers retain the current +/-$1.00 tolerance.\n'
          '   double allowedLow=IsStormSignal(id)?zLow:zLow-EntryToleranceUSD;\n'
          '   double allowedHigh=IsStormSignal(id)?zHigh:zHigh+EntryToleranceUSD;\n'
          '   if(fill<allowedLow || fill>allowedHigh)\n'
          '   {RecordDecision(id,"ENTRY","PRICE_OUTSIDE_ENTRY_ZONE",age);return;}',
          '   // Submit immediately when valid. A quoted entry is a deviation\n'
          '   // reference, NOT a zone to wait for; zero means unquoted NOW.\n'
          '   if(zLow>0 && zHigh>0 &&\n'
          '      (fill<zLow-EntryToleranceUSD || fill>zHigh+EntryToleranceUSD))\n'
          '   {RecordDecision(id,"ENTRY","ENTRY_DEVIATION_EXCEEDED",age);return;}')
    patch('      (buy ? providerSL>=zLow : providerSL<=zHigh))',
          '       (zLow>0 && zHigh>0 && (buy ? providerSL>=zLow : providerSL<=zHigh)))')
    patch('   targets[3]=tp4;targets[4]=tp5;targets[5]=tp6;\n   int targetCount=0;',
          '   targets[3]=tp4;targets[4]=tp5;targets[5]=tp6;\n'
          '   // One broker-native TP1 position, never split targets.\n'
          '   for(int t=1;t<MAX_NATIVE_TARGETS;t++) targets[t]=0;\n'
          '   int targetCount=0;')
    patch('   if(openRunner)\n   {\n      if(!EnableOpenRunnerEntries)',
          '   if(false && openRunner)\n   {\n      if(!EnableOpenRunnerEntries)')
    patch('      // The separately tracked 10% layer exits at TP2.',
          '      // Legacy orders are managed by their original EA until closed.\n'
          '      // TP1-only orders always retain their actual TP1 target.')
    patch('   bool tp1Confirmed=TP1BrokerConfirmed(id);',
          '   // Single-position TP1: there is no remaining leg to move to BE.')
    patch('input bool EnableStormConditionalTP1 = false;',
          'const bool EnableStormConditionalTP1 = false; // TP1 never shifts TP1 to TP2')
    patch('MaxSignalRiskPct<=0 || MaxSignalRiskPct>100 ||',
          'MaxSignalRiskPct<=0 || MaxSignalRiskPct>100 ||\n'
          '      MaxPortfolioRiskPct<=0 || MaxPortfolioRiskPct>100 ||')
    patch('      bool preserveBE=(slMode=="BREAKEVEN" ||\n'
          '                       (tp1Confirmed && leg!=1 && slMode!="DEFER_BE") ||\n'
          '                       slAlreadyAtOrBeyondBE);',
          '      bool preserveBE=slAlreadyAtOrBeyondBE; // No automatic TP1-to-BE migration.')
    patch('   if(status!="ACTIVE" || mid<=0 || maximum!=1 ||',
          '   if(true) return; // TP1 strategy never layers.\n'
          '   if(status!="ACTIVE" || mid<=0 || maximum!=1 ||')
    patch('      else if(fields==8 && f[0]=="L")',
          '      else if(false && fields==8 && f[0]=="L") // TP1: never layer')
    patch('         if(low<=0 || high<=0) continue;',
          '         if(low<0 || high<0) continue; // 0/0 is valid unquoted NOW reference')
    patch('      Print("DK2 filled signal=",id," targets=",targetCount,',
          '      Print("DK2 TP1 broker fill confirmed signal=",id," targets=",targetCount,')
    patch('   // Durable intent before first broker request prevents duplicates on restart.',
          '   Print("DK2 TP1 order intent signal=",id," plannedStopLossUSD=",budget,\n'
          '         " lotTarget=",targetLots," brokerMarginFree=",AccountInfoDouble(ACCOUNT_MARGIN_FREE));\n'
          '   // Durable intent before first broker request prevents duplicates on restart.')
    if 'input bool EnableNewEntries = false;' not in source:
        raise RuntimeError("Unsafe live EA entry default")
    required = ('TP1_V1', 'MaxSignalRiskPct = 8.0', 'UKTradingHour()>=15',
                'targets[t]=0;', 'if(true) return; // TP1 strategy never layers.',
                'INITIAL_GROUP_RISK_SHARE = 1.0')
    for marker in required:
        if marker not in source:
            raise RuntimeError(f"TP1 source missing {marker}")
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", type=int, choices=(3,), required=True)
    parser.add_argument("--login", type=int, required=True)
    parser.add_argument("--mode", choices=("REAL",), required=True)
    parser.add_argument("--currency", choices=("USD",), required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--lot-denominator", type=float, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--feed-token-env-file", type=Path, required=True)
    args = parser.parse_args()
    token = token_from_private_env(
        args.feed_token_env_file, login=args.login, mode=args.mode,
        currency=args.currency, server=args.server, account_id=3,
    )
    candidate = tp1_source(3, args.login, args.mode, args.currency,
                           args.server, args.lot_denominator, feed_token=token)
    output = args.output_dir / "DKcapitalTP1_Account3_REAL_USD_Private.mq5"
    if output.exists() or output.is_symlink():
        parser.error("Candidate exists: refusing to overwrite audited source")
    write_compiler_source(output, candidate, args.output_dir)
    print(f"STAGED {output} (not compiled, attached or entry-armed)")


if __name__ == "__main__":
    main()
