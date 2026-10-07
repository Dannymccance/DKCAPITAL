from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

from dkcapital.mt5_shadow import (
    TERMINAL_SIGNAL_STATUSES,
    is_stale_signal,
    lots_for_risk,
    masked_login,
    protective_stop_for_signal,
    signal_fingerprint,
    utc_now,
)

try:
    import MetaTrader5 as mt5
except ImportError as exc:  # pragma: no cover - Windows-only dependency
    raise SystemExit(
        "MetaTrader5 is not installed. Run windows/install.ps1 with Python 3.11."
    ) from exc

load_dotenv(Path(__file__).with_name(".env"))

logger = logging.getLogger("dkcapital.mt5_shadow")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


class Config:
    def __init__(self) -> None:
        self.shadow_only = _bool("MT5_SHADOW_ONLY", True)
        self.gateway_url = (
            os.getenv("MT5_GATEWAY_URL", "http://127.0.0.1:8765").strip().rstrip("/")
        )
        self.gateway_token = os.getenv("MT5_GATEWAY_TOKEN", "").strip()
        self.terminal_path = os.getenv("MT5_TERMINAL_PATH", "").strip()
        self.account_login = os.getenv("MT5_ACCOUNT_LOGIN", "").strip()
        self.account_password = os.getenv("MT5_ACCOUNT_PASSWORD", "")
        self.account_server = os.getenv("MT5_ACCOUNT_SERVER", "").strip()
        self.symbol = os.getenv("MT5_SYMBOL", "XAUUSD").strip() or "XAUUSD"
        self.risk_pct = _float("MT5_SHADOW_RISK_PCT", 0.005)
        self.poll_seconds = max(1.0, _float("MT5_POLL_SECONDS", 2.0))
        self.max_signal_age_seconds = max(
            0.0,
            _float("MT5_SHADOW_MAX_SIGNAL_AGE_SECONDS", 180.0),
        )
        self.state_path = Path(
            os.getenv(
                "MT5_SHADOW_STATE_PATH",
                str(Path(__file__).with_name("data") / "mt5-shadow.json"),
            )
        )
        self.http_timeout_seconds = max(1, _int("MT5_HTTP_TIMEOUT_SECONDS", 10))

    def validate(self) -> None:
        if not self.shadow_only:
            raise RuntimeError(
                "This DK Capital executor is shadow-only and cannot be switched to live execution."
            )
        if len(self.gateway_token) < 24:
            raise RuntimeError("MT5_GATEWAY_TOKEN must be at least 24 characters")
        if not 0 < self.risk_pct <= 0.05:
            raise RuntimeError("MT5_SHADOW_RISK_PCT must be greater than 0 and <= 0.05")


class ShadowExecutor:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.state = _load_json(config.state_path)
        if not self.state:
            self.state = {
                "version": 1,
                "mode": "shadow_only",
                "activated_at": utc_now(),
                "signals": {},
            }
            _write_json(config.state_path, self.state)
        self.symbol_name: str | None = None

    def connect(self) -> None:
        initialize_ok = (
            mt5.initialize(self.config.terminal_path)
            if self.config.terminal_path
            else mt5.initialize()
        )
        if not initialize_ok:
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

        if self.config.account_login:
            login = int(self.config.account_login)
            kwargs: dict[str, Any] = {}
            if self.config.account_password:
                kwargs["password"] = self.config.account_password
            if self.config.account_server:
                kwargs["server"] = self.config.account_server
            if not mt5.login(login, **kwargs):
                raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")

        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"MT5 account_info failed: {mt5.last_error()}")

        self.symbol_name = self._resolve_symbol(self.config.symbol)
        logger.info(
            "MT5 connected account=%s server=%s currency=%s symbol=%s shadow_only=true",
            masked_login(account.login),
            account.server,
            account.currency,
            self.symbol_name,
        )

    def _resolve_symbol(self, preferred: str) -> str:
        candidates = [preferred, "XAUUSD", "GOLD"]
        checked: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in checked:
                continue
            checked.add(candidate)
            info = mt5.symbol_info(candidate)
            if info is not None:
                if not info.visible and not mt5.symbol_select(candidate, True):
                    continue
                return candidate

        symbols = mt5.symbols_get() or ()
        ranked: list[str] = []
        for item in symbols:
            name = str(getattr(item, "name", ""))
            upper = name.upper()
            if "XAUUSD" in upper or upper.startswith("GOLD"):
                ranked.append(name)
        ranked.sort(
            key=lambda name: (
                0 if name.upper().startswith("XAUUSD") else 1,
                len(name),
            )
        )
        for name in ranked:
            info = mt5.symbol_info(name)
            if info is None:
                continue
            if not info.visible and not mt5.symbol_select(name, True):
                continue
            return name
        raise RuntimeError(
            f"Could not resolve an MT5 gold symbol from preferred name {preferred!r}"
        )

    def _fetch_snapshot(self) -> dict[str, Any]:
        request = Request(
            f"{self.config.gateway_url}/v1/snapshot",
            headers={
                "Authorization": f"Bearer {self.config.gateway_token}",
                "Accept": "application/json",
                "User-Agent": "DKCapital-MT5-Shadow/1.0",
            },
            method="GET",
        )
        try:
            with urlopen(
                request,
                timeout=self.config.http_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"MT5 gateway HTTP {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"MT5 gateway unavailable: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("mode") != "shadow_only":
            raise RuntimeError("MT5 gateway returned an invalid or non-shadow payload")
        return payload

    def _account_snapshot(self) -> tuple[Any, dict[str, Any]]:
        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"MT5 account_info failed: {mt5.last_error()}")
        hedging_constant = getattr(mt5, "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", None)
        hedging = (
            hedging_constant is not None
            and account.margin_mode == hedging_constant
        )
        return account, {
            "login": masked_login(account.login),
            "server": account.server,
            "company": account.company,
            "currency": account.currency,
            "balance": float(account.balance),
            "equity": float(account.equity),
            "leverage": int(account.leverage),
            "margin_mode": int(account.margin_mode),
            "hedging": bool(hedging),
            "trade_allowed": bool(account.trade_allowed),
            "trade_expert": bool(account.trade_expert),
        }

    def _symbol_snapshot(self) -> tuple[Any, Any, dict[str, Any]]:
        assert self.symbol_name is not None
        info = mt5.symbol_info(self.symbol_name)
        tick = mt5.symbol_info_tick(self.symbol_name)
        if info is None or tick is None:
            raise RuntimeError(f"MT5 symbol/tick unavailable for {self.symbol_name}")
        return info, tick, {
            "name": self.symbol_name,
            "bid": float(tick.bid),
            "ask": float(tick.ask),
            "point": float(info.point),
            "trade_tick_size": float(info.trade_tick_size),
            "trade_tick_value": float(info.trade_tick_value),
            "trade_contract_size": float(info.trade_contract_size),
            "volume_min": float(info.volume_min),
            "volume_max": float(info.volume_max),
            "volume_step": float(info.volume_step),
        }

    def _initial_record(
        self,
        signal: dict[str, Any],
        account: Any,
        info: Any,
        tick: Any,
    ) -> dict[str, Any]:
        signal_id = str(signal.get("signal_id") or "")
        direction = str(signal.get("direction") or "").upper()
        status = str(signal.get("status") or "").upper()
        fingerprint = signal_fingerprint(signal)
        now = datetime.now(UTC)

        base: dict[str, Any] = {
            "signal_id": signal_id,
            "direction": direction,
            "source_status": status,
            "opened_at": signal.get("opened_at"),
            "last_signal_fingerprint": fingerprint,
            "last_seen_at": utc_now(),
            "tp_hits": list(signal.get("tp_hits") or []),
            "remaining_fraction": float(signal.get("remaining_fraction") or 0.0),
            "audit": [],
        }

        if status != "ACTIVE":
            base["shadow_status"] = "NOT_OPENED_SIGNAL_INACTIVE"
            base["audit"].append(
                {"at": utc_now(), "event": "inactive_on_first_observation"}
            )
            return base

        if is_stale_signal(
            signal,
            now=now,
            max_age_seconds=self.config.max_signal_age_seconds,
        ):
            base["shadow_status"] = "STALE_SKIPPED"
            base["audit"].append(
                {"at": utc_now(), "event": "stale_signal_skipped"}
            )
            return base

        if direction not in {"BUY", "SELL"}:
            base["shadow_status"] = "REJECTED_DIRECTION"
            return base

        fill = float(tick.ask if direction == "BUY" else tick.bid)
        if fill <= 0:
            base["shadow_status"] = "REJECTED_NO_MARKET_PRICE"
            return base

        stop, stop_source = protective_stop_for_signal(signal, fill)
        order_type = (
            mt5.ORDER_TYPE_BUY
            if direction == "BUY"
            else mt5.ORDER_TYPE_SELL
        )
        loss = mt5.order_calc_profit(
            order_type,
            self.symbol_name,
            1.0,
            fill,
            stop,
        )
        if loss is None:
            base["shadow_status"] = "REJECTED_PROFIT_CALC"
            base["mt5_error"] = str(mt5.last_error())
            return base

        loss_per_lot = abs(float(loss))
        volume, planned_risk = lots_for_risk(
            equity=float(account.equity),
            risk_pct=self.config.risk_pct,
            loss_per_lot=loss_per_lot,
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
        )
        if volume <= 0:
            base["shadow_status"] = "REJECTED_SIZE_TOO_SMALL"
            base["loss_per_lot_at_stop"] = loss_per_lot
            return base

        base.update(
            {
                "shadow_status": "SHADOW_OPEN",
                "entry_reference": fill,
                "planned_stop": stop,
                "stop_source": stop_source,
                "planned_volume_lots": volume,
                "planned_risk_account_currency": planned_risk,
                "risk_pct": self.config.risk_pct,
                "account_equity_at_plan": float(account.equity),
                "account_currency": account.currency,
                "tps": dict(signal.get("tps") or {}),
            }
        )
        base["audit"].append(
            {
                "at": utc_now(),
                "event": "shadow_open_planned",
                "volume_lots": volume,
                "entry_reference": fill,
                "stop": stop,
                "stop_source": stop_source,
            }
        )
        return base

    def _update_record(
        self,
        record: dict[str, Any],
        signal: dict[str, Any],
    ) -> bool:
        fingerprint = signal_fingerprint(signal)
        if record.get("last_signal_fingerprint") == fingerprint:
            record["last_seen_at"] = utc_now()
            return False

        status = str(signal.get("status") or "").upper()
        record["last_signal_fingerprint"] = fingerprint
        record["last_seen_at"] = utc_now()
        record["source_status"] = status
        record["tp_hits"] = list(signal.get("tp_hits") or [])
        record["remaining_fraction"] = float(
            signal.get("remaining_fraction") or 0.0
        )
        record["tps"] = dict(signal.get("tps") or {})
        record["provider_sl"] = signal.get("current_sl")
        record["provider_sl_mode"] = signal.get("sl_mode")

        if record.get("shadow_status") == "SHADOW_OPEN":
            entry_reference = float(record.get("entry_reference") or 0.0)
            if entry_reference > 0:
                stop, source = protective_stop_for_signal(
                    signal,
                    entry_reference,
                )
                record["planned_stop"] = stop
                record["stop_source"] = source

        if status in TERMINAL_SIGNAL_STATUSES:
            record["shadow_status"] = "SHADOW_TERMINAL"

        audit = record.setdefault("audit", [])
        audit.append(
            {
                "at": utc_now(),
                "event": "signal_state_changed",
                "source_status": status,
                "tp_hits": record["tp_hits"],
                "remaining_fraction": record["remaining_fraction"],
                "planned_stop": record.get("planned_stop"),
                "stop_source": record.get("stop_source"),
            }
        )
        del audit[:-200]
        return True

    def cycle(self) -> None:
        account, account_payload = self._account_snapshot()
        info, tick, symbol_payload = self._symbol_snapshot()
        snapshot = self._fetch_snapshot()
        signals = snapshot.get("signals") or []
        if not isinstance(signals, list):
            raise RuntimeError("MT5 gateway signals field is invalid")

        records = self.state.setdefault("signals", {})
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            signal_id = str(signal.get("signal_id") or "")
            if not signal_id:
                continue
            record = records.get(signal_id)
            if not isinstance(record, dict):
                record = self._initial_record(
                    signal,
                    account,
                    info,
                    tick,
                )
                records[signal_id] = record
                logger.info(
                    "signal=%s shadow_status=%s direction=%s lots=%s",
                    signal_id,
                    record.get("shadow_status"),
                    record.get("direction"),
                    record.get("planned_volume_lots", "-"),
                )
            elif self._update_record(record, signal):
                logger.info(
                    "signal=%s updated source_status=%s shadow_status=%s",
                    signal_id,
                    record.get("source_status"),
                    record.get("shadow_status"),
                )

        assert self.symbol_name is not None
        positions = mt5.positions_get(symbol=self.symbol_name)
        real_positions = []
        for position in positions or ():
            real_positions.append(
                {
                    "ticket": int(position.ticket),
                    "type": int(position.type),
                    "volume": float(position.volume),
                    "price_open": float(position.price_open),
                    "sl": float(position.sl),
                    "tp": float(position.tp),
                    "price_current": float(position.price_current),
                    "profit": float(position.profit),
                    "comment": str(position.comment),
                }
            )

        self.state["mode"] = "shadow_only"
        self.state["updated_at"] = utc_now()
        self.state["gateway_source_updated_at"] = snapshot.get(
            "source_updated_at"
        )
        self.state["account"] = account_payload
        self.state["symbol"] = symbol_payload
        self.state["observed_real_positions"] = real_positions
        _write_json(self.config.state_path, self.state)

    def run(self) -> None:
        self.connect()
        try:
            while True:
                try:
                    self.cycle()
                except Exception:
                    logger.exception("MT5 shadow cycle failed")
                time.sleep(self.config.poll_seconds)
        finally:
            mt5.shutdown()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = Config()
    config.validate()
    executor = ShadowExecutor(config)
    executor.run()


if __name__ == "__main__":
    main()
