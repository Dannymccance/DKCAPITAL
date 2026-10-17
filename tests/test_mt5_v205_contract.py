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

    def test_no_unplanned_tp_runner(self):
        entry = self.code.split("void TryEntry(", 1)[1].split(
            "// Original three-leg broker fills", 1)[0]
        self.assertIn("if(tp1<=0 || tp2<=0 || tp3<=0 ||", entry)
        for target in ("tp1", "tp2", "tp3"):
            self.assertIn(f"ValidTP(buy,{target},q)<=0", entry)
        self.assertIn('"MISSING_OR_INVALID_NATIVE_TP"', entry)
        self.assertIn("StopValid(buy,providerSL,q)", entry)

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
        self.assertIn("if(BetterStop(buy,fill,wantedSL)", source)
        self.assertIn("if(BetterStop(buy,stop,wantedSL)", source)


if __name__ == "__main__":
    unittest.main()
