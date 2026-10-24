"""End-to-end deterministic MT5 recovery policy tests: no real broker or restarts."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("dk_mt5_recovery", ROOT/"scripts/mt5_recovery_supervisor.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)

T0 = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def profile(ident=1, mode="DEMO", enabled=True):
    return {
        "id": ident, "label": f"Account {ident}",
        "expected_login": 5057220041 if ident == 1 else 998877,
        "expected_mode": mode, "expected_currency": "GBP" if ident == 1 else "USD",
        "expect_entries": False,
        "auto_recover": enabled, "recovery_policy": "broker_protected" if enabled else "disabled",
    }


def snapshot(p, now, *, protected=True, capability=True, positions=None):
    pos = positions if positions is not None else [{
        "ticket": 881, "side": "BUY", "entry": 4100, "sl": 4090, "lots": .1
    }]
    return {
        "mode": p["expected_mode"], "currency": p["expected_currency"],
        "login": p["expected_login"],
        "received_at": now.isoformat(), "generated_at": now.isoformat(),
        "feed_http": 200, "entry_enabled": False,
        "restart_reconciliation_supported": capability,
        "broker_positions_total": len(pos), "broker_orders_total": 0,
        "unmanaged_positions_count": 0, "unmanaged_orders_count": 0,
        "positions": pos, "orders": [], "broker_positions_protected": protected,
    }


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def save(self, p, data):
        (self.data/f"mt5-account{p['id']}.json").write_text(json.dumps(data))

    def test_live_frozen_terminal_auto_restarts_with_known_native_stops(self):
        p = profile(3, "REAL")
        self.save(p, snapshot(p, T0))
        prev = {}
        with patch.object(recovery, "service_state", return_value=(True, "active")), \
             patch.object(recovery.subprocess, "run") as call:
            call.return_value.returncode = 0
            status, events, prev = recovery.cycle([p], self.data, prev, T0 + timedelta(seconds=151), apply=True, gateway_ok=True)
            self.assertEqual(status["accounts"]["3"]["result"], "NO_RESTART")
            self.assertEqual(events, [])
            status, events, prev = recovery.cycle([p], self.data, prev, T0 + timedelta(seconds=330), apply=True, gateway_ok=True)
            self.assertEqual(status["accounts"]["3"]["result"], "NO_RESTART")
            status, events, prev = recovery.cycle([p], self.data, prev, T0 + timedelta(seconds=332), apply=True, gateway_ok=True)
            self.assertEqual(status["accounts"]["3"]["result"], "RESTART_REQUESTED")
            self.assertEqual(call.call_args.args[0], ["systemctl", "restart", "dkcapital-mt5-3.service"])
            self.assertEqual(len(prev["accounts"]["3"]["attempts"]), 1)
            self.assertEqual(len(prev["events"]), 1)
            # After fresh broker data the supervisor acknowledges recovery.
            self.save(p, snapshot(p, T0 + timedelta(seconds=349)))
            status, events, prev = recovery.cycle([p], self.data, prev, T0 + timedelta(seconds=355), apply=True, gateway_ok=True)
            self.assertEqual(status["accounts"]["3"]["result"], "RECOVERED")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["result"], "RECOVERED")
            self.assertEqual(len(prev["events"]), 2)

    def test_fresh_broker_quote_does_not_restart(self):
        p = profile(1)
        self.save(p, snapshot(p, T0))
        allowed, reason = recovery.broker_recovery_gate(
            p, snapshot(p, T0), now=T0+timedelta(seconds=25),
            gateway_ok=True, service_active=True,
        )
        self.assertFalse(allowed)
        self.assertIn("fresh", reason)

    def test_live_missing_explicit_reconciliation_marker_blocks_restart(self):
        p = profile(2, "REAL")
        before = snapshot(p, T0, capability=False)
        allowed, reason = recovery.broker_recovery_gate(
            p, before, now=T0+timedelta(seconds=320), gateway_ok=True,
            service_active=True,
        )
        self.assertFalse(allowed)
        self.assertIn("reconciliation", reason)

    def test_unprotected_or_unmanaged_broker_positions_block(self):
        p = profile(3, "REAL")
        for modified in (
            {"broker_positions_protected": False},
            {"broker_positions_total": 2, "unmanaged_positions_count": 1},
            {"broker_orders_total": 1, "orders": []},
            {"unmanaged_orders_count": 1},
            {"positions": [{"ticket": 1, "side": "BUY", "entry": 4100, "sl": 0, "lots": .1}]},
            {"mode": "DEMO"}, {"login": 123}, {"entry_enabled": True},
        ):
            before = {**snapshot(p, T0), **modified}
            allowed, reason = recovery.broker_recovery_gate(
                p, before, now=T0+timedelta(seconds=320), gateway_ok=True,
                service_active=True,
            )
            self.assertFalse(allowed, modified)

    def test_upstream_outage_and_ancient_snapshot_block(self):
        p = profile(3, "REAL")
        before = snapshot(p, T0)
        self.assertFalse(recovery.broker_recovery_gate(
            p, before, now=T0+timedelta(seconds=320), gateway_ok=False,
            service_active=True,
        )[0])
        self.assertFalse(recovery.broker_recovery_gate(
            p, before, now=T0+timedelta(seconds=650), gateway_ok=True,
            service_active=True,
        )[0])

    def test_timer_cooldown_and_restart_limit_per_account(self):
        state, can, reason = recovery.restart_plan({}, True, 1000)
        self.assertFalse(can)
        state, can, reason = recovery.restart_plan(state, True, 1180)
        self.assertTrue(can)
        state["attempts"] = [1180]
        _, can, reason = recovery.restart_plan(state, True, 1300)
        self.assertFalse(can)
        self.assertIn("cooldown", reason)
        state["attempts"] = [1180, 2300]
        _, can, reason = recovery.restart_plan(state, True, 2400)
        self.assertFalse(can)
        self.assertIn("limit", reason)

    def test_disabled_accounts_and_systemd_crash_are_never_force_restarted(self):
        disabled = profile(2, "REAL", enabled=False)
        self.save(disabled, snapshot(disabled, T0))
        with patch.object(recovery, "service_state", return_value=(False, "service_down")), \
             patch.object(recovery.subprocess, "run") as runner:
            status, events, _ = recovery.cycle(
                [disabled], self.data, {}, T0+timedelta(seconds=300),
                apply=True, gateway_ok=True,
            )
            self.assertEqual(status["accounts"]["2"]["result"], "DISABLED")
            self.assertFalse(runner.called)
        commissioned = profile(2, "REAL")
        with patch.object(recovery, "service_state", return_value=(False, "unapproved_terminal_binary_or_LiveUpdate_exit78")), \
             patch.object(recovery.subprocess, "run") as runner:
            status, events, _ = recovery.cycle(
                [commissioned], self.data, {}, T0+timedelta(seconds=300),
                apply=True, gateway_ok=True,
            )
            self.assertEqual(status["accounts"]["2"]["result"], "SERVICE_DOWN")
            self.assertFalse(runner.called)

    def test_post_restart_requires_new_matching_telemetry_and_feed(self):
        p = profile(3, "REAL")
        stamp = (T0+timedelta(seconds=300)).isoformat()
        before = snapshot(p, T0+timedelta(seconds=310))
        self.assertTrue(recovery._recovered(before, p, T0+timedelta(seconds=320), stamp))
        for change in ({"feed_http": 1003}, {"restart_reconciliation_supported": False},
                       {"broker_positions_protected": False}, {"login": 8},
                       {"received_at": (T0+timedelta(seconds=295)).isoformat()}):
            self.assertFalse(recovery._recovered(
                {**before, **change}, p, T0+timedelta(seconds=320), stamp,
            ), change)


if __name__ == "__main__":
    unittest.main()
