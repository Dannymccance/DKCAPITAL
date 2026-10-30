import csv
import json
import tempfile
import unittest
from pathlib import Path

from dkcapital.export_signals import export


class ExportSignalsTests(unittest.TestCase):
    def test_exports_raw_events_edits_and_parsed_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            events = folder / "events.jsonl"
            state = folder / "state.json"
            original = {"event_type": "new_message", "chat_id": 1, "message_id": 10,
                        "text": "<script>alert(1)</script>"}
            amended = {"event_type": "message_edited", "chat_id": 1, "message_id": 10,
                       "text": "XAUUSD BUY NOW"}
            events.write_text("\n".join(json.dumps(x) for x in [original, amended]) + "\n")
            state.write_text(json.dumps({"signals": [{"signal_id": "test-1", "symbol": "XAUUSD",
                                                       "direction": "BUY", "history": [amended]}]}))
            output = folder / "exports"
            paths = export(events, state, output)
            self.assertEqual(len(paths), 4)
            with (output / "gws-telegram-events.csv").open(encoding="utf-8-sig", newline="") as fd:
                rows = list(csv.DictReader(fd))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["event_type"], "message_edited")
            self.assertIn("&lt;script&gt;", (output / "gws-telegram-events.html").read_text())
            self.assertNotIn("<script>", (output / "gws-telegram-events.html").read_text())
            self.assertIn("test-1", (output / "gws-parsed-signals.html").read_text())

    def test_missing_event_log_fails_instead_of_silently_empty_export(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            state = folder / "state.json"
            state.write_text('{"signals":[]}')
            with self.assertRaises(FileNotFoundError):
                export(folder / "missing.jsonl", state, folder / "out")


if __name__ == "__main__":
    unittest.main()
