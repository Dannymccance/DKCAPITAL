import unittest
from datetime import UTC, datetime, timedelta

from dkcapital.mt5_broker_events import discover_events
from dkcapital.mt5_signal_cards import mt5_group

NOW = datetime(2026, 10, 17, 19, 0, tzinfo=UTC)
SID = "-1003765237177:7081"
BASE = mt5_group(SID) * 10
SIGNALS = [{
    "signal_id": SID, "group_id": SID, "symbol": "XAUUSD",
    "source_style": "gws", "direction": "BUY",
    "sl_mode": "PRICE", "current_sl": 4130.0,
    "last_update_at": (NOW-timedelta(minutes=10)).isoformat(),
}]


def snap(deals=None, positions=None, **more):
    return {
        "version": 2, "mode": "DEMO", "currency": "GBP",
        "login": 5057220041, "received_at": NOW.isoformat(),
        "generated_at": NOW.isoformat(), "positions": positions or [],
        "deals": deals or [], **more,
    }


def deal(ticket, entry_type="OUT", reason="TP", leg=1):
    return {
        "ticket": ticket, "position_id": leg, "magic": BASE + leg,
        "entry_type": entry_type, "reason": reason,
        "lots": .01, "price": 4175.0, "profit": 5.0,
        "commission": 0.0, "swap": 0.0, "time": NOW.isoformat(),
    }


def pos(ticket=100, sl=4130., tp=4221.):
    return {
        "ticket": ticket, "magic": BASE + 1,
        "side": "BUY", "sl": sl, "tp": tp,
        "entry": 4175.0, "lots": .01,
    }


class BrokerEventsTests(unittest.TestCase):
    def test_initial_run_suppresses_history(self):
        state, found = discover_events({}, snap([deal(1)]), SIGNALS, now=NOW)
        self.assertEqual(found, [])
        self.assertEqual(state["seen_deals"], [1])

    def test_tp_and_sl_require_mt5_reason(self):
        state, _ = discover_events({}, snap(), SIGNALS, now=NOW)
        state, new = discover_events(state, snap([deal(2), deal(3, reason="SL"),
                                                 deal(4, reason="CLIENT")]), SIGNALS, now=NOW)
        self.assertEqual([e["channel"] for e in new],
                         ["tp_sl", "tp_sl", "changes"])
        self.assertIn("£+5.00", new[0]["body"])
        self.assertIn("STOP LOSS", new[1]["title"])
        self.assertIn("not claimed as TP/SL", new[2]["body"])

    def test_same_broker_deal_does_not_duplicate(self):
        state, _ = discover_events({}, snap(), SIGNALS, now=NOW)
        state, events = discover_events(state, snap([deal(2)]), SIGNALS, now=NOW)
        self.assertEqual(len(events), 1)
        _, repeat = discover_events(state, snap([deal(2)]), SIGNALS, now=NOW)
        self.assertEqual(repeat, [])

    def test_broker_native_stop_change(self):
        state, _ = discover_events({}, snap(positions=[pos()]), SIGNALS, now=NOW)
        state, changes = discover_events(state, snap(positions=[pos(sl=4175)]), SIGNALS, now=NOW)
        self.assertTrue(any(e["title"] == "MT5 NATIVE SL CHANGED" for e in changes))

    def test_missing_provider_breakeven_change_warns(self):
        signals = [{**SIGNALS[0], "sl_mode": "BREAKEVEN"}]
        state, _ = discover_events({}, snap(positions=[pos()]), signals, now=NOW)
        state, changes = discover_events(state, snap(positions=[pos()]), signals, now=NOW)
        self.assertTrue(any(e["title"].startswith("WARNING: PROVIDER SL") for e in changes))
        state, repeat = discover_events(state, snap(positions=[pos()]), signals, now=NOW)
        self.assertFalse(any(e["title"].startswith("WARNING: PROVIDER SL") for e in repeat))

    def test_daily_equity_warning_appears_only_on_transition(self):
        state, _ = discover_events({}, snap(daily_loss_warning=False), SIGNALS, now=NOW)
        state, first = discover_events(state, snap(daily_loss_warning=True, daily_loss_day=20261017),
                                       SIGNALS, now=NOW)
        self.assertEqual(len(first), 1)
        self.assertIn("WARNING", first[0]["title"])
        _, repeat = discover_events(state, snap(daily_loss_warning=True), SIGNALS, now=NOW)
        self.assertEqual(repeat, [])

    def test_late_or_wrong_account_data_emits_nothing(self):
        state, _ = discover_events({}, snap(), SIGNALS, now=NOW)
        wrong = snap([deal(2)])
        wrong["login"] = 111
        self.assertEqual(discover_events(state, wrong, SIGNALS, now=NOW)[1], [])
