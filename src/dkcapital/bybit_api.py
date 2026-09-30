from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BybitApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    tick_size: str
    qty_step: str
    min_order_qty: str
    max_market_order_qty: str
    min_notional_value: str


class BybitV5Client:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api-demo.bybit.com",
        recv_window: int = 5000,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        method = method.upper()
        query = ""
        if params:
            query = urlencode(
                [(key, str(value)) for key, value in sorted(params.items()) if value is not None]
            )
        payload = ""
        if body is not None:
            payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DKCapital-BybitDemo/1.0",
        }
        if authenticated:
            timestamp = str(int(time.time() * 1000))
            prehash = f"{timestamp}{self.api_key}{self.recv_window}{query if method == 'GET' else payload}"
            signature = hmac.new(
                self.api_secret.encode("utf-8"),
                prehash.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            headers.update(
                {
                    "X-BAPI-API-KEY": self.api_key,
                    "X-BAPI-SIGN": signature,
                    "X-BAPI-TIMESTAMP": timestamp,
                    "X-BAPI-RECV-WINDOW": str(self.recv_window),
                }
            )

        data = payload.encode("utf-8") if method != "GET" and body is not None else None
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                decoded = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BybitApiError(f"Bybit HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise BybitApiError(f"Bybit connection failed: {exc.reason}") from exc

        try:
            result = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise BybitApiError(f"Bybit returned invalid JSON: {decoded[:300]}") from exc

        if int(result.get("retCode", -1)) != 0:
            raise BybitApiError(
                f"Bybit error {result.get('retCode')}: {result.get('retMsg', 'unknown error')}"
            )
        return result

    def wallet_balance(self) -> dict[str, Any]:
        response = self._request(
            "GET",
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED", "coin": "USDT"},
        )
        rows = response.get("result", {}).get("list") or []
        if not rows:
            raise BybitApiError("Bybit demo wallet returned no UNIFIED account row")
        return rows[0]

    def instrument(self, symbol: str) -> InstrumentSpec:
        response = self._request(
            "GET",
            "/v5/market/instruments-info",
            params={"category": "linear", "symbol": symbol},
            authenticated=False,
        )
        rows = response.get("result", {}).get("list") or []
        if not rows:
            raise BybitApiError(f"Bybit instrument not found: {symbol}")
        row = rows[0]
        lot = row.get("lotSizeFilter") or {}
        price = row.get("priceFilter") or {}
        return InstrumentSpec(
            symbol=str(row.get("symbol") or symbol),
            tick_size=str(price.get("tickSize") or "0.01"),
            qty_step=str(lot.get("qtyStep") or "0.001"),
            min_order_qty=str(lot.get("minOrderQty") or "0"),
            max_market_order_qty=str(lot.get("maxMktOrderQty") or "0"),
            min_notional_value=str(lot.get("minNotionalValue") or "0"),
        )

    def ticker(self, symbol: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            "/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            authenticated=False,
        )
        rows = response.get("result", {}).get("list") or []
        if not rows:
            raise BybitApiError(f"Bybit ticker not found: {symbol}")
        return rows[0]

    def switch_hedge_mode(self, symbol: str) -> None:
        self._request(
            "POST",
            "/v5/position/switch-mode",
            body={"category": "linear", "symbol": symbol, "mode": 3},
        )

    def set_leverage(self, symbol: str, leverage: int) -> None:
        value = str(leverage)
        self._request(
            "POST",
            "/v5/position/set-leverage",
            body={
                "category": "linear",
                "symbol": symbol,
                "buyLeverage": value,
                "sellLeverage": value,
            },
        )

    def positions(self, symbol: str) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            "/v5/position/list",
            params={"category": "linear", "symbol": symbol},
        )
        return list(response.get("result", {}).get("list") or [])

    def position(self, symbol: str, position_idx: int) -> dict[str, Any] | None:
        for row in self.positions(symbol):
            try:
                row_idx = int(row.get("positionIdx") or 0)
            except (TypeError, ValueError):
                continue
            if row_idx == position_idx:
                return row
        return None

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: str,
        position_idx: int,
        order_link_id: str,
        reduce_only: bool = False,
        close_on_trigger: bool = False,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/v5/order/create",
            body={
                "category": "linear",
                "symbol": symbol,
                "side": side,
                "orderType": "Market",
                "qty": qty,
                "positionIdx": position_idx,
                "orderLinkId": order_link_id[:36],
                "reduceOnly": reduce_only,
                "closeOnTrigger": close_on_trigger,
            },
        )
        return dict(response.get("result") or {})

    def set_stop_loss(
        self,
        *,
        symbol: str,
        position_idx: int,
        stop_loss: str,
    ) -> None:
        self._request(
            "POST",
            "/v5/position/trading-stop",
            body={
                "category": "linear",
                "symbol": symbol,
                "tpslMode": "Full",
                "stopLoss": stop_loss,
                "slTriggerBy": "LastPrice",
                "slOrderType": "Market",
                "positionIdx": position_idx,
            },
        )
