"""V2.09 release path must never mutate MT5 broker terminals or arming.

The Linux script itself is intentionally a human-gated operation: test that it
parses and that candidate paths, protections and immutable-source guards exist.
"""
from pathlib import Path
import os
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/release_mt5_v210.sh"


class ReleaseScriptSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_bash_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_compiler_isolated_from_trading_prefix(self):
        s = self.text
        self.assertIn("/opt/dkcapital-mt5-compiler/workspace", s)
        self.assertIn("DKcapitalV210_TP1to6_SignFix1", s)
        self.assertIn("stage-compiler)", s)
        self.assertIn("verify-compiler)", s)
        self.assertIn("DKDEMO4", s)
        self.assertNotIn("systemctl restart", s)
        self.assertNotIn("systemctl stop", s)
        self.assertNotIn("systemctl start", s)
        self.assertNotIn("EnableNewEntries=true", s)

    def test_each_terminal_must_be_active_individually(self):
        self.assertIn("terminals() {", self.text)
        fn = "terminals() {" + self.text.split("terminals() {", 1)[1].split(
            "\nflat_disarmed() {", 1
        )[0].rstrip()
        shell = (
            'fail() { exit 2; }\n'
            'systemctl() { if [[ "$2" == "dkcapital-mt5-1" ]]; '
            'then printf "%s\n" "$DEMO_STATE"; '
            'else printf "%s\n" "$KYLE_STATE"; fi; }\n'
            + fn + "\nterminals\n"
        )
        for demo, kyle, success in (
            ("active", "active", True),
            ("failed", "active", False),
            ("active", "failed", False),
            ("inactive", "inactive", False),
        ):
            with self.subTest(demo=demo, kyle=kyle):
                env = {**os.environ, "DEMO_STATE": demo, "KYLE_STATE": kyle}
                result = subprocess.run(
                    ["bash", "-c", shell], env=env,
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode == 0, success, result.stderr)
                self.assertIn(f"dkcapital-mt5-1: {demo}", result.stdout)
                self.assertIn(f"dkcapital-mt5-2: {kyle}", result.stdout)

    def test_backend_no_other_container_restarts_and_backups(self):
        s = self.text
        self.assertIn("docker compose --profile mt5-shadow build signal-processor mt5-gateway", s)
        self.assertIn("up -d --no-deps --no-build signal-processor mt5-gateway", s)
        self.assertNotIn("docker compose down", s)
        self.assertIn('shutil.copy2(p,dest/name)', s)
        self.assertIn('"telegram-events.jsonl","signal-state.json","mt5-account1.json"', s)
        self.assertGreaterEqual(s.count("flat_disarmed"), 3)
        self.assertIn("backend_probe", s)

    def test_release_requires_expected_broker_identity_and_disarm(self):
        s = self.text
        self.assertIn('"GBP DEMO login 5057220041"', s)
        self.assertIn('x.get("entry_enabled") is False', s)
        self.assertIn('x.get("broker_positions_total")==0', s)
        self.assertIn('x.get("broker_orders_total")==0', s)
        self.assertIn('x.get("feed_http") in (200,404)', s)
        self.assertIn('0<=age<120', s)
        self.assertIn("source_check", s)
        self.assertIn('branch --show-current', s)

    def test_staging_only_does_not_require_a_preexisting_attached_ea(self):
        s = self.text
        stage = s.split(" stage-demo)", 1)[1].split(" ;;", 1)[0]
        self.assertIn("root_only;source_check;terminals", stage)
        self.assertNotIn("flat_disarmed", stage)
        self.assertNotIn("backend_probe", stage)
        self.assertIn('install -o dkmt5 -g dkmt5 -m 0644 "$ARTIFACT" "$TARGET"', stage)
        self.assertIn('cmp -s "$ARTIFACT" "$TARGET"', stage)
        self.assertIn('[[ -d "$DEMO" && ! -e "$TARGET" ]]', stage)
        self.assertIn('"${METAEDITOR_ERRORS:-unset}" == 0', stage)
        self.assertIn('"${METAEDITOR_WARNINGS:-unset}" == 0', stage)
        self.assertIn("Nothing attached, restarted, or enabled", stage)

    def test_backend_bootstrap_requires_real_flat_disarmed_broker_telemetry(self):
        s = self.text
        gate = s.split("flat_disarmed() {", 1)[1].split("backend_probe()", 1)[0]
        self.assertIn('0<=age<120', gate)
        self.assertIn('x.get("entry_enabled") is False', gate)
        self.assertIn('x.get("broker_positions_total")==0', gate)
        self.assertIn('x.get("broker_orders_total")==0', gate)
        self.assertIn('x.get("feed_http") in (200,404)', gate)
        self.assertNotIn("or True", gate)
        self.assertNotIn("DEMO_EA_ABSENT_CONFIRMED", gate)
        self.assertIn('flat_disarmed', s.split(" deploy-backend)",1)[1])
        self.assertIn('result.status!=200', s)

    def test_compiled_artifact_hash_and_human_check_required(self):
        s = self.text
        for must in (
            'cmp -s "$SRC" "$MQ5"',
            'cmp -s "$EX5" "$ARTIFACT"',
            'cmp -s "$ARTIFACT" "$TARGET"',
            "METAEDITOR_ERRORS", "METAEDITOR_WARNINGS",
            '"$EX5" -nt "$MQ5"',
            '[[ -d "$DEMO" && ! -e "$TARGET" ]]',
            'install -o dkmt5 -g dkmt5 -m 0644 "$ARTIFACT" "$TARGET"',
        ):
            self.assertIn(must, s)

    def test_no_broker_trade_api_or_automatic_attachment(self):
        s = self.text
        self.assertNotIn("trade.Buy", s)
        self.assertNotIn("trade.Sell", s)
        self.assertNotIn("OrderSend", s)
        self.assertNotIn("pkill", s)
        self.assertNotIn("killall", s)
        self.assertNotIn("systemctl enable", s)


if __name__ == "__main__":
    unittest.main()
