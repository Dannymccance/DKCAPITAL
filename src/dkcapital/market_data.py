from __future__ import annotations

import asyncio
import json
import logging
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger("dkcapital.market_data")

STANDARD_BULLION_URL = "https://standardbullion.com/spot-prices.json"
STANDARD_BULLION_ATTRIBUTION = "Data by Standard Bullion"
STANDARD_BULLION_SOURCE_URL = "https://standardbullion.com"


class GoldSpotClient:
    """Live XAU/USD spot client backed by Standard Bullion.

    The public Standard Bullion feed returns wholesale gold bid/ask in USD per
    troy ounce. Paper BUY fills use ask, SELL fills use bid, and open positions
    are marked on the executable side by the strategy/accounting layer.
    """

    def __init__(
        self,
        url: str = STANDARD_BULLION_URL,
        refresh_seconds: int = 15,
        *,
        base_backoff_seconds: int = 30,
        max_backoff_seconds: int = 900,
    ) -> None:
        self.url = url or STANDARD_BULLION_URL
        self.refresh_seconds = max(15, int(refresh_seconds))
        self.base_backoff_seconds = max(5, int(base_backoff_seconds))
        self.max_backoff_seconds = max(
            self.base_backoff_seconds,
            int(max_backoff_seconds),
        )
        self._cached: dict[str, Any] | None = None
        self._cached_at_monotonic = 0.0
        self._retry_not_before_monotonic = 0.0
        self._failure_count = 0
        self._last_error: str | None = None
        self._lock = asyncio.Lock()

    def _stale_or_none(self) -> dict[str, Any] | None:
        if self._cached is None:
            return None
        quote = dict(self._cached)
        quote["is_stale"] = True
        if self._last_error:
            quote["quote_error"] = self._last_error
        return quote

    def _retry_after_seconds(self, error: HTTPError) -> int | None:
        raw = error.headers.get("Retry-After") if error.headers else None
        if not raw:
            return None
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(raw)
                return max(0, int(retry_at.timestamp() - time.time()))
            except (TypeError, ValueError, OverflowError):
                return None

    def _register_failure(
        self,
        error_name: str,
        *,
        retry_after_seconds: int | None = None,
    ) -> int:
        self._failure_count += 1
        exponential = self.base_backoff_seconds * (2 ** (self._failure_count - 1))
        cooldown = min(self.max_backoff_seconds, exponential)
        if retry_after_seconds is not None:
            cooldown = min(
                self.max_backoff_seconds,
                max(cooldown, retry_after_seconds),
            )
        self._retry_not_before_monotonic = time.monotonic() + cooldown
        self._last_error = error_name
        return cooldown

    async def quote(self, *, force: bool = False) -> dict[str, Any] | None:
        now = time.monotonic()

        if now < self._retry_not_before_monotonic:
            return self._stale_or_none()

        if (
            not force
            and self._cached is not None
            and now - self._cached_at_monotonic < self.refresh_seconds
        ):
            return dict(self._cached)

        async with self._lock:
            now = time.monotonic()
            if now < self._retry_not_before_monotonic:
                return self._stale_or_none()
            if (
                not force
                and self._cached is not None
                and now - self._cached_at_monotonic < self.refresh_seconds
            ):
                return dict(self._cached)

            try:
                quote = await asyncio.to_thread(self._fetch)
            except HTTPError as exc:
                if exc.code == 429:
                    retry_after = self._retry_after_seconds(exc)
                    cooldown = self._register_failure(
                        "http_429",
                        retry_after_seconds=retry_after,
                    )
                    logger.warning(
                        "Standard Bullion XAU/USD feed rate limited us; "
                        "backing off for %ss (failure=%s)",
                        cooldown,
                        self._failure_count,
                    )
                    return self._stale_or_none()

                cooldown = self._register_failure(f"http_{exc.code}")
                logger.warning(
                    "Standard Bullion XAU/USD HTTP error %s; backing off for %ss",
                    exc.code,
                    cooldown,
                )
                return self._stale_or_none()
            except Exception as exc:
                cooldown = self._register_failure(type(exc).__name__)
                logger.warning(
                    "Unable to fetch Standard Bullion XAU/USD quote (%s); "
                    "backing off for %ss",
                    type(exc).__name__,
                    cooldown,
                )
                return self._stale_or_none()

            self._cached = quote
            self._cached_at_monotonic = time.monotonic()
            self._retry_not_before_monotonic = 0.0
            self._failure_count = 0
            self._last_error = None
            return dict(quote)

    @staticmethod
    def _parse_standard_bullion(payload: dict[str, Any]) -> dict[str, Any]:
        metals = payload.get("metals")
        if not isinstance(metals, list):
            raise ValueError("Standard Bullion response contained no metals list")

        gold: dict[str, Any] | None = None
        for raw in metals:
            if isinstance(raw, dict) and str(raw.get("symbol") or "").upper() == "XAU":
                gold = raw
                break
        if gold is None:
            raise ValueError("Standard Bullion response contained no XAU row")

        bid_raw = gold.get("bid")
        ask_raw = gold.get("ask")
        if bid_raw is None or ask_raw is None:
            raise ValueError("Standard Bullion XAU row is missing bid or ask")

        bid = float(bid_raw)
        ask = float(ask_raw)
        if bid <= 0 or ask <= 0:
            raise ValueError("Standard Bullion XAU bid/ask must be positive")
        if ask < bid:
            raise ValueError("Standard Bullion XAU ask is below bid")

        price = (bid + ask) / 2.0
        attribution = str(
            payload.get("attribution")
            or STANDARD_BULLION_ATTRIBUTION
        )

        return {
            "symbol": "XAUUSD",
            "price": price,
            "bid": bid,
            "ask": ask,
            "computed_at": payload.get("updated"),
            "is_stale": False,
            "source": "standard-bullion:XAUUSD",
            "source_url": STANDARD_BULLION_SOURCE_URL,
            "attribution": attribution,
        }

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

        if not isinstance(payload, dict):
            raise ValueError("Standard Bullion returned a non-object JSON payload")
        return self._parse_standard_bullion(payload)
