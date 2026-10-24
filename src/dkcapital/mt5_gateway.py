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

from dkcapital.mt5_account_reporting import normalize_telemetry

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
        self.account1_login = _env_int("MT5_ACCOUNT1_LOGIN", 0)
        self.account1_telemetry_path = _env_path("MT5_ACCOUNT1_TELEMETRY_PATH", "/app/data/mt5-account1.json")
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
    # MT5 WebRequest on Wine may report 1003/5203 when reading HTTP/1.0.
    # Use explicit HTTP/1.1 framing and close each response after its body.
    protocol_version = "HTTP/1.1"

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
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

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
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()
        self.close_connection = True

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

    def _demo_v2_feed(self) -> None:
        """Read-only V2 instruction feed: signals plus attributable explicit exits.

        Broker TP/SL fills are intentionally NOT inferred from Telegram statuses.
        Historical signal entries remain visible for managing weekend-held trades;
        the EA independently rejects stale *new* entries.
        """
        if not self.config.demo_feed_enabled:
            self._write_json(HTTPStatus.FORBIDDEN, {"error": "demo_feed_disabled"})
            return
        state = _json(self.config.signal_state_path)
        lines = ["DKDEMO2"]
        for signal in state.get("signals", []):
            if not isinstance(signal, dict) or signal.get("symbol") != "XAUUSD":
                continue
            ident = str(signal.get("signal_id", ""))
            if not re.fullmatch(r"[-0-9:]{1,64}", ident):
                continue
            direction = str(signal.get("direction") or "").upper()
            status = str(signal.get("status") or "").upper()
            if direction not in ("BUY", "SELL") or status not in (
                "ACTIVE", "COMPLETED", "CLOSED", "STOPPED",
                "BREAKEVEN", "CANCELLED", "FAILED",
            ):
                continue
            try:
                opened = datetime.fromisoformat(str(signal["opened_at"]).replace("Z", "+00:00"))
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=UTC)
                prices = [float(signal["entry_low"]), float(signal["entry_high"]),
                          float(signal["current_sl"]) if signal.get("current_sl") is not None else 0.0]
                tps = signal.get("tps") or {}
                prices.extend(float(tps.get(str(i), 0) or 0) for i in (1, 2, 3))
                if not all(__import__("math").isfinite(v) and v >= 0 for v in prices):
                    continue
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            # Keep the 12-field V2 wire format: an explicit provider OPEN
            # runner is represented by the literal OPEN at the TP3 position.
            # Unspecified TP3 stays zero and is *not* an open-runner mandate.
            stop_mode = str(signal.get("sl_mode") or "PRICE").upper()
            if signal.get("be_deferred") and stop_mode != "BREAKEVEN":
                stop_mode = "DEFER_BE"
            tp3 = "OPEN" if signal.get("tp3_open") else str(prices[5])
            lines.append("|".join([
                "S", ident, status, direction,
                str(prices[0]), str(prices[1]), str(prices[2]),
                stop_mode, str(prices[3]), str(prices[4]), tp3,
                str(int(opened.timestamp())),
            ]))
            for action in signal.get("history", []):
                if not isinstance(action, dict) or action.get("kind") not in ("close", "partial_close", "add_layer"):
                    continue
                mid = action.get("message_id")
                try:
                    mid = int(mid)
                    if mid <= 0:
                        continue
                    pct = float(action.get("partial_percent") or 0)
                    if not __import__("math").isfinite(pct) or not 0 <= pct <= 100:
                        continue
                except (ValueError, TypeError):
                    continue
                if action["kind"] == "add_layer":
                    # V2 layer instructions are executable only for GWS-style
                    # signals. The EA independently checks freshness, MT5 fills,
                    # native SL, better entry, size, and all risk budgets.
                    if signal.get("source_style") != "gws":
                        continue
                    try:
                        action_at = datetime.fromisoformat(
                            str(action["timestamp"]).replace("Z", "+00:00")
                        )
                        if action_at.tzinfo is None:
                            continue
                        boundary = float(action.get("layer_max") or 0)
                        if not __import__("math").isfinite(boundary) or boundary < 0:
                            continue
                    except (KeyError, ValueError, TypeError, OverflowError):
                        continue
                    group_id = str(signal.get("group_id") or ident)
                    if not re.fullmatch(r"[-0-9:]{1,64}", group_id):
                        continue
                    lines.append("|".join([
                        "L", ident, str(mid), "10", "1",
                        str(boundary), str(int(action_at.timestamp())), group_id,
                    ]))
                else:
                    lines.append("|".join(["A", ident, str(mid),
                                          str(action["kind"]).upper(), str(pct)]))
        self._write_text(HTTPStatus.OK, "\n".join(lines) + "\n")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/v2/demo/telemetry":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._authorized():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        if self.config.account1_login <= 0:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "account1_login_unconfigured"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 262144:
                self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid_length"})
                return
            payload = json.loads(self.rfile.read(length))
            normalized = normalize_telemetry(payload, self.config.account1_login)
        except (ValueError, TypeError, UnicodeDecodeError, KeyError) as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_telemetry", "detail": str(exc)[:100]})
            return
        path = self.config.account1_telemetry_path
        try:
            # Preserve the EA's latest broker-side rejection reason across MT5
            # restarts, even though its in-memory 120-item journal resets.
            # Only merge previously authenticated, same-account snapshots.
            previous = _json(path)
            if (previous.get("login") == normalized.get("login")
                    and previous.get("mode") == "DEMO"
                    and previous.get("currency") == "GBP"):
                decisions = {}
                from datetime import datetime, timedelta, timezone
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                for item in (previous.get("decisions") or []) + (normalized.get("decisions") or []):
                    if not isinstance(item, dict):
                        continue
                    try:
                        at = datetime.fromisoformat(str(item["at"]).replace("Z", "+00:00"))
                    except (ValueError, TypeError, KeyError):
                        continue
                    if at < cutoff or at.tzinfo is None:
                        continue
                    key = (item.get("signal_id"), item.get("stage"))
                    if key not in decisions or str(item["at"]) > str(decisions[key]["at"]):
                        decisions[key] = item
                normalized["decisions"] = sorted(
                    decisions.values(), key=lambda item: str(item["at"])
                )[-500:]
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(normalized, separators=(",", ":")), encoding="utf-8")
            temp.replace(path)
        except OSError:
            logger.exception("MT5 telemetry write failure")
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "telemetry_storage_failed"})
            return
        self._write_json(HTTPStatus.OK, {"accepted": True, "account": "demo1"})

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

        if self.path == "/v2/demo/feed":
            if not self._authorized():
                self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            self._demo_v2_feed()
            return

        if self.path == "/v2/demo/account1":
            if not self._authorized():
                self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            if self.config.account1_login <= 0:
                self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "account1_login_unconfigured"})
                return
            snapshot = _json(self.config.account1_telemetry_path)
            if not snapshot or snapshot.get("login") != self.config.account1_login:
                self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "no_account1_telemetry"})
                return
            self._write_json(HTTPStatus.OK, snapshot)
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
