from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


PAPER_ENGINE_VERSION = 1
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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PaperAccount:
    """Durable accounting core for the XAUUSD paper engine.

    Quantity is stored in troy ounces, which makes PnL broker-independent:
    for XAUUSD a $1 move produces $1 PnL per ounce. A future execution
    strategy can convert lots/contracts into ounces explicitly.
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
        self.starting_balance_usd = float(starting_balance_usd)
        self.balance_usd = float(starting_balance_usd)
        self.equity_usd = float(starting_balance_usd)
        self.realized_pnl_usd = 0.0
        self.unrealized_pnl_usd = 0.0
        self.peak_equity_usd = float(starting_balance_usd)
        self.max_drawdown_usd = 0.0
        self.max_drawdown_pct = 0.0
        self.last_mark_price: float | None = None
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
        account.last_mark_price = (
            float(payload["last_mark_price"])
            if payload.get("last_mark_price") is not None
            else None
        )
        account.last_mark_at = payload.get("last_mark_at")
        account.created_at = str(payload.get("created_at") or account.created_at)
        account.updated_at = str(payload.get("updated_at") or account.updated_at)
        account.audit = list(payload.get("audit") or [])

        for raw in payload.get("candidates") or []:
            candidate = PaperCandidate(**raw)
            account.candidates[candidate.signal_id] = candidate

        for raw in payload.get("positions") or []:
            position = PaperPosition(**raw)
            account.positions[position.position_id] = position

        account._revalue()
        return account

    def _audit(self, kind: str, **data: Any) -> None:
        self.audit.append({"timestamp": utc_now(), "kind": kind, **data})
        # Keep the snapshot bounded. The full service event log remains durable.
        self.audit = self.audit[-500:]
        self.updated_at = utc_now()

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

        existing.last_seen_at = now
        existing.signal_status = str(signal.get("status") or existing.signal_status)
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
            last_mark_price=self.last_mark_price,
            history=[
                {
                    "timestamp": timestamp,
                    "kind": "fill",
                    "price": float(fill_price),
                    "quantity_oz": float(quantity_oz),
                }
            ],
        )
        self.positions[position_id] = position
        candidate.execution_status = "OPEN"
        self._audit(
            "position_opened",
            position_id=position_id,
            signal_id=signal_id,
            fill_price=float(fill_price),
            quantity_oz=float(quantity_oz),
        )
        self._revalue()
        return position

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
        if price <= 0:
            raise ValueError("mark price must be positive")
        self.last_mark_price = float(price)
        self.last_mark_at = marked_at or utc_now()
        self._revalue()

    def _revalue(self) -> None:
        total_unrealized = 0.0
        for position in self.positions.values():
            if (
                position.status not in OPEN_POSITION_STATUSES
                or self.last_mark_price is None
            ):
                position.unrealized_pnl_usd = 0.0
                continue

            position.last_mark_price = self.last_mark_price
            move = (
                self.last_mark_price - position.entry_price
                if position.direction == "BUY"
                else position.entry_price - self.last_mark_price
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
            "last_mark_price": self.last_mark_price,
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
