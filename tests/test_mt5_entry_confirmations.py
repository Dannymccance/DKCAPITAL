"""Broker-only three-account entry status: no Telegram or paper fill claims."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dkcapital.mt5_entry_confirmations import (
    IDENTITIES, account_status, build_confirmation, entry_evidence, signal_is_new,
)
from dkcapital.mt5_signal_cards import mt5_group


NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)
SID = "-1004334637102:8742"


def signal(*, when=None):
    when = when or NOW - timedelta(seconds=240)
    return {
        "signal_id": SID,
        "symbol": "XAUUSD",
        "source_style": "gws",
        "direction": "SELL",
        "entry_low": 4000.0,
        "entry_high": 4004.0,
        "opened_at": when.isoformat(),
        "publish_ready_at": when.isoformat(),
    }


def snapshot(account, *, with_deal=False, with_position=False, fresh=True):
    ident = dict(IDENTITIES[account])
    data = {
        "version": 2, **ident,
        "received_at": (NOW if fresh else NOW - timedelta(minutes=5)).isoformat(),
        "feed_http": 200,
        "broker_positions_total": int(with_position),
        "broker_orders_total": 0,
        "positions": [],
        "deals": [],
    }
    if with_deal:
        data["deals"].append({
            "magic": mt5_group(SID) * 10 + 1,
            "entry_type": "IN", "ticket": 876501 + account,
            "lots": 0.02, "price": 4002.5, "time": NOW.isoformat(),
        })
    if with_position:
        data["positions"].append({
            "magic": mt5_group(SID) * 10 + 2,
            "ticket": 456201 + account, "lots": 0.03, "entry": 4002.6,
        })
    return data


class ThreeAccountEntryTests(unittest.TestCase):
    def test_two_live_filled_demo_not_filled_independent_evidence(self):
        snapshots = {
            1: snapshot(1), 2: snapshot(2, with_deal=True),
            3: snapshot(3, with_position=True),
        }
        content, proofs = build_confirmation(signal(), snapshots, {}, NOW)
        self.assertIn("Signal reference:", content)
        self.assertIn(SID, content)
        self.assertIn("**Kyle:** ✅ Entered", content)
        self.assertIn("**Dan:** ✅ Entered", content)
        self.assertIn("**Demo:** ❌ No broker entry confirmed", content)
        self.assertEqual(proofs["2"]["proof"], "MT5 DEAL_ENTRY_IN")
        self.assertEqual(proofs["3"]["proof"], "MT5 OPEN POSITION")
        self.assertNotIn("1", proofs)

    def test_unknown_is_not_mislabelled_as_no_trade(self):
        broken = {1: snapshot(1), 2: snapshot(2, fresh=False), 3: {}}
        msg, _ = build_confirmation(signal(), broken, {}, NOW)
        self.assertIn("**Demo:** ❌ No broker entry confirmed", msg)
        self.assertIn("**Kyle:** ⚠️ Unknown", msg)
        self.assertIn("**Dan:** ⚠️ Unknown", msg)

    def test_never_green_for_telegram_decision_or_wrong_magic(self):
        s = snapshot(2)
        s["decisions"] = [{"signal_id": SID, "code": "ACCEPTED", "stage": "ENTRY"}]
        s["deals"] = [{
            "ticket": 452, "magic": mt5_group("other:999") * 10 + 1,
            "entry_type": "IN", "lots": 0.03,
        }]
        self.assertIsNone(entry_evidence(SID, s))
        status, _ = account_status(signal(), 2, s, None, NOW)
        self.assertIn("❌", status)
        s["deals"][0]["magic"] = mt5_group(SID) * 10 + 1
        s["deals"][0]["entry_type"] = "OUT"
        self.assertIsNone(entry_evidence(SID, s))

    def test_fill_can_be_confirmed_after_wait_and_survives_history_rolloff(self):
        unfilled = snapshot(2)
        status, proof = account_status(signal(when=NOW - timedelta(seconds=20)), 2, unfilled, None, NOW)
        self.assertIn("⏳", status)
        self.assertIsNone(proof)
        s = snapshot(2, with_deal=True)
        status, proof = account_status(signal(), 2, s, None, NOW)
        self.assertIn("✅", status)
        future = snapshot(2, fresh=False)
        persisted, carried = account_status(signal(), 2, future, proof, NOW)
        self.assertIn("✅", persisted)
        self.assertEqual(carried, proof)

    def test_account_mismatch_cannot_confirm_a_fill(self):
        wrong = snapshot(2, with_deal=True)
        for k, v in (
            ("login", 999999),
            ("mode", "DEMO"),
            ("currency", "USD"),
            ("server", "VantageMarkets-Live14"),
            ("received_at", None),
            ("broker_positions_total", None),
        ):
            altered = {**wrong, k: v}
            status, evidence = account_status(signal(), 2, altered, None, NOW)
            self.assertIn("⚠️ Unknown", status, k)
            self.assertIsNone(evidence)

    def test_pending_and_feed_outage_never_report_false_negative(self):
        fresh = snapshot(1)
        a, _ = account_status(signal(when=NOW - timedelta(seconds=60)), 1, fresh, None, NOW)
        self.assertIn("⏳", a)
        fresh["feed_http"] = 503
        b, _ = account_status(signal(), 1, fresh, None, NOW)
        self.assertIn("⚠️", b)

    def test_first_valid_edited_signal_starts_tracking_without_old_replay(self):
        s = signal(when=NOW-timedelta(hours=3))
        boundary = (NOW-timedelta(seconds=300)).isoformat()
        self.assertFalse(signal_is_new(s, boundary))
        s["publish_ready_at"] = (NOW-timedelta(seconds=30)).isoformat()
        self.assertTrue(signal_is_new(s, boundary))
        self.assertFalse(signal_is_new(s, NOW.isoformat()))

    def test_wiring_separate_durable_state_and_channel(self):
        root = Path(__file__).resolve().parents[1]
        code = (root/"src/dkcapital/discord_bot.py").read_text()
        logic = (root/"src/dkcapital/mt5_entry_confirmations.py").read_text()
        self.assertIn("1564370691313377300", code)
        self.assertIn("mt5-entry-confirmation-state.json", code)
        self.assertIn("mt5_entry_confirmation_loop.start()", code)
        self.assertIn("update_mt5_entry_confirmations()", code)
        self.assertIn("account2_snapshot_path", code)
        self.assertIn("account3_snapshot_path", code)
        self.assertIn("mt5_snapshot", code)
        self.assertIn('allowed_mentions=discord.AllowedMentions.none()', code)
        for dangerous in ("OrderSend", "PositionClose", "systemctl", "os.system"):
            self.assertNotIn(dangerous, logic)


if __name__ == "__main__":
    unittest.main()
