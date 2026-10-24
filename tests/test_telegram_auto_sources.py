"""Source membership regression checks for joined GWS Telegram channels."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dkcapital.config import Settings
from dkcapital.telegram_listener import _discover_auto_gws_chats


def dialog(chat_id: int, title: str, *, channel: bool = True):
    return SimpleNamespace(
        name=title,
        entity=SimpleNamespace(
            peer_id=chat_id, title=title, broadcast=channel, megagroup=False,
        ),
    )


class AutoGwsDiscoveryTests(unittest.TestCase):
    @patch("dkcapital.telegram_listener.utils.get_peer_id",
           side_effect=lambda entity: entity.peer_id)
    def test_requested_channels_are_discovered_only_after_join(self, _get_id):
        joined = [
            dialog(-1001, "GWS - MONEY HOUSE"),
            dialog(-1002, "GWS - CAPITAL"),
            dialog(-1003, "GWS - STORM"),
            dialog(-1004, "GWS (VIP)"),
        ]
        self.assertEqual(_discover_auto_gws_chats(joined), {
            -1001: "GWS - MONEY HOUSE",
            -1002: "GWS - CAPITAL",
            -1003: "GWS - STORM",
            -1004: "GWS (VIP)",
        })
        self.assertNotIn(-1004, _discover_auto_gws_chats(joined[:3]))

    @patch("dkcapital.telegram_listener.utils.get_peer_id",
           side_effect=lambda entity: entity.peer_id)
    def test_whitespace_and_vip_title_alias(self, _get_id):
        self.assertEqual(_discover_auto_gws_chats([
            dialog(-1001, "  GWS   -   CAPITAL "),
            dialog(-1002, "GWS - VIP"),
        ]), {-1001: "  GWS   -   CAPITAL ", -1002: "GWS - VIP"})

    @patch("dkcapital.telegram_listener.utils.get_peer_id",
           side_effect=lambda entity: entity.peer_id)
    def test_actual_stylized_gws_vip_name_is_discovered(self, _get_id):
        found = _discover_auto_gws_chats([
            dialog(-1003765237177, "𝐆𝐖𝐒 (𝐕𝐈𝐏)"),
            dialog(-1004334637102, "GWS - STORM"),
            dialog(-1004406990046, "GWS - CAPITAL"),
            dialog(-1004359492110, "GWS - MONEY HOUSE"),
        ])
        self.assertEqual(found, {
            -1003765237177: "𝐆𝐖𝐒 (𝐕𝐈𝐏)",
            -1004334637102: "GWS - STORM",
            -1004406990046: "GWS - CAPITAL",
            -1004359492110: "GWS - MONEY HOUSE",
        })

    @patch("dkcapital.telegram_listener.utils.get_peer_id",
           side_effect=lambda entity: entity.peer_id)
    def test_stylized_and_plain_duplicate_titles_are_rejected(self, _get_id):
        self.assertEqual(_discover_auto_gws_chats([
            dialog(-1001, "𝐆𝐖𝐒 (𝐕𝐈𝐏)"),
            dialog(-1002, "GWS (VIP)"),
        ]), {})

    @patch("dkcapital.telegram_listener.utils.get_peer_id",
           side_effect=lambda entity: entity.peer_id)
    def test_unrelated_channels_and_private_chats_are_ignored(self, _get_id):
        self.assertEqual(_discover_auto_gws_chats([
            dialog(-1001, "GWS - STORM RESULTS"),
            dialog(-1002, "Elite Portfolios"),
            dialog(123, "GWS - CAPITAL", channel=False),
        ]), {})

    @patch("dkcapital.telegram_listener.utils.get_peer_id",
           side_effect=lambda entity: entity.peer_id)
    def test_duplicate_title_fails_closed(self, _get_id):
        self.assertEqual(_discover_auto_gws_chats([
            dialog(-1001, "GWS - STORM"),
            dialog(-1002, "GWS - STORM"),
            dialog(-1003, "GWS - CAPITAL"),
        ]), {-1003: "GWS - CAPITAL"})

    def test_auto_discovery_default_and_disable_switch(self):
        with patch.dict(os.environ, {"TELEGRAM_AUTO_GWS_SOURCES": "true"}):
            self.assertTrue(Settings.from_env().telegram_auto_gws_sources)
        with patch.dict(os.environ, {"TELEGRAM_AUTO_GWS_SOURCES": "false"}):
            self.assertFalse(Settings.from_env().telegram_auto_gws_sources)


if __name__ == "__main__":
    unittest.main()
