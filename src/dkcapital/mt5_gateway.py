from __future__ import annotations

import hmac
import json
import logging
import os
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
