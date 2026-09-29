from __future__ import annotations

import time
import unittest
from urllib.error import HTTPError

from dkcapital.market_data import GoldSpotClient


class GoldSpotClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_429_enters_backoff_and_force_does_not_bypass_it(self) -> None:
        client = GoldSpotClient(
            "https://example.test/xau",
            refresh_seconds=15,
            base_backoff_seconds=30,
            max_backoff_seconds=300,
        )
        calls = 0

        def fail() -> dict:
            nonlocal calls
            calls += 1
            raise HTTPError(
                client.url,
                429,
                "Too Many Requests",
                {"Retry-After": "120"},
                None,
            )

        client._fetch = fail  # type: ignore[method-assign]

        first = await client.quote(force=True)
        second = await client.quote(force=True)

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(calls, 1)
        self.assertEqual(client._failure_count, 1)
        self.assertGreater(client._retry_not_before_monotonic, time.monotonic())

    async def test_failed_refresh_returns_last_quote_as_stale(self) -> None:
        client = GoldSpotClient(
            "https://example.test/xau",
            refresh_seconds=15,
            base_backoff_seconds=30,
            max_backoff_seconds=300,
        )
        client._cached = {
            "symbol": "XAUUSD",
            "price": 4160.0,
            "bid": 4159.9,
            "ask": 4160.1,
            "computed_at": "2026-09-29T14:00:00+00:00",
            "is_stale": False,
            "source": "test",
        }
        client._cached_at_monotonic = time.monotonic() - 60

        def fail() -> dict:
            raise HTTPError(
                client.url,
                429,
                "Too Many Requests",
                {},
                None,
            )

        client._fetch = fail  # type: ignore[method-assign]
        quote = await client.quote(force=True)

        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote["price"], 4160.0)
        self.assertTrue(quote["is_stale"])
        self.assertEqual(quote["quote_error"], "http_429")


if __name__ == "__main__":
    unittest.main()
