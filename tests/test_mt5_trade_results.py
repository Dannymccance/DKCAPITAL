from datetime import UTC, datetime

from dkcapital.mt5_signal_cards import mt5_group
from dkcapital.mt5_trade_results import completed_results


NOW = datetime(2026, 10, 17, 19, 0, tzinfo=UTC)
SID = "-1003765237177:7081"


def snapshot(deals, positions=None):
    return {
        "version": 2, "mode": "DEMO", "currency": "GBP",
        "login": 5057220041, "received_at": NOW.isoformat(),
        "balance": 1136.59, "positions": positions or [], "deals": deals,
        "broker_orders_total": 0,
    }


def deal(ticket, leg, kind, lots, profit=0, commission=0, reason=None):
    return {
        "ticket": ticket, "position_id": leg, "magic": mt5_group(SID) * 10 + leg,
        "entry_type": kind, "side": "SELL" if kind == "IN" else "BUY",
        "lots": lots, "price": 4160.0, "profit": profit,
        "commission": commission, "swap": 0.0,
        "time": "2026-10-17T18:00:00+00:00", "reason": reason,
    }


SIGNALS = [{
    "signal_id": SID, "group_id": SID, "source_style": "gws",
    "direction": "SELL", "symbol": "XAUUSD", "opened_at": "2026-10-17T17:30:00+00:00",
}]


def test_final_result_counts_each_leg_and_commissions():
    deals = [
        deal(11, 1, "IN", .01, commission=-.20),
        deal(12, 1, "OUT", .01, profit=5.20, reason="TP"),
        deal(21, 2, "IN", .01),
        deal(22, 2, "OUT", .01, profit=2.00, reason="CLIENT"),
        deal(31, 3, "IN", .01),
        deal(32, 3, "OUT", .01, profit=-.30, reason="CLIENT"),
    ]
    results = completed_results(snapshot(deals), SIGNALS, now=NOW)
    assert len(results) == 1
    result = results[SID]
    assert result["pnl"] == 6.70
    assert result["legs"] == [
        {"label": "TP1 leg", "pnl": 5.0},
        {"label": "TP2 leg", "pnl": 2.0},
        {"label": "TP3 leg", "pnl": -.3},
    ]
    assert result["exit_type"] == "MIXED BROKER EXITS"


def test_no_result_until_every_leg_fully_closed():
    deals = [
        deal(11, 1, "IN", .01), deal(12, 1, "OUT", .01, profit=5),
        deal(21, 2, "IN", .01),
    ]
    assert completed_results(snapshot(deals), SIGNALS, now=NOW) == {}
    deals.append(deal(22, 2, "OUT", .01, profit=-2))
    assert SID in completed_results(snapshot(deals), SIGNALS, now=NOW)


def test_no_result_when_broker_position_still_open():
    deals = [deal(11, 1, "IN", .01), deal(12, 1, "OUT", .01, profit=1)]
    open_position = {"magic": mt5_group(SID) * 10 + 2, "group": mt5_group(SID)}
    assert completed_results(snapshot(deals, [open_position]), SIGNALS, now=NOW) == {}


def test_no_result_on_stale_snapshot():
    snap = snapshot([deal(11, 1, "IN", .01), deal(12, 1, "OUT", .01)])
    snap["received_at"] = "2026-10-16T18:00:00+00:00"
    assert completed_results(snap, SIGNALS, now=NOW) == {}


def test_unverified_reason_never_claims_tp():
    deals = [deal(11, 1, "IN", .01), deal(12, 1, "OUT", .01, profit=1)]
    assert completed_results(snapshot(deals), SIGNALS, now=NOW)[SID]["exit_type"] == (
        "BROKER CLOSED - REASON UNVERIFIED"
    )
