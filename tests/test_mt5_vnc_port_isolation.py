"""Guard distinct, localhost-only VNC bindings for both MT5 accounts and compiler."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VncPortIsolationTests(unittest.TestCase):
    def test_all_three_vnc_ports_are_distinct_and_private(self):
        acc1 = (ROOT / "ops/recovery/dkcapital-vnc1.service").read_text()
        acc2 = (ROOT / "ops/recovery/dkcapital-vnc2.service").read_text()
        acc3 = (ROOT / "ops/recovery/dkcapital-vnc3.service").read_text()
        compiler = (ROOT / "scripts/mt5_isolated_compiler_vnc.sh").read_text()
        self.assertIn("-rfbport 5901 -localhost", acc1)
        self.assertIn("-rfbport 5902 -localhost", acc2)
        self.assertIn("-rfbport 5904 -localhost", acc3)
        self.assertIn("-display :152", acc3)
        self.assertIn("-rfbauth /var/lib/dkcapital-mt5-3/.vnc/passwd", acc3)
        self.assertNotIn("Wants=dkcapital-mt5-3.service", acc3)
        self.assertIn("-p 127.0.0.1:5903:5903", compiler)
        self.assertNotIn("-rfbport 5903", acc2)
        self.assertIn("-rfbauth /var/lib/dkcapital-mt5-2/.vnc/passwd", acc2)
        self.assertNotIn("Wants=dkcapital-mt5-2.service", acc2)
        self.assertIn("ExecStartPre=", acc2)
        self.assertIn("-display :151", acc2)

if __name__ == "__main__":
    unittest.main()
