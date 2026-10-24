"""V2.09 source contracts for Telegram targets 1, 2, 3 and 4.

Static Python checks do not replace isolated MetaEditor compilation and
controlled real MetaTrader demo execution tests.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
EA = (ROOT / "mt5" / "DKcapitalV2.mq5").read_text(encoding="utf-8")
GATEWAY = (ROOT / "src" / "dkcapital" / "mt5_gateway.py").read_text(encoding="utf-8")


def allocate_four(steps: int):
    if steps < 5:
        return None
    units = [1, 1, 1, 1]
    weights = [.20, .40, .20, .20]
    for _ in range(4, steps):
        best, gap = 3, -1e10
        for j in (3, 2, 1, 0):
            deficit = weights[j] * steps - units[j]
            if deficit > gap + 1e-7:
                best, gap = j, deficit
        units[best] += 1
    return units if units[1] > max(units[0], units[2], units[3]) else None


class FourTargetTests(unittest.TestCase):
    def test_four_targets_are_four_real_broker_tp_orders(self):
        self.assertIn('#property version "2.09"', EA)
        self.assertIn('const int LAYER_LEG = 4;', EA)
        self.assertIn('const int TP4_LEG = 5;', EA)
        self.assertIn('double targets[4];', EA)
        self.assertIn('targets[3]=tp4;', EA)
        self.assertIn('int initialLegCount=oneTarget?1:(fourTargets?4:3);', EA)
        self.assertIn('int leg=i==3?TP4_LEG:i+1;', EA)
        self.assertIn('if(fourTargets) GlobalVariableSet(IDKey(id,"FOUR_TARGET_ENTRY"),1);', EA)
        self.assertIn('bool wasFourTargets=GlobalVariableCheck(IDKey(id,"FOUR_TARGET_ENTRY"));', EA)
        self.assertIn('for(int leg=1;leg<=TP4_LEG;leg++)', EA)

    def test_target_distribution_at_broker_min_lot(self):
        self.assertIsNone(allocate_four(4))
        self.assertEqual(allocate_four(5), [1, 2, 1, 1])
        self.assertEqual(allocate_four(10), [2, 4, 2, 2])
        for units in range(5, 150):
            with self.subTest(units=units):
                parts = allocate_four(units)
                self.assertIsNotNone(parts)
                self.assertEqual(sum(parts), units)
                self.assertGreater(parts[1], max(parts[0], parts[2], parts[3]))
        self.assertIn('if(count<5*each) return false;', EA)
        self.assertIn('double w[4];w[0]=0.20;w[1]=0.40;w[2]=0.20;w[3]=0.20;', EA)

    def test_one_two_three_four_explicit_target_modes(self):
        for text in (
            'bool oneTarget=!openRunner && tp2<=0 && tp3<=0 && tp4<=0;',
            'bool twoTargets=!openRunner && tp2>0 && tp3<=0 && tp4<=0;',
            'bool fourTargets=!openRunner && tp2>0 && tp3>0 && tp4>0;',
            'if(tp1<=0 || ValidTP(buy,tp1,q)<=0 ||',
            'if(openRunner && !EnableOpenRunnerEntries)',
            'GlobalVariableSet(IDKey(id,"ATTEMPT"),(double)TimeGMT());',
            'GlobalVariablesFlush();',
        ):
            self.assertIn(text, EA)

    def test_four_target_feed_expanded_but_legacy_format_preserved(self):
        self.assertIn('prices.extend(float(tps.get(str(i), 0) or 0) for i in (1, 2, 3, 4))', GATEWAY)
        self.assertIn('input string FeedUrl = "http://127.0.0.1:8765/v3/demo/feed";', EA)
        self.assertIn('StringFind(lines[0],"DKDEMO3")', EA)
        self.assertIn('lines = ["DKDEMO3" if v3 else "DKDEMO2"]', GATEWAY)
        self.assertIn('if four_targets and not v3 and status == "ACTIVE":', GATEWAY)
        self.assertIn('if four_targets:', GATEWAY)
        self.assertIn('fields.append(str(prices[6]))', GATEWAY)
        self.assertIn('if((fields==12 || fields==13) && f[0]=="S")', EA)
        self.assertIn('double fourth=fields==13?StringToDouble(f[11]):0;', EA)
        self.assertIn('long openedAt=(long)StringToInteger(f[fields-1]);', EA)

    def test_layer_four_magic_unchanged_and_5_owned_by_telemetry(self):
        reporting = (ROOT/"src/dkcapital/mt5_account_reporting.py").read_text()
        broker = (ROOT/"src/dkcapital/mt5_broker_events.py").read_text()
        results = (ROOT/"src/dkcapital/mt5_trade_results.py").read_text()
        recovery = (ROOT/"scripts/mt5_terminal_autoheal.py").read_text()
        self.assertIn('magic%10<=TP4_LEG', EA)
        self.assertIn('leg not in (1, 2, 3, 4, 5)', reporting)
        self.assertIn('magic % 10 not in (1, 2, 3, 4, 5)', recovery)
        self.assertIn('"TP4" if leg == 5', broker)
        self.assertIn('"TP4 leg" if leg == 5', results)

    def test_four_target_still_requires_provider_stop_and_risk_cap(self):
        for term in (
            'StopValid(buy,providerSL,q)', 'double planned=r1+r2+r3+r4;',
            'if(planned>budget+1e-7) continue;',
            'if(fourTargets && ValidTP(buy,tp4,q)<=0)',
            'if(fourTargets) GlobalVariableSet(IDKey(id,"FOUR_TARGET_ENTRY"),1);',
            'if(leg==LAYER_LEG || (leg==TP4_LEG && !fourTargets)) continue;',
        ):
            self.assertIn(term, EA)


if __name__ == "__main__":
    unittest.main()
