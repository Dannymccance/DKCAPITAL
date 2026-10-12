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
        self.assertIn("MaxGroupRiskGBP*INITIAL_GROUP_RISK_SHARE-groupRisk", entry)
        self.assertIn("MaxPortfolioRiskGBP-portfolio", entry)
        self.assertIn("RiskGBP(buy,a,fill,sl)", entry)
        self.assertIn("FloorLots(total-LotStep())", entry)

    def test_one_layer_native_stop_and_broker_checks(self):
        code = SOURCE.read_text(encoding="utf-8")
        layer = code.split("void TryLayer(", 1)[1].split("bool ActionKeySeen(", 1)[0]
        self.assertIn("MathAbs(percentage-10.0)", layer)
        self.assertIn("maximum!=1", layer)
        self.assertIn("referenceLots*0.10", layer)
        self.assertIn("LayerPreviouslyAttempted(id,groupId)", layer)
        self.assertIn("MaximumSignalAgeSeconds", layer)
        self.assertIn("StopValid(buy,protectiveSL,q)", layer)
        self.assertIn("MaxGroupRiskGBP*LAYER_GROUP_RISK_SHARE", layer)
        self.assertIn("groupRisk+plannedRisk>MaxGroupRiskGBP", layer)
        self.assertIn("portfolio+plannedRisk>MaxPortfolioRiskGBP", layer)
        self.assertIn("GlobalVariablesFlush()", layer)
        self.assertIn("LegMagic(id,LAYER_LEG)", layer)
        self.assertIn("GROUP_LAYER_ATTEMPT", code)
        self.assertIn("EnableNewEntries", layer)

    def test_layer_is_in_all_lifecycle_paths(self):
        code = SOURCE.read_text(encoding="utf-8")
        self.assertIn("magic%10<=4", code)
        self.assertIn("for(int leg=1;leg<=4;leg++)", code)
        self.assertIn("for(int leg=4;leg>=1 && needed>=LotMin()-1e-8;leg--)", code)
        self.assertIn("if(leg==1) protectiveSL=sl;", code)


if __name__ == "__main__":
    unittest.main()
