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

BYBIT_XAU_TICKER_URL = (
    "https://api.bybit.com/v5/market/tickers?category=linear&symbol=XAUUSDT"
)


class GoldSpotClient:
    def __init__(
        self,
        url: str,
        refresh_seconds: int = 60,
        *,
        base_backoff_seconds: int = 30,
        max_backoff_seconds: int = 900,
        fallback_url: str = BYBIT_XAU_TICKER_URL,
    ) -> None:
        self.url = url
        self.fallback_url = fallback_url
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
                        "XAU quote sources rate limited us; backing off for %ss "
                        "(failure=%s)",
                        cooldown,
                        self._failure_count,
                    )
                    return self._stale_or_none()

                cooldown = self._register_failure(f"http_{exc.code}")
                logger.warning(
                    "XAU quote HTTP error %s; backing off for %ss",
                    exc.code,
                    cooldown,
                )
                return self._stale_or_none()
            except Exception as exc:
                cooldown = self._register_failure(type(exc).__name__)
                logger.warning(
                    "Unable to fetch XAU quote (%s); backing off for %ss",
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

    def _fetch(self) -> dict[str, Any]:
        primary_error: Exception | None = None
        try:
            return self._fetch_goldprice()
        except Exception as exc:
            primary_error = exc
            logger.warning(
                "Primary XAU/USD quote source failed (%s); trying Bybit XAUUSDT fallback",
                type(exc).__name__,
            )

        try:
            return self._fetch_bybit()
        except Exception:
            if primary_error is not None:
                raise primary_error
            raise

    def _fetch_goldprice(self) -> dict[str, Any]:
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

    def _fetch_bybit(self) -> dict[str, Any]:
        request = Request(
            self.fallback_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "DKCapital/1.0",
            },
        )
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if int(payload.get("retCode", -1)) != 0:
            raise ValueError(
                f"Bybit ticker error {payload.get('retCode')}: "
                f"{payload.get('retMsg', 'unknown error')}"
            )

        rows = payload.get("result", {}).get("list") or []
        if not rows:
            raise ValueError("Bybit XAUUSDT ticker returned no rows")

        row = rows[0]
        raw_price = (
            row.get("lastPrice")
            or row.get("markPrice")
            or row.get("indexPrice")
        )
        if raw_price is None:
            raise ValueError("Bybit XAUUSDT ticker contained no usable price")

        price = float(raw_price)
        bid_raw = row.get("bid1Price")
        ask_raw = row.get("ask1Price")
        return {
            "symbol": "XAUUSD",
            "venue_symbol": "XAUUSDT",
            "price": price,
            "bid": float(bid_raw) if bid_raw else price,
            "ask": float(ask_raw) if ask_raw else price,
            "computed_at": None,
            "is_stale": False,
            "source": "bybit:XAUUSDT",
        }
