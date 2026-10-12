import unittest
from datetime import UTC, datetime

from dkcapital.mt5_signal_cards import broker_signal_state, mt5_group, mt5_signal_embed


class BrokerSignalCardsTest(unittest.TestCase):
    def setUp(self):
        self.signal = "-1003765237177:69961"
        self.group = mt5_group(self.signal)
        self.magic = self.group * 10 + 1
        self.snapshot = {
            "version": 2, "mode": "DEMO", "currency": "GBP", "login": 5057220041,
            "received_at": datetime.now(UTC).isoformat(), "positions": [], "deals": [],
        }

    def test_no_telegram_only_trade_claims(self):
        state = broker_signal_state(self.signal, self.snapshot)
        self.assertEqual(state["status"], "NOT FILLED ON MT5")
        self.assertFalse(state["has_broker_record"])
        embed = mt5_signal_embed({"symbol": "XAUUSD", "direction": "BUY", "tps": {"1": 4175}, "tp_hits": [1]}, state)
        self.assertIn("Not filled on MT5", embed.fields[2].value)
        self.assertNotIn("✅", embed.fields[2].value)

    def test_actual_entries_by_leg_and_closure_not_assumed_tp(self):
        self.snapshot["deals"] = [{
            "ticket": 15, "position_id": 99, "group": self.group, "magic": self.magic,
            "entry_type": "IN", "lots": 0.03, "price": 4168.25,
            "profit": 0, "commission": 0, "swap": 0,
        }, {
            "ticket": 16, "position_id": 99, "group": self.group, "magic": self.magic,
            "entry_type": "OUT", "lots": 0.03, "price": 4175,
            "profit": 15, "commission": -1, "swap": 0,
        }]
        state = broker_signal_state(self.signal, self.snapshot)
        self.assertAlmostEqual(state["legs"][1]["entry"], 4168.25)
        self.assertIn("CLOSED", state["legs"][1]["status"])
        self.assertIsNone(state["legs"][2]["entry"])
        embed = mt5_signal_embed({"direction": "BUY", "tps": {"1": 4175}, "tp_hits": [1]}, state)
        self.assertIn("4168.25", embed.fields[2].value)
        self.assertIn("CLOSED does not imply TP", embed.footer.text)

    def test_optional_layer_appears_only_with_broker_fill(self):
        state = broker_signal_state(self.signal, self.snapshot)
        self.assertNotIn(4, state["legs"])
        self.snapshot["deals"] = [{
            "ticket": 40, "position_id": 401,
            "group": self.group, "magic": self.group * 10 + 4,
            "entry_type": "IN", "lots": 0.01, "price": 4164.25,
            "profit": 0, "commission": 0, "swap": 0,
        }]
        self.snapshot["positions"] = [{
            "ticket": 401, "group": self.group, "leg": 4,
            "magic": self.group * 10 + 4, "side": "BUY",
            "lots": 0.01, "entry": 4164.25, "sl": 4150, "tp": 4175.5,
        }]
        state = broker_signal_state(self.signal, self.snapshot)
        self.assertEqual(state["legs"][4]["status"], "OPEN")
        self.assertAlmostEqual(state["legs"][4]["entry"], 4164.25)
        embed = mt5_signal_embed({"direction": "BUY", "source_style": "gws"}, state)
        self.assertIn("10% layer (TP2)", embed.fields[2].value)
        self.assertIn("4164.25", embed.fields[2].value)

    def test_stale_telemetry_cannot_confirm_fill(self):
        self.snapshot["received_at"] = "2026-01-01T00:00:00Z"
        state = broker_signal_state(self.signal, self.snapshot)
        self.assertFalse(state["fresh"])
        self.assertFalse(state["has_broker_record"])

    def test_group_hash_deterministic(self):
        self.assertEqual(mt5_group(self.signal), mt5_group(self.signal))
        self.assertNotEqual(mt5_group(self.signal), mt5_group("-1003765237177:69962"))


if __name__ == "__main__":
    unittest.main()
