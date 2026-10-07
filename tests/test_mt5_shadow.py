from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dkcapital.mt5_shadow import (
    floor_to_step,
    is_stale_signal,
    lots_for_risk,
    protective_stop_for_signal,
    signal_fingerprint,
    temporary_stop,
)


class Mt5ShadowHelpersTests(unittest.TestCase):
    def test_floor_to_step_never_rounds_up(self) -> None:
        self.assertEqual(floor_to_step(1.239, 0.01), 1.23)

    def test_lot_sizing_uses_account_currency_loss_per_lot(self) -> None:
        volume, risk = lots_for_risk(
            equity=100_000,
            risk_pct=0.005,
            loss_per_lot=1_000,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        self.assertEqual(volume, 0.5)
        self.assertEqual(risk, 500.0)

    def test_lot_sizing_does_not_round_up_to_minimum(self) -> None:
        volume, risk = lots_for_risk(
            equity=1_000,
            risk_pct=0.001,
            loss_per_lot=1_000,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )
        self.assertEqual(volume, 0.0)
        self.assertEqual(risk, 0.0)

    def test_temporary_stop_matches_existing_strategy_shape(self) -> None:
        signal = {"direction": "BUY", "entry_low": 4148.0, "entry_high": 4151.0}
        self.assertEqual(temporary_stop(signal, 4158.0), 4142.0)

    def test_valid_provider_stop_is_preferred(self) -> None:
        signal = {
            "direction": "BUY",
            "entry_low": 4148.0,
            "entry_high": 4151.0,
            "current_sl": 4140.0,
            "sl_mode": "PRICE",
        }
        stop, source = protective_stop_for_signal(signal, 4152.0)
        self.assertEqual(stop, 4140.0)
        self.assertEqual(source, "PROVIDER")

    def test_bad_provider_stop_falls_back_to_temporary(self) -> None:
        signal = {
            "direction": "BUY",
            "entry_low": 4148.0,
            "entry_high": 4151.0,
            "current_sl": 4160.0,
            "sl_mode": "PRICE",
        }
        stop, source = protective_stop_for_signal(signal, 4158.0)
        self.assertEqual(stop, 4142.0)
        self.assertEqual(source, "TEMPORARY")

    def test_signal_fingerprint_is_stable_and_changes_with_management(self) -> None:
        base = {"signal_id": "1:2", "status": "ACTIVE", "tp_hits": []}
        first = signal_fingerprint(base)
        second = signal_fingerprint(dict(base))
        changed = signal_fingerprint({**base, "tp_hits": [1]})
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_stale_signal_guard(self) -> None:
        now = datetime.now(UTC)
        old = {"opened_at": (now - timedelta(minutes=10)).isoformat()}
        fresh = {"opened_at": (now - timedelta(seconds=30)).isoformat()}
        self.assertTrue(is_stale_signal(old, now=now, max_age_seconds=180))
        self.assertFalse(is_stale_signal(fresh, now=now, max_age_seconds=180))


class Mt5ShadowSafetyBoundaryTests(unittest.TestCase):
    def test_windows_executor_contains_no_order_send_path(self) -> None:
        executor = Path(__file__).resolve().parents[1] / "windows" / "mt5_shadow_executor.py"
        source = executor.read_text(encoding="utf-8")
        self.assertNotIn("order_send(", source)
        self.assertIn('"shadow_only"', source)


if __name__ == "__main__":
    unittest.main()
