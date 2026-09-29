from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from urllib.request import Request, urlopen

logger = logging.getLogger("dkcapital.market_data")


class GoldSpotClient:
    def __init__(self, url: str, refresh_seconds: int = 60) -> None:
        self.url = url
        self.refresh_seconds = max(15, int(refresh_seconds))
        self._cached: dict[str, Any] | None = None
        self._cached_at_monotonic = 0.0
        self._lock = asyncio.Lock()

    async def quote(self, *, force: bool = False) -> dict[str, Any] | None:
        now = time.monotonic()
        if (
            not force
            and self._cached is not None
            and now - self._cached_at_monotonic < self.refresh_seconds
        ):
            return dict(self._cached)

        async with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._cached is not None
                and now - self._cached_at_monotonic < self.refresh_seconds
            ):
                return dict(self._cached)

            try:
                quote = await asyncio.to_thread(self._fetch)
            except Exception:
                logger.exception("Unable to fetch XAU/USD spot quote")
                return dict(self._cached) if self._cached is not None else None

            self._cached = quote
            self._cached_at_monotonic = time.monotonic()
            return dict(quote)

    def _fetch(self) -> dict[str, Any]:
        request = Request(
            self.url,
            headers={
                "Accept": "application/json",
                "User-Agent": "DKCapital/1.0",
            },
        )
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))

        rows = payload.get("symbols") or []
        if not rows:
            raise ValueError("Gold spot response contained no symbols")

        row = rows[0]
        price = float(row["price"])
        return {
            "symbol": "XAUUSD",
            "price": price,
            "bid": float(row["bid"]) if row.get("bid") is not None else None,
            "ask": float(row["ask"]) if row.get("ask") is not None else None,
            "computed_at": row.get("computed_at"),
            "is_stale": bool(row.get("is_stale", False)),
            "source": "goldprice.dev",
        }
