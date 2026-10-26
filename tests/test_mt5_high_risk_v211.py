"""High-risk broker feed and target allocation regressions."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dkcapital.signal_parser import parse_actions
from dkcapital.signal_state import SignalState
from dkcapital.mt5_gateway import Mt5GatewayHandler


MESSAGE = ("GWS - MONEY HOUSE\nXAUUSD 4015-4009 BUY\n"
           "TP1 4017.50\nTP2 4020\nTP3 4022\nTP4 4025\n"
           "TP5 4030\nSL 4005")


def event(mid: int, body: str) -> dict:
    return {
        "event_type": "new_message",
        "chat_id": 123,
        "message_id": mid,
        "date": "2026-10-26T08:45:00+00:00",
        "text": body,
        "reply_to_message_id": None,
    }


class HighRiskSignalTests(unittest.TestCase):
    def test_explicit_labels_and_negative_mentions(self):
        for label in ("HIGH RISK", "HIGH-RISK TRADE", "Risk: High",
                      "RISK LEVEL: HIGH", "THIS IS A HIGH RISK TRADE"):
            with self.subTest(label=label):
                parsed = parse_actions(MESSAGE + "\n" + label)[0]
                self.assertTrue(parsed.high_risk)
                self.assertEqual(len(parsed.tps), 5)
        for label in ("NORMAL RISK", "LOW RISK", "NOT HIGH RISK",
                      "NO HIGH RISK", "THIS IS NOT A HIGH RISK TRADE"):
            with self.subTest(label=label):
                self.assertFalse(parse_actions(MESSAGE + "\n" + label)[0].high_risk)

    def test_state_preserves_flag_and_all_targets_after_rebuild(self):
        state = SignalState()
        state.ingest_event(event(10, MESSAGE + "\nHIGH RISK"))
        self.assertTrue(state.signals["123:10"].high_risk)
        self.assertEqual(len(state.signals["123:10"].tps), 5)
        state.rebuild()
        row = state.snapshot()["signals"][0]
        self.assertTrue(row["high_risk"])
        self.assertEqual(len(row["tps"]), 5)

    def test_versioned_feed_blocks_legacy_entry_and_preserves_targets(self):
        state = SignalState()
        state.ingest_event(event(10, MESSAGE + "\nHIGH RISK"))
        row = state.snapshot()["signals"][0]
        outputs = []
        handler = Mt5GatewayHandler.__new__(Mt5GatewayHandler)
        handler.server = SimpleNamespace(config=SimpleNamespace(
            demo_feed_enabled=True, signal_state_path=Path("/nonexistent")))
        handler._write_text = lambda status, body: outputs.append(body)
        with patch("dkcapital.mt5_gateway._json", return_value={"signals": [row]}):
            handler._demo_v2_feed(v4=True)
            handler._demo_v2_feed(v5=True)
        old, new = outputs
        self.assertTrue(old.startswith("DKDEMO4\n"))
        self.assertIn("|CANCELLED|BUY|", old)
        self.assertTrue(new.startswith("DKDEMO5\n"))
        fields = next(line for line in new.splitlines()
                      if line.startswith("S|")).split("|")
        self.assertEqual(len(fields), 16)
        self.assertEqual(fields[2], "ACTIVE")
        self.assertEqual(fields[14], "HIGH")
        self.assertEqual(fields[8:14], ["4017.5", "4020.0", "4022.0",
                                       "4025.0", "4030.0", "0.0"])

    def test_ea_risk_applies_to_entry_layer_and_sl_widening(self):
        ea = (Path(__file__).resolve().parents[1] /
              "mt5/DKcapitalV2.mq5").read_text()
        for token in (
            'input double HighRiskSignalPct = 2.0;',
            'SignalRiskLimitGBP(const bool highRisk=false)',
            'double signalLimit=SignalRiskLimitGBP(highRisk);',
            'groupRisk+plannedRisk>SignalRiskLimitGBP(highRisk)',
            'groupRisk+(afterRisk-beforeRisk)>SignalRiskLimitGBP(highRisk)',
            'if(f[14]!="HIGH" && f[14]!="STANDARD") continue;',
            'if(fields==16 && f[0]=="S")',
            'StringFind(lines[0],"DKDEMO5")',
            'for(int i=0;i<initialLegCount;i++)',
            'if(!CloseGroup(id))',
        ):
            with self.subTest(token=token):
                self.assertIn(token, ea)


if __name__ == "__main__":
    unittest.main()
