"""Cross-room entry gate: three accounts, broker truth and management safety."""
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from dkcapital.mt5_entry_policy import (
    new_entry_block_reason, signal_chat, verified_snapshot,
)
from dkcapital.mt5_gateway import GatewayConfig, Mt5GatewayHandler
from dkcapital.mt5_signal_cards import mt5_group


def signal(chat, mid, side):
    return {
        "signal_id": f"{chat}:{mid}", "chat_id": chat, "symbol": "XAUUSD",
        "status": "ACTIVE", "source_style": "gws",
        "direction": side, "entry_low": 4015.5, "entry_high": 4020.5,
        "current_sl": 4022.5 if side == "SELL" else 4011.5,
        "sl_mode": "PRICE",
        "tps": {"1": 4013, "2": 4012, "3": 4011} if side == "SELL"
               else {"1": 4024, "2": 4025, "3": 4026},
        "opened_at": (datetime.now(UTC)-timedelta(seconds=70)).isoformat(),
        "last_update_at": (datetime.now(UTC)-timedelta(seconds=60)).isoformat(),
        "history": [{"kind":"partial_close", "message_id":987, "partial_percent":25}],
    }


def snapshot(login, side, chat=-100111, *, age=0, mode="DEMO", currency="GBP"):
    return {
        "version":2, "login":login, "mode":mode, "currency":currency,
        "server":"Test Server", "received_at":(datetime.now(UTC)-timedelta(seconds=age)).isoformat(),
        "positions":[{"group":mt5_group(f"{chat}:100"), "side":side, "leg":1}],
    }


class Recorder(Mt5GatewayHandler):
    def __init__(self, config):
        self._config=config
        self.response=""
    @property
    def config(self):
        return self._config
    def _write_text(self, status, response):
        self.response=response


class OpposingProviderGateTests(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.path=Path(self.root.name)
        with patch.dict(os.environ, {
            "MT5_GATEWAY_TOKEN":"a"*32, "MT5_DEMO_FEED_ENABLED":"true",
            "MT5_ACCOUNT1_LOGIN":"11001",
            "MT5_ACCOUNT1_TELEMETRY_PATH":str(self.path/"acct1.json"),
            "SIGNAL_STATE_PATH":str(self.path/"signals.json")
        }, clear=False):
            self.config=GatewayConfig()
        self.config.profiles={
            2:dict(login=22002,mode="REAL",currency="GBP",server="Test Server",
                   telemetry_path=self.path/"acct2.json",token="b"*32),
            3:dict(login=33003,mode="REAL",currency="USD",server="Test Server",
                   telemetry_path=self.path/"acct3.json",token="c"*32),
        }
        self.held=signal(-100111,100,"SELL")
        self.buy=signal(-100222,200,"BUY")
        self.signals=[self.held,self.buy]
        (self.path/"signals.json").write_text(json.dumps({"signals":self.signals}))
        for n in (1,2,3):
            p=self.config.profiles.get(n)
            login=p["login"] if p else self.config.account1_login
            mode=p["mode"] if p else "DEMO"
            currency=p["currency"] if p else "GBP"
            (self.path/f"acct{n}.json").write_text(
                json.dumps(snapshot(login,"SELL",mode=mode,currency=currency))
            )

    def feed(self, account):
        handler=Recorder(self.config)
        handler._demo_v2_feed(v5=True,account_id=account,
                              profile_header=(f"DKACCOUNT{account}V5" if account>1 else None))
        return handler.response.splitlines()

    def test_cross_room_opposite_blocked_in_all_three_accounts(self):
        for n in (1,2,3):
            with self.subTest(account=n):
                lines=self.feed(n)
                self.assertIn(f"S|{self.buy['signal_id']}|CANCELLED|BUY|",lines[2])
                self.assertIn(f"S|{self.held['signal_id']}|ACTIVE|SELL|",lines[1])
                self.assertTrue(any(x.startswith(f"A|{self.buy['signal_id']}|")
                                    for x in lines))

    def test_same_chat_opposite_allowed(self):
        self.buy["chat_id"]=-100111
        (self.path/"signals.json").write_text(json.dumps({"signals":self.signals}))
        for n in (1,2,3):
            self.assertIn(f"S|{self.buy['signal_id']}|ACTIVE|BUY|",self.feed(n)[2])

    def test_same_side_allowed(self):
        self.buy["direction"]="SELL"
        self.buy["current_sl"]=4022.5
        self.buy["tps"]={"1":4013,"2":4012,"3":4011}
        (self.path/"signals.json").write_text(json.dumps({"signals":self.signals}))
        self.assertIn("|ACTIVE|SELL|",self.feed(2)[2])

    def test_closed_existing_trade_allows_opposite(self):
        for n in (1,2,3):
            p=self.path/f"acct{n}.json"
            data=json.loads(p.read_text())
            data["positions"]=[]
            p.write_text(json.dumps(data))
            self.assertIn(f"S|{self.buy['signal_id']}|ACTIVE|BUY|",self.feed(n)[2])

    def test_accounts_are_independent(self):
        p=self.path/"acct2.json"
        data=json.loads(p.read_text())
        data["positions"]=[]
        p.write_text(json.dumps(data))
        self.assertIn(f"S|{self.buy['signal_id']}|ACTIVE|BUY|",self.feed(2)[2])
        self.assertIn(f"S|{self.buy['signal_id']}|CANCELLED|BUY|",self.feed(1)[2])
        self.assertIn(f"S|{self.buy['signal_id']}|CANCELLED|BUY|",self.feed(3)[2])

    def test_stale_telemetry_holds_new_entries_but_not_actions(self):
        p=self.path/"acct3.json"
        data=json.loads(p.read_text())
        data["received_at"]=(datetime.now(UTC)-timedelta(minutes=10)).isoformat()
        p.write_text(json.dumps(data))
        lines=self.feed(3)
        self.assertTrue(any(x.startswith(f"S|{self.held['signal_id']}|CANCELLED|") for x in lines))
        self.assertTrue(any(x.startswith(f"A|{self.held['signal_id']}|") for x in lines))
        self.assertTrue(any(x.startswith(f"A|{self.buy['signal_id']}|") for x in lines))

    def test_account_identity_mismatch_holds_new_entries(self):
        p=self.path/"acct2.json"
        data=json.loads(p.read_text())
        data["login"]=9999
        p.write_text(json.dumps(data))
        self.assertIn(f"S|{self.held['signal_id']}|CANCELLED|SELL|",self.feed(2)[1])

    def test_unknown_opposing_magic_is_conservative(self):
        data=snapshot(11001,"SELL",chat=-100999)
        self.assertEqual(new_entry_block_reason(self.buy,self.signals,data),
                         "OPPOSING_GROUP_POSITION_ACTIVE")

    def test_unmanaged_positions_hold_new_entries(self):
        data=snapshot(11001,"SELL")
        data["unmanaged_positions_count"]=1
        self.assertEqual(new_entry_block_reason(self.held,self.signals,data),
                         "UNMANAGED_BROKER_POSITION")


if __name__=="__main__":
    unittest.main()
