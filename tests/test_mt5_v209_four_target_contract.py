"""V2.09 four-target native broker compatibility under V2.10 equal sizing.

The original TP4 magic suffix 5 and layer suffix 4 are never reassigned.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
EA = (ROOT/"mt5/DKcapitalV2.mq5").read_text(encoding="utf-8")
GATEWAY = (ROOT/"src/dkcapital/mt5_gateway.py").read_text(encoding="utf-8")


class FourTargetCompatibility(unittest.TestCase):
    def test_four_target_trade_remains_four_real_native_tp_legs(self):
        for token in (
            '#property version "2.11"',
            'const int TP4_LEG = 5;',
            'const int LAYER_LEG = 4;',
            'return i<3?i+1:i+2;',
            'if(!AllocateEqual(total,initialLegCount,vols)) continue;',
            'for(int i=0;i<initialLegCount;i++)',
            'bool wasFourTargets=GlobalVariableCheck(IDKey(id,"FOUR_TARGET_ENTRY"));',
        ):
            self.assertIn(token, EA)

    def test_four_targets_equal_lots_in_current_ea(self):
        self.assertIn('int base=totalUnits/targetCount;', EA)
        self.assertIn('int remainder=totalUnits%targetCount;', EA)
        self.assertIn('int units=base+(i<remainder?1:0);', EA)
        self.assertIn('if(totalUnits<targetCount*minimumUnits) return false;', EA)

    def test_legacy_gateway_v3_and_v2_headers_remain(self):
        self.assertIn('lines = ["DKDEMO5" if v5 else ("DKDEMO4" if v4 else ("DKDEMO3" if v3 else "DKDEMO2"))]', GATEWAY)
        self.assertIn('supported = 6 if (v4 or v5) else (4 if v3 else 3)', GATEWAY)
        self.assertIn('if (count > supported or (signal.get("high_risk") and not v5)) and status == "ACTIVE":', GATEWAY)
        self.assertIn('if v4:', GATEWAY)
        self.assertIn('elif v3 and count >= 4:', GATEWAY)

    def test_broker_magic_5_6_7_reporting_and_stop_evidence(self):
        reporting = (ROOT/"src/dkcapital/mt5_account_reporting.py").read_text()
        broker = (ROOT/"src/dkcapital/mt5_broker_events.py").read_text()
        results = (ROOT/"src/dkcapital/mt5_trade_results.py").read_text()
        recovery = (ROOT/"scripts/mt5_terminal_autoheal.py").read_text()
        self.assertIn('magic%10<=(ulong)TP6_LEG', EA)
        self.assertIn('leg not in (1, 2, 3, 4, 5, 6, 7)', reporting)
        self.assertIn('magic % 10 not in (1, 2, 3, 4, 5, 6, 7)', recovery)
        self.assertIn('for leg in (1, 2, 3, 4, 5, 6, 7):', broker)
        self.assertIn('name = "Layer (TP2)" if leg == 4', results)

    def test_provider_native_sl_and_risk_cap_present(self):
        for token in (
            'StopValid(buy,providerSL,q)',
            'double risk=RiskGBP(buy,vols[i],fill,sl);',
            'if(planned>budget+1e-7',
            'if(!CloseGroup(id))',
            'GlobalVariableSet(IDKey(id,"ATTEMPT"),(double)TimeGMT());',
        ):
            self.assertIn(token, EA)


if __name__ == "__main__":
    unittest.main()
