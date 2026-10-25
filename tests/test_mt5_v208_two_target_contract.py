"""Backward compatibility coverage for 2/3-TP legacy V2.08/V2.09 tickets.

Latest V2.10 uses equal volumes, not historical TP2-majority sizing. Source
contract checks are complementary to isolated MetaEditor/broker testing.
"""
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "mt5" / "DKcapitalV2.mq5"


class TwoTargetCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_two_targets_now_use_two_equal_native_legs(self):
        s = self.source
        self.assertIn('#property version "2.10"', s)
        self.assertIn('int initialLegCount=targetCount;', s)
        self.assertIn('bool AllocateEqual(const double total,const int targetCount,double &lots[])', s)
        self.assertIn('ArrayResize(lots,targetCount);', s)
        self.assertIn('int base=totalUnits/targetCount;', s)
        self.assertIn('int leg=TargetLeg(i);', s)
        self.assertIn('for(int i=0;i<initialLegCount;i++)', s)
        self.assertIn('GlobalVariableSet(IDKey(id,"TARGET_COUNT"),targetCount);', s)

    def test_historical_2tp_3leg_remapping_and_legacy_magic_remain(self):
        s = self.source
        self.assertIn('bool wasTwoTargets=GlobalVariableCheck(IDKey(id,"TWO_TARGET_ENTRY"));', s)
        self.assertIn('(leg==3 && wasTwoTargets)?tp2:', s)
        self.assertIn('const int LAYER_LEG = 4;', s)
        self.assertIn('if(leg!=LAYER_LEG && magic==LegMagic(id,leg))', s)
        self.assertIn('HistoryDealsTotal()', s)

    def test_provider_stop_and_trade_duplicate_safety_remain(self):
        s = self.source
        for token in (
            'StopValid(buy,providerSL,q)',
            'RiskGBP(buy,vols[i],fill,sl)',
            'if(planned>budget+1e-7',
            'if(!EnableOpenRunnerEntries)',
            'RecordDecision(id,"ENTRY","MISSING_OR_INVALID_NATIVE_TP",age);',
            'GlobalVariableSet(IDKey(id,"ATTEMPT"),(double)TimeGMT());',
            'GlobalVariablesFlush();',
            'if(!CloseGroup(id))',
        ):
            self.assertIn(token, s)

    def test_explicit_tp3_open_still_requires_separate_approval(self):
        s = self.source
        self.assertIn('input bool EnableOpenRunnerEntries = false;', s)
        self.assertIn('if(openRunner)', s)
        self.assertIn('if(!EnableOpenRunnerEntries)', s)
        self.assertIn('double tp=(openRunner && i==2)?0:ValidTP(buy,target,q);', s)


if __name__ == "__main__":
    unittest.main()
