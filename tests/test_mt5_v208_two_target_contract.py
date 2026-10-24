"""Source contracts for V2.08 two/three-target profit distribution.

These guard source intent only. MetaEditor compilation and broker demo testing
are still required; CI Python tests cannot certify compiled MQL execution.
"""
from pathlib import Path
import re
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "mt5" / "DKcapitalV2.mq5"


def allocation(total_units: int, *, two_targets: bool):
    """Mirror the EA's discrete lot-step target allocation for regression cases."""
    if total_units < 3:
        return None
    units = [1, 1, 1]
    weights = [0.25, 0.375 if two_targets else 0.50,
               0.375 if two_targets else 0.25]
    for _ in range(3, total_units):
        best = 2
        gap = -1e10
        for j in (2, 1, 0):
            missing = weights[j] * total_units - units[j]
            if missing > gap + 1e-7:
                gap = missing
                best = j
        units[best] += 1
    a, b, c = units
    if two_targets:
        return units if b + c > a else None
    return units if b > a and b > c else None


class Tp2MajorityContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_v208_explicit_two_target_policy_is_persistent(self):
        s = self.source
        self.assertIn('#property version "2.08"', s)
        self.assertIn('bool twoTargets=!openRunner && tp3<=0;', s)
        self.assertIn('if(twoTargets) GlobalVariableSet(IDKey(id,"TWO_TARGET_ENTRY"),1);', s)
        self.assertIn('GlobalVariableFlush', s.replace("GlobalVariablesFlush", "GlobalVariableFlush"))
        self.assertIn('bool wasTwoTargets=GlobalVariableCheck(IDKey(id,"TWO_TARGET_ENTRY"));', s)
        self.assertIn('(leg==3 && wasTwoTargets)?tp2:tp3', s)
        self.assertIn('targets[1]=tp2;targets[2]=openRunner?0:(twoTargets?tp2:tp3);', s)
        self.assertIn('!twoTargets && !openRunner && ValidTP(buy,tp3,q)<=0', s)

    def test_native_three_leg_stops_and_broker_risk_controls_preserved(self):
        entry = self.source.split("void TryEntry(", 1)[1].split(
            "// Original three-leg broker fills", 1
        )[0]
        for code in ('trade.Buy(', 'trade.Sell(', '"BROKER_ORDER_REJECTED_OR_PARTIAL"',
                     'GlobalVariableSet(IDKey(id,"ATTEMPT")', 'StopValid(buy,providerSL,q)',
                     'RiskGBP(buy,a,fill,sl)', 'RiskGBP(buy,b,fill,sl)',
                     'RiskGBP(buy,c,fill,sl)', 'SignalRiskLimitGBP()', 'ValidTP(buy,tp1,q)',
                     'ValidTP(buy,tp2,q)', 'if(openRunner && !EnableOpenRunnerEntries)'):
            self.assertIn(code, entry)
        self.assertIn('return twoTargets ? b+c>a+1e-7 : (b>a+1e-7 && b>c+1e-7);',
                      self.source)

    def test_two_targets_largest_exit_tp2_at_all_allowed_lot_sizes(self):
        for count in range(3, 90):
            with self.subTest(steps=count):
                result = allocation(count, two_targets=True)
                self.assertIsNotNone(result)
                a, b, c = result
                self.assertGreater(b+c, a)
                self.assertEqual(a+b+c, count)

    def test_three_targets_never_equal_tp2_at_minimum_size(self):
        self.assertIsNone(allocation(3, two_targets=False))
        for count in range(4, 90):
            with self.subTest(steps=count):
                result = allocation(count, two_targets=False)
                self.assertIsNotNone(result)
                a, b, c = result
                self.assertGreater(b, a)
                self.assertGreater(b, c)
                self.assertEqual(a+b+c, count)

    def test_no_unintended_tp3_or_open_runner(self):
        s = self.source
        self.assertIn('bool twoTargets=!openRunner && tp3<=0;', s)
        self.assertIn('if(tp1<=0 || tp2<=0 ||', s)
        self.assertIn('if(openRunner && !EnableOpenRunnerEntries)', s)
        self.assertIn('bool wasTwoTargets=GlobalVariableCheck', s)
        # A missing numeric third target is NEVER promoted to the OPEN option.
        self.assertIn('targets[2]=openRunner?0:(twoTargets?tp2:tp3)', s)


if __name__ == "__main__":
    unittest.main()
