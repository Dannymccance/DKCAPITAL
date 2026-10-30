import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dkcapital.gws_discord_routing import (
    PROVIDER_CHANNEL_NAMES, destination_for_signal, load_routes,
    provider_for_signal, purge_channel_entries, setup_channels,
)
from dkcapital.mt5_tp1_policy import CAPITAL_CHAT_ID, STORM_CHAT_ID

LEGACY = 1554231371013169235
ROUTES = {
    "STORM": 1566000000000000001,
    "CAPITAL": 1566000000000000002,
    "MONEY HOUSE": 1566000000000000003,
    "VIP": 1566000000000000004,
}


class SourceRoutingTests(unittest.TestCase):
    def test_pinned_storm_and_capital_ids_win(self):
        self.assertEqual(provider_for_signal({
            "source_style": "gws", "origin_chat_id": STORM_CHAT_ID,
            "source_chat_title": "GWS - CAPITAL",
        }), "STORM")
        self.assertEqual(provider_for_signal({
            "source_style": "gws", "origin_chat_id": CAPITAL_CHAT_ID,
        }), "CAPITAL")

    def test_money_house_and_vip_labels_with_unicode(self):
        for title, expected in (
            ("GWS - MONEY HOUSE", "MONEY HOUSE"),
            ("GWS (VIP)", "VIP"),
            ("GWS - VIP", "VIP"),
            ("𝐆𝐖𝐒 (𝐕𝐈𝐏)", "VIP"),
        ):
            with self.subTest(title=title):
                self.assertEqual(provider_for_signal({
                    "source_style": "gws", "chat_id": -100123,
                    "origin_chat_id": -100123,
                    "source_chat_title": title,
                }), expected)

    def test_forwarded_and_unknown_sources_fail_closed(self):
        for signal in [
            {"source_style": "gws", "chat_id": -100111,
             "origin_chat_id": 0, "source_chat_title": "GWS - MONEY HOUSE"},
            {"source_style": "gws", "chat_id": -100111,
             "origin_chat_id": -100222, "source_chat_title": "GWS - STORM"},
            {"source_style": "gws", "chat_id": -100111,
             "origin_chat_id": -100111, "source_chat_title": "Unknown Signals"},
            {"source_style": "gws", "chat_id": -100111},
        ]:
            self.assertIsNone(provider_for_signal(signal))
            self.assertIsNone(destination_for_signal(signal, ROUTES, legacy_channel_id=LEGACY))

    def test_legacy_and_other_providers_stay_on_original(self):
        signal = {"source_style": "gws", "origin_chat_id": STORM_CHAT_ID}
        self.assertEqual(destination_for_signal(signal, None, legacy_channel_id=LEGACY), LEGACY)
        self.assertEqual(destination_for_signal(signal, ROUTES, legacy_channel_id=LEGACY), ROUTES["STORM"])
        elite = {"source_style": "elite", "source_chat_title": "Elite Portfolios"}
        self.assertEqual(destination_for_signal(elite, ROUTES, legacy_channel_id=LEGACY), LEGACY)

    def test_route_file_is_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "channels.json"
            self.assertIsNone(load_routes(path, legacy_channel_id=LEGACY))
            path.write_text(json.dumps({k: str(v) for k, v in ROUTES.items()}))
            self.assertEqual(load_routes(path, legacy_channel_id=LEGACY), ROUTES)
            broken = dict(ROUTES, VIP=ROUTES["STORM"])
            path.write_text(json.dumps({k: str(v) for k, v in broken.items()}))
            with self.assertRaisesRegex(ValueError, "unique"):
                load_routes(path, legacy_channel_id=LEGACY)
            broken = dict(ROUTES, VIP=LEGACY)
            path.write_text(json.dumps({k: str(v) for k, v in broken.items()}))
            with self.assertRaisesRegex(ValueError, "legacy"):
                load_routes(path, legacy_channel_id=LEGACY)

    def test_purge_only_affected_channel(self):
        registry = {
            "messages": {
                "storm": {"message_id": 111, "channel_id": ROUTES["STORM"]},
                "capital": {"message_id": 222, "channel_id": ROUTES["CAPITAL"]},
                "legacy": {"message_id": 333},
            },
        }
        self.assertTrue(purge_channel_entries(
            registry, channel_id=ROUTES["STORM"],
            legacy_channel_id=LEGACY, timestamp="2026-10-30T00:00:00Z",
        ))
        self.assertNotIn("storm", registry["messages"])
        self.assertIn("capital", registry["messages"])
        self.assertIn("legacy", registry["messages"])
        self.assertIn("storm", registry["suppressed"])

    def test_setup_reuses_existing_and_creates_missing_without_alerts(self):
        requests = []
        guild = "1600000000000000000"
        category = "1600000000000000001"
        existing = [
            {"name": "gws-storm-signals", "id": str(ROUTES["STORM"]),
             "parent_id": category, "type": 0},
        ]
        ids = iter([str(ROUTES["CAPITAL"]), str(ROUTES["MONEY HOUSE"]), str(ROUTES["VIP"])])

        def request(_token, method, route, payload=None):
            requests.append((method, route))
            if route == f"/channels/{LEGACY}":
                return {"name": "gws-signals", "type": 0, "guild_id": guild, "parent_id": category}
            if method == "GET":
                return existing
            return {"id": next(ids)}

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gws-channels.json"
            with patch("dkcapital.gws_discord_routing._discord_request", side_effect=request):
                result = setup_channels("fake", legacy_channel_id=LEGACY, path=path)
            self.assertEqual(result, ROUTES)
            self.assertEqual(load_routes(path, legacy_channel_id=LEGACY), ROUTES)
            self.assertEqual(sum(method == "POST" for method, _ in requests), 3)
            self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_setup_refuses_wrong_anchor_before_mutations(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("dkcapital.gws_discord_routing._discord_request", return_value={
                "name": "other", "type": 0, "guild_id": "1600000000000000000",
            }) as request:
                with self.assertRaisesRegex(ValueError, "expected"):
                    setup_channels("fake", legacy_channel_id=LEGACY,
                                   path=Path(folder) / "channels.json")
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
