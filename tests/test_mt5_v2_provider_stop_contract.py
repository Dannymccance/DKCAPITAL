"""Contract checks for the provider-only stop in the MQL5 entry function.

These checks are an additional guard against regressing to invented stops.
They do NOT replace compiling and broker-testing the MQL5 EA.
"""
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "mt5" / "DKcapitalV2.mq5"


class V2ProviderStopContract(unittest.TestCase):
    def test_only_provider_sl_accepted_for_new_entry(self):
        code = SOURCE.read_text(encoding="utf-8")
        entry = code.split("void TryEntry(", 1)[1].split("bool ActionKeySeen(", 1)[0]
        self.assertIn("if(providerSL<=0 || !StopValid(buy,providerSL,q)", entry)
        self.assertIn("double sl=NormalizePrice(providerSL);", entry)
        self.assertIn('Print("DK2 ENTRY SKIP: missing/invalid provider SL', entry)
        self.assertNotIn("fallbackBuffer", entry)
        self.assertNotIn("HouseMinStopDistanceUSD", code)

    def test_existing_positions_follow_explicit_provider_sl_even_when_wider(self):
        code = SOURCE.read_text(encoding="utf-8")
        manage = code.split("void ManageLegs(", 1)[1].split("void TryEntry(", 1)[0]
        self.assertIn(
            'else if(slMode=="PRICE" && stop>0 && StopValid(buy,stop,quote))',
            manage,
        )
        self.assertIn("wantedSL=NormalizePrice(stop);", manage)
        # Entry sizing limits must not reject an explicit SL widening on
        # a position that the broker has already opened.
        self.assertNotIn("GroupRiskLimitGBP()", manage)
        self.assertNotIn("PortfolioRiskLimitGBP()", manage)
        self.assertNotIn("BetterStop(buy,stop,wantedSL)", manage)
        self.assertIn('Print("DK2 native SL updated ticket=",ticket', manage)

    def test_tp1_and_breakeven_protection_prevent_later_stop_widening(self):
        code = SOURCE.read_text(encoding="utf-8")
        manage = code.split("void ManageLegs(", 1)[1].split("void TryEntry(", 1)[0]
        self.assertIn("tp1Confirmed && leg!=1", manage)
        self.assertIn("slAlreadyAtOrBeyondBE", manage)
        self.assertIn('slMode=="BREAKEVEN"', manage)
        self.assertIn("BetterStop(buy,proposed,wantedSL)", manage)
        self.assertIn("StopValid(buy,proposed,quote)", manage)

    def test_failed_broker_risk_valuation_blocks_entry(self):
        code = SOURCE.read_text(encoding="utf-8")
        entry = code.split("void TryEntry(", 1)[1].split("bool ActionKeySeen(", 1)[0]
        self.assertIn("if(r1<0 || r2<0 || r3<0)", entry)
        self.assertIn("double planned=r1+r2+r3;", entry)
        self.assertNotIn(
            "double planned=RiskGBP(buy,a,fill,sl)+RiskGBP", entry
        )

    def test_risk_caps_resize_lots_not_stop(self):
        code = SOURCE.read_text(encoding="utf-8")
        entry = code.split("void TryEntry(", 1)[1].split("bool ActionKeySeen(", 1)[0]
        self.assertIn("groupLimit*INITIAL_GROUP_RISK_SHARE-groupRisk", entry)
        self.assertIn("portfolioLimit-portfolio", entry)
        self.assertIn("RiskGBP(buy,a,fill,sl)", entry)
        self.assertIn("FloorLots(total-LotStep())", entry)

    def test_balance_scaled_risk_limits_and_full_broker_snapshot(self):
        code = SOURCE.read_text(encoding="utf-8")
        self.assertIn("RiskReferenceBalanceGBP = 1000.0", code)
        self.assertIn("return MathMin(balance,equity);", code)
        self.assertIn("MaxGroupRiskGBP*RiskBalanceGBP()/RiskReferenceBalanceGBP", code)
        self.assertIn("MaxPortfolioRiskGBP*RiskBalanceGBP()/RiskReferenceBalanceGBP", code)
        self.assertIn("MathFloor(AccountInfoDouble(ACCOUNT_BALANCE)/100)*0.01", code)
        self.assertIn("broker_positions_total", code)
        self.assertIn("broker_orders_total", code)
        self.assertIn("unmanaged_positions_count", code)
        self.assertIn("broker_positions_protected", code)

    def test_pending_orders_inhibit_new_entries_and_are_fully_reported(self):
        code = SOURCE.read_text(encoding="utf-8")
        trade_allowed = code.split("bool TradeAllowed()", 1)[1].split("bool BrokerDone()", 1)[0]
        self.assertIn("OrdersTotal()==0", trade_allowed)
        attempts = code.split("bool HasBeenAttempted(", 1)[1].split("ulong TicketForLeg(", 1)[0]
        self.assertIn("OrderGetInteger(ORDER_MAGIC)", attempts)
        self.assertIn("HistoryOrdersTotal()", attempts)
        telemetry = code.split("void SendTelemetry()", 1)[1].split("void ProcessFeed(", 1)[0]
        for key in (
            "unmanaged_orders_count", "broker_orders_total",
            "ORDER_VOLUME_CURRENT", "ORDER_PRICE_OPEN", "ORDER_SL",
            "ORDER_TYPE_BUY_LIMIT", "ORDER_TYPE_SELL_STOP",
        ):
            self.assertIn(key, telemetry)

    def test_one_layer_native_stop_and_broker_checks(self):
        code = SOURCE.read_text(encoding="utf-8")
        layer = code.split("void TryLayer(", 1)[1].split("bool ActionKeySeen(", 1)[0]
        self.assertIn("MathAbs(percentage-10.0)", layer)
        self.assertIn("maximum!=1", layer)
        self.assertIn("referenceLots*0.10", layer)
        self.assertIn("LayerPreviouslyAttempted(id,groupId)", layer)
        self.assertIn("SignalAgeLimit(id)", layer)
        self.assertIn("StopValid(buy,protectiveSL,q)", layer)
        self.assertIn("GroupRiskLimitGBP()*LAYER_GROUP_RISK_SHARE", layer)
        self.assertIn("groupRisk+plannedRisk>GroupRiskLimitGBP()", layer)
        self.assertIn("portfolio+plannedRisk>PortfolioRiskLimitGBP()", layer)
        self.assertIn("GlobalVariablesFlush()", layer)
        self.assertIn("LegMagic(id,LAYER_LEG)", layer)
        self.assertIn("GROUP_LAYER_ATTEMPT", code)
        self.assertIn("EnableNewEntries", layer)
        self.assertIn("EnableLayerEntries", layer)
        self.assertIn("LayerMarker(groupId)", code)
        self.assertIn("HistoryDealGetString(deal,DEAL_COMMENT)", code)

    def test_layer_is_in_all_lifecycle_paths(self):
        code = SOURCE.read_text(encoding="utf-8")
        self.assertIn("magic%10<=4", code)
        self.assertIn("for(int leg=1;leg<=4;leg++)", code)
        self.assertIn("for(int leg=4;leg>=1 && needed>=LotMin()-1e-8;leg--)", code)
        self.assertIn("if(leg==1) protectiveSL=sl;", code)


if __name__ == "__main__":
    unittest.main()
