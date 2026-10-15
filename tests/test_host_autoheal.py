"""Tests for bounded opt-in DK Capital host-side recovery."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "scripts/host_autoheal.py"
spec = importlib.util.spec_from_file_location("host_autoheal", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HostRecoveryTests(unittest.TestCase):
    def test_waits_for_persistent_failure(self):
        state, worker, _ = module.plan({}, {"SIGNAL_PROCESSOR_HEARTBEAT_STALE"}, 1000)
        self.assertIsNone(worker)
        _, worker, _ = module.plan(state, {"SIGNAL_PROCESSOR_HEARTBEAT_STALE"}, 1179)
        self.assertIsNone(worker)
        _, worker, _ = module.plan(state, {"SIGNAL_PROCESSOR_HEARTBEAT_STALE"}, 1181)
        self.assertEqual(worker, "signal-processor")

    def test_mt5_stale_is_alert_only(self):
        _, worker, notes = module.plan({}, {"MT5_ACCOUNT1_TELEMETRY_STALE"}, 100000)
        self.assertIsNone(worker)
        self.assertTrue(any("ALERT_ONLY" in n for n in notes))

    def test_cooldown_and_attempt_budget(self):
        issue = "DISCORD_BOT_HEARTBEAT_STALE"
        state = {"incidents": {issue: {"first_seen": 1, "attempts": [1200, 2400]}}}
        _, worker, notes = module.plan(state, {issue}, 2600)
        self.assertIsNone(worker)
        self.assertTrue(any("budget exceeded" in n for n in notes))
        state = {"incidents": {issue: {"first_seen": 1, "attempts": [2400]}}}
        _, worker, notes = module.plan(state, {issue}, 2600)
        self.assertIsNone(worker)
        self.assertTrue(any("cooldown" in n for n in notes))

    def test_recovery_clears_incident_state(self):
        state = {"incidents": {
            "GATEWAY_HEALTH_UNAVAILABLE": {"first_seen": 1, "attempts": []}
        }}
        updated, worker, _ = module.plan(state, set(), 20000)
        self.assertEqual(updated["incidents"], {})
        self.assertIsNone(worker)

    def test_only_one_service_selected(self):
        incidents = {
            issue: {"first_seen": 1, "attempts": []}
            for issue in module.RESTARTABLE
        }
        _, worker, _ = module.plan({"incidents": incidents}, set(incidents), 1000)
        self.assertIn(worker, set(module.RESTARTABLE.values()))


if __name__ == "__main__":
    unittest.main()
