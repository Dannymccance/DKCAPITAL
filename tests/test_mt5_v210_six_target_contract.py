"""V2.11 source contracts: 1-6 separate native targets and equal volume.

These checks are static. Actual .mq5 -> .ex5 compilation and broker DEMO
execution tests are still mandatory before replacing V2.07 on Account 1.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
EA = (ROOT / "mt5/DKcapitalV2.mq5").read_text(encoding="utf-8")
GATEWAY = (ROOT / "src/dkcapital/mt5_gateway.py").read_text(encoding="utf-8")


def allocate(total_steps: int, targets: int, minimum_steps: int = 1):
    if not 1 <= targets <= 6 or total_steps < targets * minimum_steps:
        return None
    base, remainder = divmod(total_steps, targets)
    result = [base + (i < remainder) for i in range(targets)]
    if min(result) < minimum_steps:
        return None
    return result


class SixTargetContracts(unittest.TestCase):
    def test_allocation_balances_total_lots_within_one_volume_step(self):
        for targets in range(1, 7):
            for total in range(1, 180):
                with self.subTest(targets=targets, total=total):
                    result = allocate(total, targets)
                    if total < targets:
                        self.assertIsNone(result)
                    else:
                        self.assertEqual(sum(result), total)
                        self.assertEqual(len(result), targets)
                        self.assertLessEqual(max(result)-min(result), 1)
        self.assertEqual(allocate(12, 6), [2]*6)
        self.assertEqual(allocate(12, 5), [3, 3, 2, 2, 2])
        self.assertIsNone(allocate(5, 6))
        self.assertEqual(allocate(12, 3), [4, 4, 4])

    def test_source_equal_allocator_and_target_count_limits(self):
        for token in (
            '#property version "2.11"',
            'bool AllocateEqual(const double total,const int targetCount,double &lots[])',
            'int totalUnits=(int)MathFloor(total/step+1e-7);',
            'if(totalUnits<targetCount*minimumUnits) return false;',
            'int base=totalUnits/targetCount;',
            'int remainder=totalUnits%targetCount;',
            'int units=base+(i<remainder?1:0);',
            'int initialLegCount=targetCount;',
            'if(!AllocateEqual(total,initialLegCount,vols)) continue;',
            'GlobalVariableSet(IDKey(id,"TARGET_COUNT"),targetCount);',
            'for(int i=0;i<initialLegCount;i++)',
            'if(planned>budget+1e-7',
        ):
            self.assertIn(token, EA)

    def test_broker_magic_suffix_preserves_gws_layer(self):
        for token in (
            'const int LAYER_LEG = 4;',
            'const int TP4_LEG = 5;',
            'const int TP5_LEG = 6;',
            'const int TP6_LEG = 7;',
            'return i<3?i+1:i+2;',
            'magic%10<=(ulong)TP6_LEG',
            'for(int leg=1;leg<=TP6_LEG;leg++)',
            'for(int leg=TP6_LEG;leg>=1 && needed>=LotMin()-1e-8;leg--)',
        ):
            self.assertIn(token, EA)

    def test_six_target_wire_contract_and_old_versions(self):
        self.assertIn('input string FeedUrl = "http://127.0.0.1:8765/v5/demo/feed";', EA)
        self.assertIn('StringFind(lines[0],"DKDEMO5")', EA)
        self.assertIn('if(fields==16 && f[0]=="S")', EA)
        self.assertIn('double fifth=StringToDouble(f[12]);', EA)
        self.assertIn('double sixth=StringToDouble(f[13]);', EA)
        self.assertIn('lines = [profile_header or ("DKDEMO5" if v5 else', GATEWAY)
        self.assertIn('self.path in ("/v2/demo/feed", "/v3/demo/feed", "/v4/demo/feed", "/v5/demo/feed")', GATEWAY)
        self.assertIn('supported = 6 if (v4 or v5) else (4 if v3 else 3)', GATEWAY)
        self.assertIn('if (count > supported or (signal.get("high_risk") and not v5)) and status == "ACTIVE":', GATEWAY)
        self.assertIn('nums != list(range(1, len(nums) + 1))', GATEWAY)

    def test_lot_size_respects_provider_stop_and_group_budget(self):
        for token in (
            'StopValid(buy,providerSL,q)',
            'double signalRemaining=signalLimit-groupRisk;',
            'double budget=MathMin(groupLimit*INITIAL_GROUP_RISK_SHARE-groupRisk,',
            'double risk=RiskGBP(buy,vols[i],fill,sl);',
            'planned+=risk;',
            'if(planned>budget+1e-7',
            'if(IsUKFriday()) targetLots=MathMin(targetLots,0.03);',
            'RecordDecision(id,"ENTRY","BROKER_RISK_VALUATION_FAILED",age);',
            'GlobalVariableSet(IDKey(id,"ATTEMPT"),(double)TimeGMT());',
            'if(!CloseGroup(id))',
            'double nativeTP=ticketValid?PositionGetDouble(POSITION_TP):-1;',
            'bool tpVerified=ticketValid && (tp>0 ?',
            'PositionGetDouble(POSITION_SL)<=0 || !tpVerified ||',
        ):
            self.assertIn(token, EA)

    def test_no_automatic_open_runner_and_no_synthetic_tp(self):
        self.assertIn('if(!EnableOpenRunnerEntries)', EA)
        self.assertIn('if(openRunner && (fourth>0 || fifth>0 || sixth>0)) continue;', EA)
        self.assertIn('double tp=(openRunner && i==2)?0:ValidTP(buy,target,q);', EA)
        self.assertIn('if(tp<=0 && !(openRunner && i==2))', EA)


if __name__ == "__main__":
    unittest.main()
