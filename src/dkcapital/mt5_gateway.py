from __future__ import annotations

import hmac
import json
import logging
import os
import re
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from dkcapital.logging_setup import configure_logging

logger = logging.getLogger("dkcapital.mt5_gateway")


def _json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default).strip() or default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


class GatewayConfig:
    def __init__(self) -> None:
        self.host = os.getenv("MT5_GATEWAY_HOST", "0.0.0.0").strip() or "0.0.0.0"
        self.port = _env_int("MT5_GATEWAY_PORT", 8765)
        self.token = os.getenv("MT5_GATEWAY_TOKEN", "").strip()
        self.demo_feed_enabled = os.getenv("MT5_DEMO_FEED_ENABLED", "false").strip().lower() in {"true", "1", "yes"}
        self.signal_state_path = _env_path(
            "SIGNAL_STATE_PATH",
            "/app/data/signal-state.json",
        )

    def validate(self) -> None:
        if len(self.token) < 24:
            raise RuntimeError(
                "MT5_GATEWAY_TOKEN must be set to a random value of at least 24 characters"
            )
        if not 1 <= self.port <= 65535:
            raise RuntimeError("MT5_GATEWAY_PORT must be between 1 and 65535")


class Mt5GatewayHandler(BaseHTTPRequestHandler):
    server_version = "DKCapitalMT5Gateway/1.0"

    @property
    def config(self) -> GatewayConfig:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("client=%s %s", self.client_address[0], fmt % args)

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not supplied.startswith(prefix):
            return False
        return hmac.compare_digest(supplied[len(prefix) :], self.config.token)

    def _write_text(self, status: HTTPStatus, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def _demo_feed(self) -> None:
        """Pipe-delimited UTF-8 protocol for local MQL5 demo-only EAs.

        Fields: S|id|status|direction|entry_low|entry_high|sl|sl_mode|
                tp1|tp2|tp3|opened_unix_seconds|remaining_fraction
        The MQL5 agent performs *all* broker/account checks and trade risk
        checks. This endpoint never submits an order.
        """
        if not self.config.demo_feed_enabled:
            self._write_json(HTTPStatus.FORBIDDEN, {"error": "demo_feed_disabled"})
            return
        state = _json(self.config.signal_state_path)
        result = ["DKDEMO1"]
        signals = state.get("signals", [])
        if not isinstance(signals, list):
            signals = []
        now = datetime.now(UTC).timestamp()
        for item in signals:
            if not isinstance(item, dict) or str(item.get("symbol", "")).upper() != "XAUUSD":
                continue
            ident = str(item.get("signal_id", ""))
            if not re.fullmatch(r"[-0-9:]{1,64}", ident):
                continue
            try:
                opened = datetime.fromisoformat(str(item["opened_at"]).replace("Z", "+00:00"))
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=UTC)
                opened_epoch = int(opened.timestamp())
                if abs(now - opened_epoch) > 172800:
                    continue
                status = str(item.get("status", "")).upper()
                direction = str(item.get("direction", "")).upper()
                if direction not in ("BUY", "SELL"):
                    continue
                if status not in ("ACTIVE", "COMPLETED", "CLOSED", "STOPPED", "BREAKEVEN", "CANCELLED", "FAILED"):
                    continue
                low = float(item["entry_low"])
                high = float(item["entry_high"])
                sl = float(item["current_sl"]) if item.get("current_sl") is not None else 0.0
                sl_mode = str(item.get("sl_mode", "PRICE")).upper()
                tps = item.get("tps") or {}
                if not isinstance(tps, dict):
                    tps = {}
                tp = [float(tps.get(str(i), 0) or 0) for i in (1, 2, 3)]
                remaining = float(item.get("remaining_fraction", 1))
                vals = [low, high, sl, *tp, remaining]
                if not all(__import__("math").isfinite(v) for v in vals):
                    continue
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            fields = [
                "S", ident, status, direction, str(low), str(high),
                str(sl), sl_mode, *(str(x) for x in tp),
                str(opened_epoch), str(remaining),
            ]
            result.append("|".join(fields))
        self._write_text(HTTPStatus.OK, "\n".join(result) + "\n")

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            self._write_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "mode": "shadow_only",
                    "time": datetime.now(UTC).isoformat(),
                },
            )
            return

        if self.path == "/v1/demo/feed":
            if not self._authorized():
                self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            self._demo_feed()
            return

        if self.path != "/v1/snapshot":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        if not self._authorized():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        state = _json(self.config.signal_state_path)
        signals = state.get("signals")
        if not isinstance(signals, list):
            signals = []

        xau_signals = [
            signal
            for signal in signals
            if isinstance(signal, dict)
            and str(signal.get("symbol") or "").upper() == "XAUUSD"
        ]
        self._write_json(
            HTTPStatus.OK,
            {
                "version": 1,
                "mode": "shadow_only",
                "generated_at": datetime.now(UTC).isoformat(),
                "source_updated_at": state.get("updated_at"),
                "signals": xau_signals,
            },
        )


def run() -> None:
    configure_logging(os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO")
    config = GatewayConfig()
    config.validate()
    server = ThreadingHTTPServer((config.host, config.port), Mt5GatewayHandler)
    server.config = config  # type: ignore[attr-defined]
    logger.info(
        "MT5 shadow gateway listening host=%s port=%s signal_state=%s",
        config.host,
        config.port,
        config.signal_state_path,
    )
    server.serve_forever(poll_interval=0.5)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
