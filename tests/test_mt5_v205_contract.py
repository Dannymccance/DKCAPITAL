"""Source guards for V2.05: supplement, never replace, MetaEditor compilation."""
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "mt5" / "DKcapitalV2.mq5"


class V205SourceContract(unittest.TestCase):
    def setUp(self):
        self.code = SOURCE.read_text(encoding="utf-8")

    def test_daily_equity_threshold_never_gates_new_orders(self):
        source = self.code
        self.assertIn("input double DailyLossWarningPct = 2.0;", source)
        self.assertIn("bool DailyEquityWarningActive()", source)
        self.assertIn("AccountInfoDouble(ACCOUNT_EQUITY)", source)
        self.assertIn("DAILY_WARN_", source)
        entry = source.split("void TryEntry(", 1)[1].split(
            "// Original three-leg broker fills", 1)[0]
        layer = source.split("void TryLayer(", 1)[1].split("bool ActionKeySeen(", 1)[0]
        self.assertNotIn("DailyEquityWarningActive", entry)
        self.assertNotIn("DailyEquityWarningActive", layer)
        self.assertNotIn("DailyLossAllowed", entry + layer)
        self.assertNotIn("DAILY_EQUITY_LOSS_LOCK", source)
        self.assertIn("daily_loss_guard_mode", source)
        self.assertIn("ALERT_ONLY", source)

    def test_balance_based_lots_with_four_percent_provider_stop_risk(self):
        source = self.code
        self.assertIn("input double MaxSignalRiskPct = 4.0;", source)
        self.assertIn("double SignalRiskLimitGBP()", source)
        self.assertIn("RiskBalanceGBP()*MaxSignalRiskPct/100.0", source)
        self.assertIn("return MathMin(balance,equity);", source)
        entry = source.split("void TryEntry(", 1)[1].split(
            "// Original three-leg broker fills", 1)[0]
        layer = source.split("void TryLayer(", 1)[1].split(
            "bool ActionKeySeen(", 1)[0]
        # Do not replace the account-BALANCE target sizing with fixed
        # fractional lots or an entirely different entry allocation.
        self.assertIn(
            "MathFloor(AccountInfoDouble(ACCOUNT_BALANCE)/100)*0.01",
            entry,
        )
        self.assertIn("MathMin(portfolioLimit-portfolio,signalRemaining)", entry)
        self.assertIn("double signalRemaining=signalLimit-groupRisk;", entry)
        self.assertIn("if(planned>budget+1e-7) continue;", entry)
        self.assertIn("FloorLots(total-LotStep())", entry)
        self.assertIn("groupRisk+plannedRisk>SignalRiskLimitGBP()+1e-7", layer)
        self.assertIn("LAYER_EXCEEDS_SIGNAL_4_PERCENT_RISK", layer)
        self.assertIn("RiskGBP(buy,minA,fill,sl)", entry)
        self.assertIn("MINIMUM_THREE_LEG_SIZE_EXCEEDS_SIGNAL_RISK", entry)
        self.assertIn("EnableLayerEntries = false", source)
        self.assertNotIn("DailyLossAllowed()", entry + layer)

    def test_only_explicit_provider_open_runner_with_demo_opt_in(self):
        entry = self.code.split("void TryEntry(", 1)[1].split(
            "// Original three-leg broker fills", 1)[0]
        self.assertIn("input bool EnableOpenRunnerEntries = false;", self.code)
        self.assertIn("bool twoTargets=!openRunner && tp3<=0;", entry)
        self.assertIn("(!twoTargets && !openRunner && ValidTP(buy,tp3,q)<=0)", entry)
        self.assertIn("targets[2]=openRunner?0:(twoTargets?tp2:tp3);", entry)
        self.assertIn('GlobalVariableSet(IDKey(id,"TWO_TARGET_ENTRY"),1);', entry)
        self.assertIn('GlobalVariablesFlush();', entry)
        self.assertIn('if(openRunner && !EnableOpenRunnerEntries)', entry)
        self.assertIn('"OPEN_RUNNER_REQUIRES_OPT_IN"', entry)
        self.assertIn('"MISSING_OR_INVALID_NATIVE_TP"', entry)
        self.assertIn("StopValid(buy,providerSL,q)", entry)
        self.assertIn('double tp=(leg==3 && openRunner)?0:ValidTP', entry)

    def test_storm_drawdown_tp1_rule_is_source_scoped_and_persistent(self):
        code = self.code
        self.assertIn("input bool EnableStormConditionalTP1 = false;", code)
        self.assertIn("input double StormPipSizeUSD = 0.10;", code)
        self.assertIn("input double StormDrawdownPips = 20.0;", code)
        self.assertIn('StringFind(id,"-1004334637102:")==0', code)
        self.assertIn('"STORM_TP_POLICY"', code)
        self.assertIn('"STORM_DRAWDOWN_TP1"', code)
        self.assertIn("GlobalVariablesFlush();", code)
        self.assertIn("double firstTarget=(stormAdaptive && !stormUseTP1)?tp2:tp1;", code)
        self.assertIn("targets[0]=stormAdaptive?tp2:tp1;", code)
        self.assertIn("adverse=buy ? fill-quote.bid : quote.ask-fill", code)
        self.assertIn("slMode!=\"DEFER_BE\"", code)

    def test_storm_signals_arm_promptly_but_can_wait_for_entry_zone(self):
        source = self.code
        self.assertIn("input int StormInitialFreshnessSeconds = 45;", source)
        self.assertIn("input int StormEntryZoneWaitSeconds = 300;", source)
        self.assertIn("int SignalAgeLimit(const string id)", source)
        self.assertIn(
            "IsStormSignal(id) ? StormEntryZoneWaitSeconds : MaximumSignalAgeSeconds",
            source,
        )
        self.assertIn("bool StormEntryArmed(const string id,const long age)", source)
        self.assertIn('IDKey(id,"STORM_ENTRY_ARMED")', source)
        self.assertIn("GlobalVariableCheck(key)) return true", source)
        self.assertIn("age>StormInitialFreshnessSeconds", source)
        self.assertIn("GlobalVariableSet(key,(double)TimeGMT());", source)
        self.assertIn("GlobalVariablesFlush();", source)
        entry = source.split("void TryEntry(", 1)[1].split(
            "// Original three-leg broker fills", 1)[0]
        layer = source.split("void TryLayer(", 1)[1].split(
            "bool ActionKeySeen(", 1)[0]
        self.assertIn("int maxAge=SignalAgeLimit(id);", entry)
        self.assertIn("if(age<0 || age>maxAge)", entry)
        self.assertIn("if(!StormEntryArmed(id,age))", entry)
        self.assertIn("double allowedLow=IsStormSignal(id)?zLow:zLow-EntryToleranceUSD;", entry)
        self.assertIn("double allowedHigh=IsStormSignal(id)?zHigh:zHigh+EntryToleranceUSD;", entry)
        self.assertIn("if(fill<allowedLow || fill>allowedHigh)", entry)
        self.assertIn('RecordDecision(id,"ENTRY","STORM_SIGNAL_NOT_ARMED",age);', entry)
        # Layer orders are separate immediate instructions, not inherited
        # from the initial signal's five-minute entry-zone waiting period.
        self.assertIn("age>MaximumSignalAgeSeconds", layer)
        self.assertNotIn("age>SignalAgeLimit(id)", layer)

    def test_mt5_exit_reasons_never_inferred_from_provider(self):
        code = self.code
        self.assertIn("DEAL_REASON_TP", code)
        self.assertIn("DEAL_REASON_SL", code)
        self.assertIn("HistoryDealGetInteger(deal,DEAL_REASON)", code)
        self.assertIn('json+=",\\"reason\\":\\""', code)

    def test_entry_layer_decisions_include_broker_quote_and_age(self):
        source = self.code
        for phrase in (
            '"PRICE_OUTSIDE_ENTRY_ZONE"', '"BROKER_SPREAD_TOO_WIDE"',
            '"MINIMUM_THREE_LEG_SIZE_OR_MARGIN"', '"BROKER_ORDER_REJECTED_OR_PARTIAL"',
            '"LAYER_BELOW_MINIMUM_LOT_OR_MISSING_ORIGINAL"',
            '"LAYER_ENTRIES_DISABLED"',
            '"ENTRY_WINDOW_EXPIRED"', '"MISSING_OR_INVALID_NATIVE_TP"',
        ):
            self.assertIn(phrase, source)
        for telemetry_field in ('decisions', 'age_seconds', 'broker_retcode', 'layer_entries_enabled'):
            self.assertIn(telemetry_field, source)

    def test_native_stop_and_fixed_risk_rules_not_removed(self):
        source = self.code
        self.assertIn("MaxSpreadUSD = 1.5", source)
        self.assertIn("MaximumSignalAgeSeconds = 180", source)
        self.assertIn("EntryToleranceUSD = 1.0", source)
        self.assertIn("RiskReferenceBalanceGBP = 1000.0", source)
        self.assertIn("MaxPortfolioRiskGBP = 150.0", source)
        self.assertIn("EnableLayerEntries = false", source)
        # Breakeven still only allows better stops. Before breakeven,
        # explicit provider amendments can move the stop in either direction.
        self.assertIn("if(BetterStop(buy,proposed,wantedSL)", source)
        self.assertIn(
            'else if(slMode=="PRICE" && stop>0 && StopValid(buy,stop,quote))',
            source,
        )


if __name__ == "__main__":
    unittest.main()
