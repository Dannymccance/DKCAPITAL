from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from pathlib import Path
from typing import Any

from dkcapital.bybit_api import BybitApiError, BybitV5Client, InstrumentSpec
from dkcapital.config import Settings
from dkcapital.logging_setup import configure_logging

logger = logging.getLogger("dkcapital.bybit_demo")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _event_fingerprint(item: dict[str, Any]) -> str:
    raw = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _order_link(prefix: str, signal_id: str, suffix: str = "") -> str:
    digest = hashlib.sha256(signal_id.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}{suffix}"[:36]


def _position_idx(direction: str) -> int:
    return 1 if direction.upper() == "BUY" else 2


def _entry_side(direction: str) -> str:
    return "Buy" if direction.upper() == "BUY" else "Sell"


def _exit_side(direction: str) -> str:
    return "Sell" if direction.upper() == "BUY" else "Buy"


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    if value <= 0 or step <= 0:
        return Decimal("0")
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _tick_price(value: float, tick_size: str) -> str:
    tick = _decimal(tick_size, "0.01")
    stepped = _floor_step(_decimal(value), tick)
    return _format_decimal(stepped)


def _temporary_stop(signal: dict[str, Any], fill: float) -> float:
    low = float(signal.get("entry_low") or fill)
    high = float(signal.get("entry_high") or low)
    lo, hi = min(low, high), max(low, high)
    width = max(0.0, hi - lo)
    buffer = max(6.0, width * 2.0)
    if str(signal.get("direction") or "").upper() == "BUY":
        return min(lo - buffer, fill - buffer)
    return max(hi + buffer, fill + buffer)


def _provider_stop_is_structurally_valid(signal: dict[str, Any], stop: float) -> bool:
    low = float(signal.get("entry_low") or 0.0)
    high = float(signal.get("entry_high") or low)
    direction = str(signal.get("direction") or "").upper()
    if direction == "BUY":
        return stop < min(low, high)
    return stop > max(low, high)


def _stop_is_actionable(direction: str, stop: float, market_price: float) -> bool:
    if direction.upper() == "BUY":
        return stop < market_price
    return stop > market_price


def _tp_weights(signal: dict[str, Any]) -> dict[int, float]:
    tps = signal.get("tps") or {}
    indices: list[int] = []
    for key in tps:
        try:
            indices.append(int(key))
        except (TypeError, ValueError):
            continue
    indices = sorted(set(indices))
    if not indices:
        return {}

    base = {1: 0.30, 2: 0.30, 3: 0.40}
    raw = {index: base.get(index, 0.40 if index > 3 else 0.0) for index in indices}
    total = sum(raw.values())
    if total <= 0:
        equal = 1.0 / len(indices)
        return {index: equal for index in indices}
    return {index: weight / total for index, weight in raw.items()}


class BybitDemoEngine:
    def __init__(self, settings: Settings) -> None:
        settings.validate_bybit_demo()
        self.settings = settings
        self.client = BybitV5Client(
            api_key=str(settings.bybit_demo_api_key),
            api_secret=str(settings.bybit_demo_api_secret),
            base_url=settings.bybit_demo_base_url,
        )
        self.instrument: InstrumentSpec | None = None
        self.state = _load_json(settings.bybit_demo_state_path)
        if not self.state:
            self.state = {
                "version": 1,
                "activated_at": _utc_now(),
                "environment": "bybit_demo",
                "symbol": settings.bybit_demo_symbol,
                "execution_enabled": settings.bybit_demo_execution_enabled,
                "signals": {},
            }
            _write_json(settings.bybit_demo_state_path, self.state)

    def _save(self) -> None:
        self.state["updated_at"] = _utc_now()
        self.state["execution_enabled"] = self.settings.bybit_demo_execution_enabled
        _write_json(self.settings.bybit_demo_state_path, self.state)

    def _active_for_direction(self, direction: str) -> tuple[str, dict[str, Any]] | None:
        for signal_id, record in (self.state.get("signals") or {}).items():
            if (
                str(record.get("direction") or "").upper() == direction.upper()
                and str(record.get("status") or "") == "OPEN"
            ):
                return signal_id, record
        return None

    async def _wait_for_position(
        self,
        position_idx: int,
        minimum_size: float = 0.0,
    ) -> dict[str, Any] | None:
        for _ in range(12):
            position = self.client.position(self.settings.bybit_demo_symbol, position_idx)
            if position is not None and float(position.get("size") or 0.0) > minimum_size:
                return position
            await asyncio.sleep(0.5)
        return self.client.position(self.settings.bybit_demo_symbol, position_idx)

    def _wallet_equity(self) -> float:
        wallet = self.client.wallet_balance()
        equity = float(wallet.get("totalEquity") or wallet.get("totalWalletBalance") or 0.0)
        if equity <= 0:
            raise BybitApiError("Bybit demo account has no usable equity")
        return equity

    def _size_for_risk(self, *, equity: float, fill: float, stop: float) -> Decimal:
        assert self.instrument is not None
        distance = abs(fill - stop)
        if distance <= 0:
            return Decimal("0")
        risk_usd = equity * self.settings.bybit_demo_risk_pct
        raw_qty = _decimal(risk_usd / distance)
        step = _decimal(self.instrument.qty_step, "0.001")
        qty = _floor_step(raw_qty, step)
        min_qty = _decimal(self.instrument.min_order_qty)
        max_qty = _decimal(self.instrument.max_market_order_qty)
        if min_qty > 0 and qty < min_qty:
            return Decimal("0")
        if max_qty > 0:
            qty = min(qty, max_qty)
        min_notional = _decimal(self.instrument.min_notional_value)
        if min_notional > 0 and qty * _decimal(fill) < min_notional:
            return Decimal("0")
        return qty

    async def _open_signal(self, signal: dict[str, Any], ticker: dict[str, Any]) -> None:
        signal_id = str(signal.get("signal_id") or "")
        direction = str(signal.get("direction") or "").upper()
        if not signal_id or direction not in {"BUY", "SELL"}:
            return
        if self._active_for_direction(direction) is not None:
            self.state.setdefault("signals", {})[signal_id] = {
                "status": "SKIPPED_DIRECTION_ALREADY_OPEN",
                "direction": direction,
                "observed_at": _utc_now(),
            }
            self._save()
            logger.warning(
                "Bybit demo skipped signal=%s: %s side already open",
                signal_id,
                direction,
            )
            return

        ask = float(ticker.get("ask1Price") or ticker.get("lastPrice") or 0.0)
        bid = float(ticker.get("bid1Price") or ticker.get("lastPrice") or 0.0)
        fill_reference = ask if direction == "BUY" else bid
        if fill_reference <= 0:
            raise BybitApiError("Bybit ticker returned no usable bid/ask")

        provider_stop = signal.get("current_sl")
        stop_source = "TEMPORARY"
        stop = _temporary_stop(signal, fill_reference)
        if provider_stop is not None:
            candidate = float(provider_stop)
            if _provider_stop_is_structurally_valid(
                signal,
                candidate,
            ) and _stop_is_actionable(direction, candidate, fill_reference):
                stop = candidate
                stop_source = "PROVIDER"

        equity = self._wallet_equity()
        qty = self._size_for_risk(equity=equity, fill=fill_reference, stop=stop)
        if qty <= 0:
            self.state.setdefault("signals", {})[signal_id] = {
                "status": "SKIPPED_SIZE_TOO_SMALL",
                "direction": direction,
                "fill_reference": fill_reference,
                "stop_loss": stop,
                "observed_at": _utc_now(),
            }
            self._save()
            logger.warning(
                "Bybit demo skipped signal=%s: calculated quantity is zero",
                signal_id,
            )
            return

        position_idx = _position_idx(direction)
        existing = self.client.position(self.settings.bybit_demo_symbol, position_idx)
        existing_size = float(existing.get("size") or 0.0) if existing else 0.0
        if existing_size > 0:
            self.state.setdefault("signals", {})[signal_id] = {
                "status": "SKIPPED_UNTRACKED_EXCHANGE_POSITION",
                "direction": direction,
                "exchange_size": existing_size,
                "observed_at": _utc_now(),
            }
            self._save()
            logger.error(
                "Bybit demo has untracked %s position size=%s; refusing new signal=%s",
                direction,
                existing_size,
                signal_id,
            )
            return

        order = self.client.place_market_order(
            symbol=self.settings.bybit_demo_symbol,
            side=_entry_side(direction),
            qty=_format_decimal(qty),
            position_idx=position_idx,
            order_link_id=_order_link("dkd", signal_id),
        )
        position = await self._wait_for_position(position_idx, minimum_size=0.0)
        if position is None or float(position.get("size") or 0.0) <= 0:
            raise BybitApiError(
                f"Entry accepted but no {direction} demo position appeared"
            )

        actual_entry = float(position.get("avgPrice") or fill_reference)
        actual_size = float(position.get("size") or float(qty))
        if not _stop_is_actionable(direction, stop, actual_entry):
            stop = _temporary_stop(signal, actual_entry)
            stop_source = "TEMPORARY"
        assert self.instrument is not None
        self.client.set_stop_loss(
            symbol=self.settings.bybit_demo_symbol,
            position_idx=position_idx,
            stop_loss=_tick_price(stop, self.instrument.tick_size),
        )

        processed = [
            _event_fingerprint(item) for item in (signal.get("history") or [])
        ]
        self.state.setdefault("signals", {})[signal_id] = {
            "status": "OPEN",
            "direction": direction,
            "position_idx": position_idx,
            "order_id": order.get("orderId"),
            "entry_price": actual_entry,
            "initial_qty": actual_size,
            "remaining_qty": actual_size,
            "stop_loss": stop,
            "stop_source": stop_source,
            "tp_hits": [],
            "processed_history": list(dict.fromkeys(processed))[-500:],
            "opened_at": _utc_now(),
            "source_opened_at": signal.get("opened_at"),
        }
        self._save()
        logger.info(
            "Bybit demo OPEN signal=%s side=%s qty=%s entry=%.2f stop=%.2f "
            "source=%s order=%s",
            signal_id,
            direction,
            actual_size,
            actual_entry,
            stop,
            stop_source,
            order.get("orderId"),
        )

    def _current_position_size(self, record: dict[str, Any]) -> float:
        position = self.client.position(
            self.settings.bybit_demo_symbol,
            int(record.get("position_idx") or 0),
        )
        return float(position.get("size") or 0.0) if position else 0.0

    async def _close_quantity(
        self,
        signal_id: str,
        record: dict[str, Any],
        qty: float,
        *,
        reason: str,
    ) -> None:
        assert self.instrument is not None
        step = _decimal(self.instrument.qty_step, "0.001")
        current_size = self._current_position_size(record)
        amount = _floor_step(
            _decimal(min(max(qty, 0.0), current_size)),
            step,
        )
        if amount <= 0:
            return
        direction = str(record.get("direction") or "").upper()
        reason_hash = hashlib.sha1(reason.encode()).hexdigest()[:5]
        self.client.place_market_order(
            symbol=self.settings.bybit_demo_symbol,
            side=_exit_side(direction),
            qty=_format_decimal(amount),
            position_idx=int(record.get("position_idx") or 0),
            order_link_id=_order_link("dkx", signal_id, f"-{reason_hash}"),
            reduce_only=True,
        )
        await asyncio.sleep(0.6)
        remaining = self._current_position_size(record)
        record["remaining_qty"] = remaining
        record["last_action"] = reason
        record["last_action_at"] = _utc_now()
        if remaining <= 0:
            record["status"] = "CLOSED"
            record["closed_reason"] = reason
            record["closed_at"] = _utc_now()
        self._save()
        logger.info(
            "Bybit demo REDUCE signal=%s reason=%s qty=%s remaining=%s",
            signal_id,
            reason,
            _format_decimal(amount),
            remaining,
        )

    async def _close_all(
        self,
        signal_id: str,
        record: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        current_size = self._current_position_size(record)
        if current_size <= 0:
            record["status"] = "CLOSED"
            record["closed_reason"] = reason
            record["closed_at"] = _utc_now()
            self._save()
            return
        await self._close_quantity(
            signal_id,
            record,
            current_size,
            reason=reason,
        )

    def _update_stop(
        self,
        signal_id: str,
        record: dict[str, Any],
        stop: float,
        *,
        reason: str,
    ) -> bool:
        assert self.instrument is not None
        ticker = self.client.ticker(self.settings.bybit_demo_symbol)
        market = float(ticker.get("lastPrice") or 0.0)
        direction = str(record.get("direction") or "").upper()
        if market <= 0 or not _stop_is_actionable(direction, stop, market):
            logger.warning(
                "Bybit demo ignored non-actionable stop signal=%s stop=%.2f "
                "market=%.2f reason=%s",
                signal_id,
                stop,
                market,
                reason,
            )
            return False
        self.client.set_stop_loss(
            symbol=self.settings.bybit_demo_symbol,
            position_idx=int(record.get("position_idx") or 0),
            stop_loss=_tick_price(stop, self.instrument.tick_size),
        )
        record["stop_loss"] = stop
        record["stop_source"] = reason
        record["last_action"] = reason
        record["last_action_at"] = _utc_now()
        self._save()
        logger.info(
            "Bybit demo STOP signal=%s stop=%.2f reason=%s",
            signal_id,
            stop,
            reason,
        )
        return True

    async def _handle_tp(
        self,
        signal_id: str,
        signal: dict[str, Any],
        record: dict[str, Any],
        tp_index: int,
        *,
        reason: str,
    ) -> None:
        hit_set = {int(v) for v in (record.get("tp_hits") or [])}
        if tp_index in hit_set or str(record.get("status")) != "OPEN":
            return
        weights = _tp_weights(signal)
        if tp_index not in weights:
            return
        remaining_indices = [
            idx for idx in sorted(weights) if idx not in hit_set
        ]
        if not remaining_indices:
            return
        current_size = self._current_position_size(record)
        if current_size <= 0:
            record["status"] = "CLOSED"
            self._save()
            return

        if tp_index == remaining_indices[-1]:
            close_qty = current_size
        else:
            close_qty = (
                float(record.get("initial_qty") or current_size)
                * weights[tp_index]
            )
            close_qty = min(close_qty, current_size)
        await self._close_quantity(
            signal_id,
            record,
            close_qty,
            reason=f"{reason}_tp{tp_index}",
        )
        hit_set.add(tp_index)
        record["tp_hits"] = sorted(hit_set)

        if record.get("status") == "OPEN" and tp_index == sorted(weights)[0]:
            entry = float(record.get("entry_price") or 0.0)
            if entry > 0:
                self._update_stop(
                    signal_id,
                    record,
                    entry,
                    reason="BREAKEVEN",
                )
        self._save()

    async def _process_history(
        self,
        signal_id: str,
        signal: dict[str, Any],
        record: dict[str, Any],
    ) -> None:
        processed = set(record.get("processed_history") or [])
        for item in signal.get("history") or []:
            fingerprint = _event_fingerprint(item)
            if fingerprint in processed:
                continue
            if record.get("status") != "OPEN":
                processed.add(fingerprint)
                continue

            kind = str(item.get("kind") or "")
            if kind == "partial_close":
                pct = max(
                    0.0,
                    min(
                        100.0,
                        float(item.get("partial_percent") or 0.0),
                    ),
                )
                current_size = self._current_position_size(record)
                await self._close_quantity(
                    signal_id,
                    record,
                    current_size * pct / 100.0,
                    reason="provider_partial",
                )
            elif kind == "trade_update":
                if item.get("move_sl_to_be"):
                    entry = float(record.get("entry_price") or 0.0)
                    if entry > 0:
                        self._update_stop(
                            signal_id,
                            record,
                            entry,
                            reason="PROVIDER_BREAKEVEN",
                        )
                elif item.get("sl_after") is not None:
                    self._update_stop(
                        signal_id,
                        record,
                        float(item["sl_after"]),
                        reason="PROVIDER",
                    )
                for raw_tp in item.get("tp_hits") or []:
                    try:
                        tp_index = int(raw_tp)
                    except (TypeError, ValueError):
                        continue
                    await self._handle_tp(
                        signal_id,
                        signal,
                        record,
                        tp_index,
                        reason="provider",
                    )
            elif kind in {
                "close",
                "cancel",
                "stop_loss",
                "setup_failed",
                "breakeven_close",
            }:
                await self._close_all(
                    signal_id,
                    record,
                    reason=f"provider_{kind}",
                )
            elif kind in {"add_layer", "reenter"}:
                logger.warning(
                    "Bybit demo signal=%s event=%s observed but not "
                    "auto-layered in v1",
                    signal_id,
                    kind,
                )

            processed.add(fingerprint)
            record["processed_history"] = list(processed)[-500:]
            self._save()

    async def _process_market_targets(
        self,
        signal_id: str,
        signal: dict[str, Any],
        record: dict[str, Any],
        ticker: dict[str, Any],
    ) -> None:
        if record.get("status") != "OPEN":
            return
        direction = str(record.get("direction") or "").upper()
        market = float(ticker.get("lastPrice") or 0.0)
        if market <= 0:
            return

        def _index(item: tuple[Any, Any]) -> int:
            try:
                return int(item[0])
            except (TypeError, ValueError):
                return 9999

        for raw_index, raw_target in sorted(
            (signal.get("tps") or {}).items(),
            key=_index,
        ):
            try:
                index = int(raw_index)
                target = float(raw_target)
            except (TypeError, ValueError):
                continue
            hit = market >= target if direction == "BUY" else market <= target
            if hit:
                await self._handle_tp(
                    signal_id,
                    signal,
                    record,
                    index,
                    reason="market",
                )

    async def initialise(self) -> None:
        wallet = self.client.wallet_balance()
        self.instrument = self.client.instrument(
            self.settings.bybit_demo_symbol
        )
        ticker = self.client.ticker(self.settings.bybit_demo_symbol)
        self.state["connection"] = {
            "status": "OK",
            "checked_at": _utc_now(),
            "total_equity": wallet.get("totalEquity"),
            "available_balance": wallet.get("totalAvailableBalance"),
            "last_price": ticker.get("lastPrice"),
            "qty_step": self.instrument.qty_step,
            "tick_size": self.instrument.tick_size,
        }
        self._save()

        logger.info(
            "Bybit demo connected symbol=%s equity=%s last=%s execution=%s",
            self.settings.bybit_demo_symbol,
            wallet.get("totalEquity"),
            ticker.get("lastPrice"),
            self.settings.bybit_demo_execution_enabled,
        )
        if self.settings.bybit_demo_execution_enabled:
            try:
                self.client.switch_hedge_mode(
                    self.settings.bybit_demo_symbol
                )
            except BybitApiError as exc:
                logger.warning(
                    "Could not switch hedge mode automatically: %s",
                    exc,
                )
            self.client.set_leverage(
                self.settings.bybit_demo_symbol,
                self.settings.bybit_demo_leverage,
            )

    async def run(self) -> None:
        await self.initialise()
        activation = (
            _iso(self.state.get("activated_at"))
            or datetime.now(UTC)
        )

        while True:
            try:
                ticker = self.client.ticker(
                    self.settings.bybit_demo_symbol
                )
                signal_state = _load_json(self.settings.signal_state_path)
                signals = sorted(
                    signal_state.get("signals") or [],
                    key=lambda item: (
                        str(item.get("opened_at") or ""),
                        str(item.get("signal_id") or ""),
                    ),
                )
                known = self.state.setdefault("signals", {})

                for signal in signals:
                    if str(signal.get("symbol") or "").upper() != "XAUUSD":
                        continue
                    signal_id = str(signal.get("signal_id") or "")
                    if not signal_id:
                        continue

                    record = known.get(signal_id)
                    if record is None:
                        opened = _iso(signal.get("opened_at"))
                        if opened is None or opened < activation:
                            known[signal_id] = {
                                "status": "SKIPPED_PRE_ACTIVATION",
                                "direction": signal.get("direction"),
                                "observed_at": _utc_now(),
                            }
                            self._save()
                            continue
                        age_seconds = max(
                            0.0,
                            (datetime.now(UTC) - opened).total_seconds(),
                        )
                        if (
                            age_seconds
                            > self.settings.bybit_demo_max_signal_age_seconds
                        ):
                            known[signal_id] = {
                                "status": "SKIPPED_STALE_SIGNAL",
                                "direction": signal.get("direction"),
                                "age_seconds": age_seconds,
                                "observed_at": _utc_now(),
                            }
                            self._save()
                            continue
                        if not self.settings.bybit_demo_execution_enabled:
                            known[signal_id] = {
                                "status": "OBSERVED_EXECUTION_DISABLED",
                                "direction": signal.get("direction"),
                                "observed_at": _utc_now(),
                            }
                            self._save()
                            continue
                        if str(signal.get("status") or "") == "ACTIVE":
                            await self._open_signal(signal, ticker)
                            record = known.get(signal_id)

                    if record is None or record.get("status") != "OPEN":
                        continue

                    exchange_size = self._current_position_size(record)
                    if exchange_size <= 0:
                        record["status"] = "CLOSED_BY_EXCHANGE"
                        record["closed_at"] = _utc_now()
                        self._save()
                        logger.info(
                            "Bybit demo signal=%s closed by exchange",
                            signal_id,
                        )
                        continue
                    record["remaining_qty"] = exchange_size

                    await self._process_history(
                        signal_id,
                        signal,
                        record,
                    )
                    if record.get("status") == "OPEN":
                        await self._process_market_targets(
                            signal_id,
                            signal,
                            record,
                            ticker,
                        )

                self.state["connection"] = {
                    **dict(self.state.get("connection") or {}),
                    "status": "OK",
                    "checked_at": _utc_now(),
                    "last_price": ticker.get("lastPrice"),
                }
                self._save()
            except BybitApiError as exc:
                logger.exception("Bybit demo API error: %s", exc)
                self.state["connection"] = {
                    **dict(self.state.get("connection") or {}),
                    "status": "ERROR",
                    "checked_at": _utc_now(),
                    "error": str(exc),
                }
                self._save()
            except Exception:
                logger.exception("Unexpected Bybit demo engine error")

            await asyncio.sleep(
                self.settings.bybit_demo_poll_seconds
            )


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    engine = BybitDemoEngine(settings)
    await engine.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
