"""Pure, read-only quality calculations: no broker trading permissions."""
import unittest
from datetime import UTC, datetime, timedelta

from dkcapital.execution_quality import edit_ready, execution_quality, planned_rr
from dkcapital.mt5_signal_cards import mt5_group


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(UTC)
        self.sell = {
            "signal_id": "-1000:1", "symbol": "XAUUSD",
            "source_style": "gws", "direction": "SELL",
            "entry_low": 3997, "entry_high": 4003,
            "current_sl": 4005, "tps": {"1": 3995},
            "opened_at": (self.now - timedelta(seconds=80)).isoformat(),
            "last_update_at": (self.now - timedelta(seconds=10)).isoformat(),
        }

    def test_provider_quiet_period_uses_latest_edit(self):
        self.assertFalse(edit_ready(self.sell, self.now)["ready"])
        self.sell["last_update_at"] = (self.now - timedelta(seconds=50)).isoformat()
        self.assertTrue(edit_ready(self.sell, self.now)["ready"])

    def test_single_tp_below_one_r_is_advisory(self):
        result = planned_rr(self.sell)
        self.assertTrue(result["single_tp_below_1r"])
        self.assertEqual(result["tp_count"], 1)
        self.assertEqual(result["rr"], 0.25)

    def test_equal_weight_four_target_rr(self):
        self.sell["tps"] = {"1":3995, "2":3994, "3":3993, "4":3990}
        self.assertEqual(planned_rr(self.sell)["tp_count"], 4)
        self.assertEqual(planned_rr(self.sell)["rr"], 0.5)

    def test_invalid_targets_fail_closed_for_estimates(self):
        self.sell["tps"] = {"1":3995, "3":3990}
        self.assertIsNone(planned_rr(self.sell)["rr"])

    def test_telemetry_must_be_fresh(self):
        result = execution_quality([self.sell], {"version":2,"received_at":
            (self.now-timedelta(hours=1)).isoformat(),"positions":[]}, now=self.now)
        self.assertFalse(result["verified"])

    def test_open_position_management_not_suppressed(self):
        snap = {"version":2, "received_at":self.now.isoformat(),
                "positions":[{"ticket":1,"group":mt5_group("-1000:1"),
                              "side":"SELL","entry":3999,"sl":4005,"lots":0.02}]}
        result = execution_quality([self.sell], snap, now=self.now)
        self.assertTrue(result["verified"])
        self.assertIn("OPEN_POSITION_MANAGEMENT_MUST_CONTINUE",
                      [w["code"] for w in result["warnings"]])
        self.assertEqual(result["signals"][0]["actual_average_entry"], 3999)
        self.assertFalse(result["breakeven_floor_enforced"])

    def test_opposing_provider_positions_warn_only(self):
        other = {**self.sell, "signal_id":"-1000:2", "source_style":"elite",
                 "direction":"BUY", "entry_low":4000, "entry_high":4002,
                 "current_sl":3990, "tps":{"1":4015}}
        snap = {"version":2, "received_at":self.now.isoformat(),
                "positions":[
                    {"ticket":1,"group":mt5_group("-1000:1"),"side":"SELL",
                     "entry":3999,"sl":4005,"lots":0.02},
                    {"ticket":2,"group":mt5_group("-1000:2"),"side":"BUY",
                     "entry":4001,"sl":3990,"lots":0.02}]}
        result = execution_quality([self.sell,other],snap,now=self.now)
        self.assertIn("CONFLICTING_OPEN_DIRECTIONS",
                      [w["code"] for w in result["warnings"]])


if __name__ == "__main__":
    unittest.main()
