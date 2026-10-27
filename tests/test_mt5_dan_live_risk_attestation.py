"""Only the new EA's fresh UK_V2 broker risk telemetry permits live entries."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dkcapital.mt5_entry_policy import verified_uk_session_risk
from dkcapital.mt5_gateway import GatewayConfig, Mt5GatewayHandler
from scripts.generate_mt5_v211_profile import generate


class Recorder(Mt5GatewayHandler):
    def __init__(self, cfg):
        self._cfg = cfg
        self.response = ""
    @property
    def config(self):
        return self._cfg
    def _write_text(self, status, body):
        self.response = body


class AttestedLiveRiskTests(unittest.TestCase):
    def test_utc_and_bst_session_boundaries(self):
        cases = [
            ("2026-10-27T00:59:59+00:00", 4),
            ("2026-10-27T01:00:00+00:00", 10),
            ("2026-10-27T08:59:59+00:00", 10),
            ("2026-10-27T09:00:00+00:00", 4),
            ("2026-07-01T00:00:00+00:00", 10),
            ("2026-07-01T07:59:59+00:00", 10),
            ("2026-07-01T08:00:00+00:00", 4),
        ]
        for iso, pct in cases:
            at = datetime.fromisoformat(iso)
            snap = {
                "risk_schedule": "UK_V2", "mode": "REAL", "currency": "USD",
                "balance": 2000, "equity": 2400, "credit": 500,
                "signal_risk_cap_pct": pct,
                "portfolio_risk_cap_account_currency": 1900 * pct / 100,
            }
            with self.subTest(at=iso):
                self.assertTrue(verified_uk_session_risk(snap, now=at))
                self.assertFalse(verified_uk_session_risk({**snap, "signal_risk_cap_pct": 0}, now=at))
                self.assertFalse(verified_uk_session_risk({**snap, "risk_schedule": "UK_V1"}, now=at))
                self.assertFalse(verified_uk_session_risk({
                    **snap, "portfolio_risk_cap_account_currency": pct * 20,
                }, now=at))

    def test_live_account3_feed_holds_old_ea_but_preserves_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now = datetime.now(UTC)
            hour = now.astimezone(ZoneInfo("Europe/Isle_of_Man")).hour
            pct = 10.0 if 1 <= hour < 9 else 4.0
            env = {
                "MT5_DAN_ONLY": "true",
                "MT5_GATEWAY_TOKEN": "g"*32,
                "MT5_ACCOUNT3_LOGIN": "710003",
                "MT5_ACCOUNT3_MODE": "REAL",
                "MT5_ACCOUNT3_CURRENCY": "USD",
                "MT5_ACCOUNT3_SERVER": "Vantage-Live",
                "MT5_ACCOUNT3_TOKEN": "c"*32,
                "SIGNAL_STATE_PATH": str(root / "signals.json"),
            }
            signal_id="-1001:123"
            signal = {
                "signal_id":signal_id, "status":"ACTIVE", "symbol":"XAUUSD",
                "direction":"BUY", "entry_low":4000, "entry_high":4001,
                "current_sl":3993, "sl_mode":"PRICE",
                "tps":{"1":4004,"2":4005,"3":4006},
                "opened_at":now.isoformat(),
                "source_style":"elite",
                "history":[{"kind":"close", "message_id":124,"partial_percent":0}],
            }
            (root / "signals.json").write_text(json.dumps({"signals":[signal]}))
            snapshot = {
                "version":2, "mode":"REAL", "currency":"USD", "server":"Vantage-Live",
                "login":710003, "received_at":now.isoformat(), "positions":[],
                "balance":2000, "equity":2500, "credit":500,
                "risk_schedule":"UK_V2", "signal_risk_cap_pct":pct,
                "portfolio_risk_cap_account_currency":2000*pct/100,
            }
            (root / "dan.json").write_text(json.dumps(snapshot))
            with patch.dict(os.environ, env):
                cfg = GatewayConfig()
                cfg.validate()
                cfg.profiles[3]["telemetry_path"] = root / "dan.json"
                def feed():
                    h=Recorder(cfg)
                    h._demo_v2_feed(v5=True,account_id=3,profile_header="DKACCOUNT3V5")
                    return h.response
                ok=feed()
                self.assertIn(f"S|{signal_id}|ACTIVE|BUY|", ok)
                self.assertIn(f"A|{signal_id}|124|CLOSE|", ok)

                for mutation in (
                    {"risk_schedule": None}, {"signal_risk_cap_pct":0},
                    {"portfolio_risk_cap_account_currency": 900},
                    {"received_at": (now-timedelta(minutes=10)).isoformat()},
                ):
                    with self.subTest(mutation=mutation):
                        current={**snapshot, **mutation}
                        (root / "dan.json").write_text(json.dumps(current))
                        blocked=feed()
                        self.assertIn(f"S|{signal_id}|CANCELLED|BUY|", blocked)
                        self.assertIn(f"A|{signal_id}|124|CLOSE|", blocked)

    def test_compiled_dan_candidate_telemeters_the_uk_v2_flag(self):
        text = generate(3, 710003, "REAL", "USD", "Vantage-Live", 130)
        self.assertIn('risk_schedule', text)
        self.assertIn('UK_V2', text)
        self.assertIn('N(SessionRiskPct(),3)', text)
        self.assertIn('PortfolioRiskLimitAccountCurrency()', text)
        self.assertIn('AccountInfoDouble(ACCOUNT_CREDIT)', text)


if __name__ == "__main__":
    unittest.main()
