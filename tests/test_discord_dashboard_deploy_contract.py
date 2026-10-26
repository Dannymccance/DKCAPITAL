"""Safety contract for the isolated Discord dashboard deployment."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy-discord-dashboard.yml"


class DashboardDeployContract(unittest.TestCase):
    def test_deployment_is_manual_and_only_updates_reporting_services(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("environment: mt5-recovery-deployment", content)
        self.assertIn("git merge --ff-only origin/main", content)
        self.assertIn("docker compose --profile discord build signal-processor discord-bot", content)
        self.assertIn("docker compose --profile discord up -d --no-deps signal-processor discord-bot", content)
        self.assertNotIn("up -d --build --no-deps telegram-listener", content)
        self.assertNotIn("systemctl restart dkcapital-mt5", content)
        self.assertIn("from dkcapital.mt5_pip_history import", content)
        self.assertIn("from dkcapital.signal_state import Signal", content)
        self.assertIn("from dkcapital.discord_bot import _new_signal_card_eligible", content)
        for key in (
            "DKCAPITAL_VPS_HOST", "DKCAPITAL_VPS_USER",
            "DKCAPITAL_VPS_SSH_KEY", "DKCAPITAL_VPS_HOST_KEY",
        ):
            self.assertIn("secrets." + key, content)
        self.assertIn("StrictHostKeyChecking=yes", content)


if __name__ == "__main__":
    unittest.main()
