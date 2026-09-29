from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


PAPER_ENGINE_VERSION = 2
OPEN_POSITION_STATUSES = {"OPEN"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class PaperCandidate:
    signal_id: str
    symbol: str
    direction: str
    source_style: str | None
    chat_id: int | None
    root_message_id: int | None
    opened_at: str
    first_seen_at: str
    last_seen_at: str
    initial_snapshot: bool
    signal_status: str
    entry_low: float
    entry_high: float
    original_sl: float | None
    current_sl: float | None
    tps: dict[str, float] = field(default_factory=dict)
    remaining_fraction: float = 1.0
    partial_close_count: int = 0
    source_message_ids: list[int] = field(default_factory=list)
    signal_history: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: str = ""
    execution_status: str = "OBSERVED"
    execution_note: str = ""
    processed_history_fingerprints: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaperPosition:
    position_id: str
    signal_id: str
    symbol: str
    direction: str
    opened_at: str
    entry_price: float
    initial_quantity_oz: float
    remaining_quantity_oz: float
    stop_loss: float | None
    status: str = "OPEN"
    last_mark_price: float | None = None
    unrealized_pnl_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    closed_at: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    # Strategy/accounting metadata.
    initial_stop_loss: float | None = None
    initial_risk_usd: float = 0.0
    lot_size: float = 0.0
    breakeven_locked: bool = False
    tp_prices: dict[str, float] = field(default_factory=dict)
    tp_close_quantities_oz: dict[str, float] = field(default_factory=dict)
    eligible_tp_indices: list[int] = field(default_factory=list)
    tp_hits: list[int] = field(default_factory=list)
    be_trigger_tp_index: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PaperAccount:
    """Durable accounting core for the XAUUSD paper engine.

    Quantity is stored in troy ounces. For XAUUSD, a $1 price move produces
    $1 PnL per ounce, which keeps the ledger independent from a broker's lot
    representation. The strategy layer converts lots into ounces explicitly.
    """

    def __init__(
        self,
        *,
        starting_balance_usd: float = 100_000.0,
        symbol: str = "XAUUSD",
        strategy_mode: str = "observe_only",
        entry_policy: str = "all_parsed",
    ) -> None:
        if starting_balance_usd <= 0:
            raise ValueError("starting_balance_usd must be positive")

        self.version = PAPER_ENGINE_VERSION
        self.symbol = symbol.upper()
        self.strategy_mode = strategy_mode
        self.entry_policy = entry_policy
        self.strategy_activated_at: str | None = None

        self.starting_balance_usd = float(starting_balance_usd)
        self.balance_usd = float(starting_balance_usd)
        self.equity_usd = float(starting_balance_usd)
        self.realized_pnl_usd = 0.0
        self.unrealized_pnl_usd = 0.0

        self.peak_equity_usd = float(starting_balance_usd)
        self.max_drawdown_usd = 0.0
        self.max_drawdown_pct = 0.0

        self.trading_day: str | None = None
        self.day_start_balance_usd = float(starting_balance_usd)
        self.daily_equity_floor_usd = float(starting_balance_usd)
        self.daily_stop_triggered = False
        self.daily_stop_triggered_at: str | None = None

        self.last_mark_price: float | None = None
        self.last_bid: float | None = None
        self.last_ask: float | None = None
        self.last_mark_at: str | None = None
        self.created_at = utc_now()
        self.updated_at = self.created_at

        self.candidates: dict[str, PaperCandidate] = {}
        self.positions: dict[str, PaperPosition] = {}
        self.audit: list[dict[str, Any]] = []

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> "PaperAccount":
        account = cls(
            starting_balance_usd=float(payload.get("starting_balance_usd", 100_000.0)),
            symbol=str(payload.get("symbol") or "XAUUSD"),
            strategy_mode=str(payload.get("strategy_mode") or "observe_only"),
            entry_policy=str(payload.get("entry_policy") or "all_parsed"),
        )
        account.version = int(payload.get("version", PAPER_ENGINE_VERSION))
        account.strategy_activated_at = payload.get("strategy_activated_at")

        account.balance_usd = float(payload.get("balance_usd", account.starting_balance_usd))
        account.equity_usd = float(payload.get("equity_usd", account.balance_usd))
        account.realized_pnl_usd = float(payload.get("realized_pnl_usd", 0.0))
        account.unrealized_pnl_usd = float(payload.get("unrealized_pnl_usd", 0.0))
        account.peak_equity_usd = float(
            payload.get(
                "peak_equity_usd",
                max(account.starting_balance_usd, account.equity_usd),
            )
        )
        account.max_drawdown_usd = float(payload.get("max_drawdown_usd", 0.0))
        account.max_drawdown_pct = float(payload.get("max_drawdown_pct", 0.0))

        account.trading_day = payload.get("trading_day")
        account.day_start_balance_usd = float(
            payload.get("day_start_balance_usd", account.balance_usd)
        )
        account.daily_equity_floor_usd = float(
            payload.get("daily_equity_floor_usd", account.day_start_balance_usd)
        )
        account.daily_stop_triggered = bool(payload.get("daily_stop_triggered", False))
        account.daily_stop_triggered_at = payload.get("daily_stop_triggered_at")

        account.last_mark_price = (
            float(payload["last_mark_price"])
            if payload.get("last_mark_price") is not None
            else None
        )
        account.last_bid = (
            float(payload["last_bid"])
            if payload.get("last_bid") is not None
            else account.last_mark_price
        )
        account.last_ask = (
            float(payload["last_ask"])
            if payload.get("last_ask") is not None
            else account.last_mark_price
        )
        account.last_mark_at = payload.get("last_mark_at")
        account.created_at = str(payload.get("created_at") or account.created_at)
        account.updated_at = str(payload.get("updated_at") or account.updated_at)
        account.audit = list(payload.get("audit") or [])

        for raw in payload.get("candidates") or []:
            candidate = PaperCandidate(**raw)
            if (
                account.entry_policy == "all_parsed"
                and candidate.execution_status == "OBSERVED"
            ):
                candidate.execution_status = "PENDING_STRATEGY"
            account.candidates[candidate.signal_id] = candidate

        for raw in payload.get("positions") or []:
            position = PaperPosition(**raw)
            account.positions[position.position_id] = position

        account._revalue()
        return account

    def _audit(self, kind: str, **data: Any) -> None:
        self.audit.append({"timestamp": utc_now(), "kind": kind, **data})
        self.audit = self.audit[-1000:]
        self.updated_at = utc_now()

    def activate_strategy(self, mode: str, *, activated_at: str | None = None) -> None:
        self.strategy_mode = mode
        if mode != "observe_only" and self.strategy_activated_at is None:
            self.strategy_activated_at = activated_at or utc_now()
            self._audit(
                "strategy_activated",
                strategy_mode=mode,
                strategy_activated_at=self.strategy_activated_at,
            )

    def ensure_trading_day(
        self,
        local_date: str,
        *,
        daily_loss_pct: float,
    ) -> None:
        if self.trading_day == local_date:
            return
        self.trading_day = local_date
        self.day_start_balance_usd = self.balance_usd
        self.daily_equity_floor_usd = self.day_start_balance_usd * (
            1.0 - daily_loss_pct
        )
        self.daily_stop_triggered = False
        self.daily_stop_triggered_at = None
        self._audit(
            "trading_day_started",
            trading_day=local_date,
            day_start_balance_usd=self.day_start_balance_usd,
            daily_equity_floor_usd=self.daily_equity_floor_usd,
        )

    def check_daily_stop(self, *, timestamp: str | None = None) -> bool:
        if self.daily_stop_triggered:
            return True
        if self.equity_usd <= self.daily_equity_floor_usd:
            self.daily_stop_triggered = True
            self.daily_stop_triggered_at = timestamp or utc_now()
            self._audit(
                "daily_stop_triggered",
                equity_usd=self.equity_usd,
                daily_equity_floor_usd=self.daily_equity_floor_usd,
            )
        return self.daily_stop_triggered

    def sync_signal(
        self,
        signal: dict[str, Any],
        *,
        initial_snapshot: bool = False,
        observed_at: str | None = None,
    ) -> tuple[bool, str]:
        signal_id = str(signal.get("signal_id") or "")
        symbol = str(signal.get("symbol") or "").upper()
        direction = str(signal.get("direction") or "").upper()

        if not signal_id:
            return False, "missing_signal_id"
        if symbol != self.symbol:
            return False, "symbol_filtered"
        if direction not in {"BUY", "SELL"}:
            return False, "invalid_direction"

        now = observed_at or utc_now()
        relevant = {
            "signal_id": signal_id,
            "symbol": symbol,
            "direction": direction,
            "source_style": signal.get("source_style"),
            "chat_id": signal.get("chat_id"),
            "root_message_id": signal.get("root_message_id"),
            "opened_at": signal.get("opened_at"),
            "status": signal.get("status"),
            "entry_low": signal.get("entry_low"),
            "entry_high": signal.get("entry_high"),
            "original_sl": signal.get("original_sl"),
            "current_sl": signal.get("current_sl"),
            "tps": signal.get("tps") or {},
            "remaining_fraction": signal.get("remaining_fraction", 1.0),
            "partial_close_count": signal.get("partial_close_count", 0),
            "source_message_ids": signal.get("source_message_ids") or [],
            "history": signal.get("history") or [],
        }
        fingerprint = _fingerprint(relevant)

        existing = self.candidates.get(signal_id)
        if existing is not None and existing.fingerprint == fingerprint:
            return False, "unchanged"

        if existing is None:
            candidate = PaperCandidate(
                signal_id=signal_id,
                symbol=symbol,
                direction=direction,
                source_style=signal.get("source_style"),
                chat_id=(
                    int(signal["chat_id"])
                    if signal.get("chat_id") is not None
                    else None
                ),
                root_message_id=(
                    int(signal["root_message_id"])
                    if signal.get("root_message_id") is not None
                    else None
                ),
                opened_at=str(signal.get("opened_at") or now),
                first_seen_at=now,
                last_seen_at=now,
                initial_snapshot=bool(initial_snapshot),
                signal_status=str(signal.get("status") or "UNKNOWN"),
                entry_low=float(signal.get("entry_low") or 0.0),
                entry_high=float(signal.get("entry_high") or signal.get("entry_low") or 0.0),
                original_sl=(
                    float(signal["original_sl"])
                    if signal.get("original_sl") is not None
                    else None
                ),
                current_sl=(
                    float(signal["current_sl"])
                    if signal.get("current_sl") is not None
                    else None
                ),
                tps={str(k): float(v) for k, v in (signal.get("tps") or {}).items()},
                remaining_fraction=float(signal.get("remaining_fraction", 1.0) or 0.0),
                partial_close_count=int(signal.get("partial_close_count", 0) or 0),
                source_message_ids=[int(v) for v in signal.get("source_message_ids") or []],
                signal_history=list(signal.get("history") or []),
                fingerprint=fingerprint,
                execution_status=(
                    "PENDING_STRATEGY"
                    if self.entry_policy == "all_parsed"
                    else "OBSERVED"
                ),
            )
            self.candidates[signal_id] = candidate
            self._audit(
                "signal_received",
                signal_id=signal_id,
                initial_snapshot=bool(initial_snapshot),
                direction=direction,
                entry_low=candidate.entry_low,
                entry_high=candidate.entry_high,
            )
            return True, "created"

        previous_entry_low = existing.entry_low
        previous_entry_high = existing.entry_high
        previous_sl = existing.current_sl
        previous_stop_was_malformed = bool(
            previous_sl is not None
            and (
                (
                    direction == "BUY"
                    and previous_sl >= min(previous_entry_low, previous_entry_high)
                )
                or (
                    direction == "SELL"
                    and previous_sl <= max(previous_entry_low, previous_entry_high)
                )
            )
        )

        existing.last_seen_at = now
        existing.signal_status = str(signal.get("status") or existing.signal_status)
        existing.entry_low = float(signal.get("entry_low") or existing.entry_low)
        existing.entry_high = float(
            signal.get("entry_high") or signal.get("entry_low") or existing.entry_high
        )
        existing.original_sl = (
            float(signal["original_sl"])
            if signal.get("original_sl") is not None
            else None
        )
        existing.current_sl = (
            float(signal["current_sl"])
            if signal.get("current_sl") is not None
            else None
        )
        existing.tps = {str(k): float(v) for k, v in (signal.get("tps") or {}).items()}
        existing.remaining_fraction = float(signal.get("remaining_fraction", 1.0) or 0.0)
        existing.partial_close_count = int(signal.get("partial_close_count", 0) or 0)
        existing.source_message_ids = [
            int(v) for v in signal.get("source_message_ids") or []
        ]
        existing.signal_history = list(signal.get("history") or [])
        existing.fingerprint = fingerprint

        # Same-message provider corrections must update the paper candidate.
        if (
            existing.execution_status == "REJECTED_INVALIDATED"
            and previous_stop_was_malformed
        ):
            existing.execution_status = "PENDING_STRATEGY"
            existing.execution_note = "Provider edited the malformed setup; re-evaluating."
        self._audit(
            "signal_updated",
            signal_id=signal_id,
            signal_status=existing.signal_status,
            remaining_fraction=existing.remaining_fraction,
        )
        return True, "updated"

    def open_position(
        self,
        *,
        signal_id: str,
        fill_price: float,
        quantity_oz: float,
        stop_loss: float | None = None,
        opened_at: str | None = None,
        initial_risk_usd: float = 0.0,
        lot_size: float = 0.0,
        tp_prices: dict[str, float] | None = None,
        tp_close_quantities_oz: dict[str, float] | None = None,
        eligible_tp_indices: list[int] | None = None,
        be_trigger_tp_index: int | None = None,
    ) -> PaperPosition:
        if self.strategy_mode == "observe_only":
            raise RuntimeError("paper strategy is observe_only; execution is disabled")
        candidate = self.candidates.get(signal_id)
        if candidate is None:
            raise KeyError(f"unknown signal_id: {signal_id}")
        if fill_price <= 0 or quantity_oz <= 0:
            raise ValueError("fill_price and quantity_oz must be positive")
        if any(
            p.signal_id == signal_id and p.status in OPEN_POSITION_STATUSES
            for p in self.positions.values()
        ):
            raise ValueError(f"signal already has an open paper position: {signal_id}")

        position_id = f"paper:{signal_id}"
        timestamp = opened_at or utc_now()
        position = PaperPosition(
            position_id=position_id,
            signal_id=signal_id,
            symbol=self.symbol,
            direction=candidate.direction,
            opened_at=timestamp,
            entry_price=float(fill_price),
            initial_quantity_oz=float(quantity_oz),
            remaining_quantity_oz=float(quantity_oz),
            stop_loss=float(stop_loss) if stop_loss is not None else None,
            initial_stop_loss=float(stop_loss) if stop_loss is not None else None,
            initial_risk_usd=float(initial_risk_usd),
            lot_size=float(lot_size),
            tp_prices=dict(tp_prices or {}),
            tp_close_quantities_oz=dict(tp_close_quantities_oz or {}),
            eligible_tp_indices=list(eligible_tp_indices or []),
            be_trigger_tp_index=be_trigger_tp_index,
            last_mark_price=self.last_mark_price,
            history=[
                {
                    "timestamp": timestamp,
                    "kind": "fill",
                    "price": float(fill_price),
                    "quantity_oz": float(quantity_oz),
                    "lot_size": float(lot_size),
                    "risk_usd": float(initial_risk_usd),
                }
            ],
        )
        self.positions[position_id] = position
        candidate.execution_status = "OPEN"
        candidate.execution_note = ""
        self._audit(
            "position_opened",
            position_id=position_id,
            signal_id=signal_id,
            fill_price=float(fill_price),
            quantity_oz=float(quantity_oz),
            lot_size=float(lot_size),
            risk_usd=float(initial_risk_usd),
        )
        self._revalue()
        return position

    def add_quantity(
        self,
        *,
        position_id: str,
        fill_price: float,
        quantity_oz: float,
        lot_size: float,
        risk_usd: float,
        reason: str,
        timestamp: str | None = None,
    ) -> None:
        position = self.positions[position_id]
        if position.status not in OPEN_POSITION_STATUSES:
            raise ValueError("paper position is not open")
        if fill_price <= 0 or quantity_oz <= 0:
            raise ValueError("fill_price and quantity_oz must be positive")

        old_remaining = position.remaining_quantity_oz
        new_remaining = old_remaining + float(quantity_oz)
        position.entry_price = (
            (position.entry_price * old_remaining)
            + (float(fill_price) * float(quantity_oz))
        ) / new_remaining
        position.remaining_quantity_oz = new_remaining
        position.initial_quantity_oz += float(quantity_oz)
        position.lot_size += float(lot_size)
        position.initial_risk_usd += float(risk_usd)
        position.history.append(
            {
                "timestamp": timestamp or utc_now(),
                "kind": "add_fill",
                "reason": reason,
                "price": float(fill_price),
                "quantity_oz": float(quantity_oz),
                "lot_size": float(lot_size),
                "risk_usd": float(risk_usd),
            }
        )
        self._audit(
            "position_layer_added",
            position_id=position_id,
            fill_price=float(fill_price),
            quantity_oz=float(quantity_oz),
            risk_usd=float(risk_usd),
            reason=reason,
        )
        self._revalue()

    def set_stop(
        self,
        *,
        position_id: str,
        stop_loss: float,
        reason: str,
        timestamp: str | None = None,
    ) -> None:
        position = self.positions[position_id]
        if position.status not in OPEN_POSITION_STATUSES:
            return
        previous = position.stop_loss
        position.stop_loss = float(stop_loss)
        position.history.append(
            {
                "timestamp": timestamp or utc_now(),
                "kind": "stop_update",
                "reason": reason,
                "before": previous,
                "after": float(stop_loss),
            }
        )
        self._audit(
            "position_stop_updated",
            position_id=position_id,
            before=previous,
            after=float(stop_loss),
            reason=reason,
        )

    def close_quantity(
        self,
        *,
        position_id: str,
        quantity_oz: float,
        fill_price: float,
        reason: str,
        closed_at: str | None = None,
    ) -> float:
        position = self.positions[position_id]
        if position.status not in OPEN_POSITION_STATUSES:
            raise ValueError("paper position is not open")
        if quantity_oz <= 0 or fill_price <= 0:
            raise ValueError("quantity_oz and fill_price must be positive")
        if quantity_oz > position.remaining_quantity_oz + 1e-9:
            raise ValueError("cannot close more than the remaining quantity")

        quantity = min(float(quantity_oz), position.remaining_quantity_oz)
        pnl_per_oz = (
            float(fill_price) - position.entry_price
            if position.direction == "BUY"
            else position.entry_price - float(fill_price)
        )
        realized = pnl_per_oz * quantity
        position.remaining_quantity_oz -= quantity
        position.realized_pnl_usd += realized
        self.realized_pnl_usd += realized
        self.balance_usd = self.starting_balance_usd + self.realized_pnl_usd

        timestamp = closed_at or utc_now()
        position.history.append(
            {
                "timestamp": timestamp,
                "kind": "close",
                "reason": reason,
                "price": float(fill_price),
                "quantity_oz": quantity,
                "realized_pnl_usd": realized,
            }
        )
        if position.remaining_quantity_oz <= 1e-9:
            position.remaining_quantity_oz = 0.0
            position.status = "CLOSED"
            position.closed_at = timestamp
            candidate = self.candidates.get(position.signal_id)
            if candidate is not None:
                candidate.execution_status = "CLOSED"

        self._audit(
            "position_reduced",
            position_id=position_id,
            fill_price=float(fill_price),
            quantity_oz=quantity,
            realized_pnl_usd=realized,
            reason=reason,
        )
        self._revalue()
        return realized

    def mark(self, price: float, *, marked_at: str | None = None) -> None:
        self.mark_quote(
            bid=float(price),
            ask=float(price),
            price=float(price),
            marked_at=marked_at,
        )

    def mark_quote(
        self,
        *,
        bid: float,
        ask: float,
        price: float | None = None,
        marked_at: str | None = None,
    ) -> None:
        if bid <= 0 or ask <= 0:
            raise ValueError("bid and ask must be positive")
        self.last_bid = float(bid)
        self.last_ask = float(ask)
        self.last_mark_price = (
            float(price) if price is not None and price > 0 else (bid + ask) / 2.0
        )
        self.last_mark_at = marked_at or utc_now()
        self._revalue()

    def current_directional_risk_usd(self, direction: str) -> float:
        direction = direction.upper()
        total = 0.0
        for position in self.positions.values():
            if position.status not in OPEN_POSITION_STATUSES:
                continue
            if position.direction.upper() != direction:
                continue
            if position.stop_loss is None:
                continue
            if direction == "BUY":
                risk_per_oz = max(0.0, position.entry_price - position.stop_loss)
            else:
                risk_per_oz = max(0.0, position.stop_loss - position.entry_price)
            total += risk_per_oz * position.remaining_quantity_oz
        return total

    def _revalue(self) -> None:
        total_unrealized = 0.0
        for position in self.positions.values():
            if (
                position.status not in OPEN_POSITION_STATUSES
                or self.last_mark_price is None
            ):
                position.unrealized_pnl_usd = 0.0
                continue

            exit_mark = (
                self.last_bid
                if position.direction == "BUY"
                else self.last_ask
            )
            if exit_mark is None:
                exit_mark = self.last_mark_price
            position.last_mark_price = exit_mark
            move = (
                exit_mark - position.entry_price
                if position.direction == "BUY"
                else position.entry_price - exit_mark
            )
            position.unrealized_pnl_usd = move * position.remaining_quantity_oz
            total_unrealized += position.unrealized_pnl_usd

        self.unrealized_pnl_usd = total_unrealized
        self.balance_usd = self.starting_balance_usd + self.realized_pnl_usd
        self.equity_usd = self.balance_usd + self.unrealized_pnl_usd

        if self.equity_usd > self.peak_equity_usd:
            self.peak_equity_usd = self.equity_usd

        current_drawdown_usd = max(0.0, self.peak_equity_usd - self.equity_usd)
        current_drawdown_pct = (
            (current_drawdown_usd / self.peak_equity_usd) * 100.0
            if self.peak_equity_usd > 0
            else 0.0
        )
        if current_drawdown_usd > self.max_drawdown_usd:
            self.max_drawdown_usd = current_drawdown_usd
        if current_drawdown_pct > self.max_drawdown_pct:
            self.max_drawdown_pct = current_drawdown_pct
        self.updated_at = utc_now()

    def snapshot(self) -> dict[str, Any]:
        self._revalue()
        return {
            "version": self.version,
            "engine": "xauusd_paper",
            "strategy_mode": self.strategy_mode,
            "strategy_activated_at": self.strategy_activated_at,
            "entry_policy": self.entry_policy,
            "symbol": self.symbol,
            "starting_balance_usd": self.starting_balance_usd,
            "balance_usd": self.balance_usd,
            "equity_usd": self.equity_usd,
            "realized_pnl_usd": self.realized_pnl_usd,
            "unrealized_pnl_usd": self.unrealized_pnl_usd,
            "peak_equity_usd": self.peak_equity_usd,
            "current_drawdown_usd": max(
                0.0,
                self.peak_equity_usd - self.equity_usd,
            ),
            "current_drawdown_pct": (
                max(0.0, self.peak_equity_usd - self.equity_usd)
                / self.peak_equity_usd
                * 100.0
                if self.peak_equity_usd > 0
                else 0.0
            ),
            "max_drawdown_usd": self.max_drawdown_usd,
            "max_drawdown_pct": self.max_drawdown_pct,
            "trading_day": self.trading_day,
            "day_start_balance_usd": self.day_start_balance_usd,
            "daily_equity_floor_usd": self.daily_equity_floor_usd,
            "daily_stop_triggered": self.daily_stop_triggered,
            "daily_stop_triggered_at": self.daily_stop_triggered_at,
            "directional_risk_usd": {
                "BUY": self.current_directional_risk_usd("BUY"),
                "SELL": self.current_directional_risk_usd("SELL"),
            },
            "last_mark_price": self.last_mark_price,
            "last_bid": self.last_bid,
            "last_ask": self.last_ask,
            "last_mark_at": self.last_mark_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "candidate_count": len(self.candidates),
            "open_position_count": sum(
                1 for p in self.positions.values() if p.status in OPEN_POSITION_STATUSES
            ),
            "candidates": [
                candidate.as_dict()
                for candidate in sorted(
                    self.candidates.values(),
                    key=lambda item: (item.opened_at, item.signal_id),
                )
            ],
            "positions": [
                position.as_dict()
                for position in sorted(
                    self.positions.values(),
                    key=lambda item: (item.opened_at, item.position_id),
                )
            ],
            "audit": list(self.audit),
        }
