from __future__ import annotations

import unittest
from decimal import Decimal

from dkcapital.bybit_demo_engine import (
    _floor_step,
    _provider_stop_is_structurally_valid,
    _temporary_stop,
    _tp_weights,
)


class BybitDemoHelpersTests(unittest.TestCase):
    def test_floor_step_never_rounds_up(self) -> None:
        self.assertEqual(
            _floor_step(Decimal("1.239"), Decimal("0.01")),
            Decimal("1.23"),
        )

    def test_buy_temporary_stop_is_below_zone_and_fill(self) -> None:
        signal = {
            "direction": "BUY",
            "entry_low": 4148.0,
            "entry_high": 4151.0,
        }
        self.assertEqual(_temporary_stop(signal, 4158.0), 4142.0)

    def test_sell_temporary_stop_is_above_zone_and_fill(self) -> None:
        signal = {
            "direction": "SELL",
            "entry_low": 4148.0,
            "entry_high": 4151.0,
        }
        self.assertEqual(_temporary_stop(signal, 4140.0), 4157.0)

    def test_provider_stop_must_be_on_protective_side_of_entry_zone(self) -> None:
        buy = {
            "direction": "BUY",
            "entry_low": 4148.0,
            "entry_high": 4151.0,
        }
        sell = {
            "direction": "SELL",
            "entry_low": 4148.0,
            "entry_high": 4151.0,
        }
        self.assertTrue(_provider_stop_is_structurally_valid(buy, 4142.0))
        self.assertFalse(_provider_stop_is_structurally_valid(buy, 4342.0))
        self.assertTrue(_provider_stop_is_structurally_valid(sell, 4157.0))
        self.assertFalse(_provider_stop_is_structurally_valid(sell, 4142.0))

    def test_three_targets_use_existing_30_30_40_split(self) -> None:
        weights = _tp_weights({"tps": {"1": 4170, "2": 4180, "3": 4200}})
        self.assertAlmostEqual(weights[1], 0.30)
        self.assertAlmostEqual(weights[2], 0.30)
        self.assertAlmostEqual(weights[3], 0.40)


if __name__ == "__main__":
    unittest.main()
