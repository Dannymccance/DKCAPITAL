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
        self.profiles: dict[int, dict[str, Any]] = {}
        for account_id in (2, 3):
            prefix = f"MT5_ACCOUNT{account_id}_"
            raw = {key: os.getenv(prefix + key, "").strip()
                   for key in ("LOGIN", "MODE", "CURRENCY", "SERVER", "TOKEN")}
            if not any(raw.values()):
                continue  # Not commissioned, always fail closed.
            if not all(raw.values()):
                raise RuntimeError(f"Incomplete MT5 Account {account_id} identity")
            try:
                login = int(raw["LOGIN"])
            except ValueError as exc:
                raise RuntimeError(f"Invalid MT5 Account {account_id} login") from exc
            if (login <= 0 or raw["MODE"] not in {"DEMO", "REAL"}
                    or raw["CURRENCY"] not in {"GBP", "USD"}
                    or not re.fullmatch(r"[A-Za-z0-9._ -]{3,100}", raw["SERVER"])
                    or len(raw["TOKEN"]) < 24):
                raise RuntimeError(f"Unsafe MT5 Account {account_id} configuration")
            self.profiles[account_id] = {
                "login": login, "mode": raw["MODE"],
                "currency": raw["CURRENCY"], "server": raw["SERVER"],
                "token": raw["TOKEN"],
                "telemetry_path": Path(f"/app/data/mt5-account{account_id}.json"),
            }
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
        all_logins = [self.account1_login] if self.account1_login > 0 else []
        all_tokens = [self.token]
        for profile in self.profiles.values():
            all_logins.append(profile["login"])
            all_tokens.append(profile["token"])
        if len(all_logins) != len(set(all_logins)):
            raise RuntimeError("Duplicate MT5 account login across profiles")
        if len(all_tokens) != len(set(all_tokens)):
            raise RuntimeError("MT5 profiles must have independent bearer tokens")


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

    def _authorized(self, token: str | None = None) -> bool:
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not supplied.startswith(prefix):
            return False
        return hmac.compare_digest(supplied[len(prefix) :], token or self.config.token)

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
                if item.get("high_risk") and status == "ACTIVE":
                    status = "CANCELLED"  # Legacy EA must not enter at normal risk.
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

    def _demo_v2_feed(self, *, v3: bool = False, v4: bool = False,
                      v5: bool = False, profile_header: str | None = None,
                      profile_account: int | None = None) -> None:
        """Read-only V2 instruction feed: signals plus attributable explicit exits.

        Broker TP/SL fills are intentionally NOT inferred from Telegram statuses.
        Historical signal entries remain visible for managing weekend-held trades;
        the EA independently rejects stale *new* entries.
        """
        if not self.config.demo_feed_enabled and profile_header is None:
            self._write_json(HTTPStatus.FORBIDDEN, {"error": "demo_feed_disabled"})
            return
        state = _json(self.config.signal_state_path)
        lines = [profile_header or ("DKDEMO5" if v5 else ("DKDEMO4" if v4 else ("DKDEMO3" if v3 else "DKDEMO2")))]
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
                prices.extend(float(tps.get(str(i), 0) or 0) for i in (1, 2, 3, 4, 5, 6))
                if not all(__import__("math").isfinite(v) and v >= 0 for v in prices):
                    continue
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            # Every numeric target must be TP1..TPN, with N <= 6.
            # Do not invent targets, or silently drop TP5/TP6.
            try:
                nums = sorted(int(k) for k in tps)
            except (ValueError, TypeError):
                continue
            if (nums and nums != list(range(1, len(nums) + 1))) or len(nums) > 6:
                continue
            count = len(nums)
            # Account 2/3 only: fail closed for a GWS entry with fewer
            # than two actual numeric take-profits. A timer must NEVER
            # turn a partial TP1-only signal into an executable trade.
            # No CANCELLED instruction is emitted, because that could
            # unintentionally close an existing position. An existing
            # single-target position requires separate broker management
            # review before deploying this filter.
            if (profile_account in (2, 3) and status == "ACTIVE"
                    and count < 2 and signal.get("source_style") == "gws"):
                logger.warning(
                    "Account %s GWS entry held pending >=2 native targets: signal=%s target_count=%s",
                    profile_account, ident, count,
                )
                continue
            if signal.get("tp3_open") and (count != 2 or any(prices[6:])):
                continue
            # Older EAs have only 3 or 4 target slots. Mark unsupported
            # ACTIVE rows CANCELLED, rather than cause an incomplete fill.
            supported = 6 if (v4 or v5) else (4 if v3 else 3)
            if (count > supported or (signal.get("high_risk") and not v5)) and status == "ACTIVE":
                status = "CANCELLED"  # Prevent legacy 4% high-risk entries.
            stop_mode = str(signal.get("sl_mode") or "PRICE").upper()
            if signal.get("be_deferred") and stop_mode != "BREAKEVEN":
                stop_mode = "DEFER_BE"
            tp3 = "OPEN" if signal.get("tp3_open") else str(prices[5])
            fields = [
                "S", ident, status, direction,
                str(prices[0]), str(prices[1]), str(prices[2]),
                stop_mode, str(prices[3]), str(prices[4]), tp3,
            ]
            if v4 or v5:
                fields.extend(str(value) for value in prices[6:9])
            elif v3 and count >= 4:
                fields.append(str(prices[6]))
            if v5:
                fields.append("HIGH" if signal.get("high_risk") else "STANDARD")
            fields.append(str(int(opened.timestamp())))
            lines.append("|".join(fields))
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
        profile_match = re.fullmatch(r"/v5/accounts/([23])/telemetry", self.path)
        profile = self.config.profiles.get(int(profile_match.group(1))) if profile_match else None
        if self.path != "/v2/demo/telemetry" and not profile_match:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if profile_match and profile is None:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "profile_not_commissioned"})
            return
        if not self._authorized(profile["token"] if profile else None):
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        expected_login = profile["login"] if profile else self.config.account1_login
        if expected_login <= 0:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "account_unconfigured"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 262144:
                self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid_length"})
                return
            payload = json.loads(self.rfile.read(length))
            normalized = normalize_telemetry(
                payload, expected_login,
                expected_mode=profile["mode"] if profile else "DEMO",
                expected_currency=profile["currency"] if profile else "GBP",
                expected_server=profile["server"] if profile else None,
            )
        except (ValueError, TypeError, UnicodeDecodeError, KeyError) as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_telemetry", "detail": str(exc)[:100]})
            return
        path = profile["telemetry_path"] if profile else self.config.account1_telemetry_path
        try:
            # Preserve the EA's latest broker-side rejection reason across MT5
            # restarts, even though its in-memory 120-item journal resets.
            # Only merge previously authenticated, same-account snapshots.
            previous = _json(path)
            if (previous.get("login") == normalized.get("login")
                    and previous.get("mode") == normalized.get("mode")
                    and previous.get("currency") == normalized.get("currency")
                    and previous.get("server") == normalized.get("server")):
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
        self._write_json(HTTPStatus.OK, {"accepted": True, "account": (
            f"profile{profile_match.group(1)}" if profile_match else "demo1"
        )})

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

        profile_match = re.fullmatch(r"/v5/accounts/([23])/(feed|snapshot)", self.path)
        if profile_match:
            account_id = int(profile_match.group(1))
            profile = self.config.profiles.get(account_id)
            if profile is None:
                self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "profile_not_commissioned"})
                return
            if not self._authorized(profile["token"]):
                self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            if profile_match.group(2) == "feed":
                self._demo_v2_feed(v5=True, profile_header=f"DKACCOUNT{account_id}V5", profile_account=account_id)
            else:
                snapshot = _json(profile["telemetry_path"])
                if (snapshot.get("login") != profile["login"]
                        or snapshot.get("mode") != profile["mode"]
                        or snapshot.get("currency") != profile["currency"]
                        or snapshot.get("server") != profile["server"]):
                    self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "no_verified_profile_telemetry"})
                    return
                self._write_json(HTTPStatus.OK, snapshot)
            return

        if self.path in ("/v2/demo/feed", "/v3/demo/feed", "/v4/demo/feed", "/v5/demo/feed"):
            if not self._authorized():
                self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            self._demo_v2_feed(v3=self.path == "/v3/demo/feed", v4=self.path == "/v4/demo/feed",
                               v5=self.path == "/v5/demo/feed")
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
