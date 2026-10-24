"""Host-side MT5 status probe and per-account gated recovery tests."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE / "src"))


def import_script(name):
    spec = importlib.util.spec_from_file_location(name, BASE / "scripts" / f"{name}.py")
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


audit = import_script("mt5_profile_audit")
heal = import_script("mt5_terminal_autoheal")


class ProfileHostAuditTests(unittest.TestCase):
    def test_new_service_status_has_no_false_restart_alert(self):
        now = datetime.now(UTC)
        with patch.object(audit, "inspect_unit", return_value={
            "active": True, "restart_count": 0, "main_pid": 123,
        }):
            first = audit.build_snapshot([{"id": 3}], {}, now)
        self.assertIsNone(first["accounts"]["3"]["last_restart_at"])

    def test_restarts_detected_only_for_correct_account(self):
        now = datetime.now(UTC)
        before = {
            "accounts": {
                "1": {"restart_count": 1, "main_pid": 100},
                "3": {"restart_count": 1, "main_pid": 300},
            }
        }
        def inspect(ident):
            return {
                "active": True,
                "restart_count": 1 if ident == 1 else 2,
                "main_pid": 100 if ident == 1 else 400,
            }
        with patch.object(audit, "inspect_unit", side_effect=inspect):
            out = audit.build_snapshot([{"id": 1}, {"id": 3}], before, now)
        self.assertIsNone(out["accounts"]["1"]["last_restart_at"])
        self.assertEqual(out["accounts"]["3"]["last_restart_at"], now.isoformat())

    def test_host_audit_is_strictly_read_only(self):
        source = (BASE / "scripts/mt5_profile_audit.py").read_text()
        self.assertNotIn('["systemctl", "restart"', source)
        self.assertNotIn('["systemctl", "start"', source)
        self.assertNotIn("order_send(", source)


class MultiAccountAutohealTests(unittest.TestCase):
    def test_legacy_account1_service_unchanged_and_per_account_files_staged(self):
        service = (BASE/"deployment/dkcapital-mt5-autoheal.service").read_text()
        self.assertIn("mt5_terminal_autoheal.py --apply", service)
        for ident in (2, 3):
            unit = (BASE/f"deployment/dkcapital-mt5-autoheal-account{ident}.service").read_text()
            self.assertIn(f"--account-id {ident} --apply", unit)
            self.assertNotIn("dkcapital-mt5-1.service", unit)
        boot = (BASE/"ops/recovery/install-recovery.sh").read_text()
        self.assertNotIn("dkcapital-mt5-autoheal-account2", boot)
        self.assertNotIn("dkcapital-mt5-autoheal-account3", boot)

    def test_recovery_gate_fails_closed_for_real_account(self):
        now = datetime.now(UTC)
        base = {
            "mode": "REAL", "currency": "GBP", "login": 321,
            "entry_enabled": True,
            "received_at": (now-timedelta(minutes=4)).isoformat(),
            "generated_at": (now-timedelta(minutes=4)).isoformat(),
        }
        good, reason = heal.recovery_gate(
            base, expected_login=321, now=now,
            signals_ambiguous=False, gateway_ok=True, service_active=True,
        )
        self.assertFalse(good)
        self.assertIn("DEMO", reason)

    def test_account_2_demo_recovery_cannot_restart_account_1_or_3(self):
        with patch.object(heal.subprocess, "run") as spy:
            spy.return_value.returncode = 0
            self.assertTrue(heal.service_running("dkcapital-mt5-2.service"))
            self.assertEqual(
                spy.call_args.args[0],
                ["systemctl", "is-active", "--quiet", "dkcapital-mt5-2.service"],
            )


if __name__ == "__main__":
    unittest.main()
