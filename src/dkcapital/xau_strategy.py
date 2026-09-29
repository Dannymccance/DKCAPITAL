from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from dkcapital.paper_trading import PaperAccount, PaperCandidate, PaperPosition, utc_now


@dataclass(frozen=True)
class XauStrategyConfig:
    risk_pct: float = 0.005
    direction_risk_cap_pct: float = 0.008
    min_trade_risk_pct: float = 0.001
    daily_loss_pct: float = 0.02
    contract_oz_per_lot: float = 100.0
    lot_step: float = 0.01
    timezone_name: str = "Europe/Isle_of_Man"


class XauSignalFollowingStrategy:
    """Signal-following XAUUSD execution with a deterministic risk overlay."""

    BASE_TP_WEIGHTS = {1: 0.30, 2: 0.30, 3: 0.40}

    def __init__(self, config: XauStrategyConfig) -> None:
        self.config = config

    @staticmethod
    def _event_fingerprint(item: dict[str, Any]) -> str:
        raw = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _iso(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return None

    def _local_date(self, timestamp: str | None = None) -> str:
        parsed = self._iso(timestamp) or datetime.now(UTC)
        return parsed.astimezone(ZoneInfo(self.config.timezone_name)).date().isoformat()

    @staticmethod
    def _quote_prices(quote: dict[str, Any]) -> tuple[float, float, float]:
        price = float(quote.get("price") or 0.0)
        bid = float(quote.get("bid") or price or 0.0)
        ask = float(quote.get("ask") or price or 0.0)
        if price <= 0:
            price = (bid + ask) / 2.0
        if bid <= 0 or ask <= 0 or price <= 0:
            raise ValueError("quote must contain a positive price/bid/ask")
        return bid, ask, price

    @staticmethod
    def _entry_fill(direction: str, quote: dict[str, Any]) -> float:
        bid, ask, _ = XauSignalFollowingStrategy._quote_prices(quote)
        return ask if direction == "BUY" else bid

    @staticmethod
    def _exit_fill(direction: str, quote: dict[str, Any]) -> float:
        bid, ask, _ = XauSignalFollowingStrategy._quote_prices(quote)
        return bid if direction == "BUY" else ask

    @staticmethod
    def _event_quote(
        item: dict[str, Any],
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        market = item.get("market")
        return market if isinstance(market, dict) and market.get("price") else fallback

    def _risk_budget(
        self,
        account: PaperAccount,
        direction: str,
    ) -> tuple[float, str]:
        standard = account.balance_usd * self.config.risk_pct
        cap = account.balance_usd * self.config.direction_risk_cap_pct
        used = account.current_directional_risk_usd(direction)
        available = max(0.0, cap - used)
        budget = min(standard, available)
        minimum = account.balance_usd * self.config.min_trade_risk_pct
        if budget + 1e-9 < minimum:
            return 0.0, "directional_risk_capacity_below_minimum"
        return budget, "ok"

    def _size_from_stop(
        self,
        *,
        risk_budget_usd: float,
        fill_price: float,
        stop_loss: float,
    ) -> tuple[float, float, float]:
        stop_distance = abs(fill_price - stop_loss)
        if stop_distance <= 0:
            return 0.0, 0.0, 0.0

        raw_oz = risk_budget_usd / stop_distance
        raw_lots = raw_oz / self.config.contract_oz_per_lot
        stepped_lots = (
            math.floor((raw_lots + 1e-12) / self.config.lot_step)
            * self.config.lot_step
        )
        if stepped_lots <= 0:
            return 0.0, 0.0, 0.0

        quantity_oz = stepped_lots * self.config.contract_oz_per_lot
        actual_risk = quantity_oz * stop_distance
        return stepped_lots, quantity_oz, actual_risk

    @staticmethod
    def _stop_invalid(direction: str, fill_price: float, stop_loss: float) -> bool:
        if direction == "BUY":
            return fill_price <= stop_loss
        return fill_price >= stop_loss

    @staticmethod
    def _future_tp_indices(
        direction: str,
        fill_price: float,
        tps: dict[str, float],
    ) -> list[int]:
        eligible: list[int] = []
        for raw_index, raw_price in tps.items():
            try:
                index = int(raw_index)
                price = float(raw_price)
            except (TypeError, ValueError):
                continue
            if direction == "BUY" and price > fill_price:
                eligible.append(index)
            elif direction == "SELL" and price < fill_price:
                eligible.append(index)
        return sorted(set(eligible))

    def _weights_for(self, indices: list[int]) -> dict[int, float]:
        if not indices:
            return {}
        raw: dict[int, float] = {}
        for index in indices:
            if index in self.BASE_TP_WEIGHTS:
                raw[index] = self.BASE_TP_WEIGHTS[index]
            elif index > 3:
                raw[index] = 0.40
            else:
                raw[index] = 0.0

        total = sum(raw.values())
        if total <= 0:
            equal = 1.0 / len(indices)
            return {index: equal for index in indices}
        return {index: value / total for index, value in raw.items()}

    def _build_tp_schedule(
        self,
        *,
        position: PaperPosition,
        candidate: PaperCandidate,
        indices: list[int] | None = None,
    ) -> None:
        available = [
            index
            for index in (indices if indices is not None else position.eligible_tp_indices)
            if index not in position.tp_hits
            and str(index) in candidate.tps
        ]
        available = sorted(available)
        known = sorted(set(position.tp_hits).union(available))
        position.eligible_tp_indices = known
        position.tp_prices = {
            str(index): float(candidate.tps[str(index)])
            for index in available
            if str(index) in candidate.tps
        }
        weights = self._weights_for(available)
        position.tp_close_quantities_oz = {
            str(index): position.remaining_quantity_oz * weights[index]
            for index in available
        }
        if available and position.be_trigger_tp_index is None:
            position.be_trigger_tp_index = available[0]

    def _mark_existing_history_processed(self, candidate: PaperCandidate) -> None:
        fingerprints = [
            self._event_fingerprint(item)
            for item in candidate.signal_history
        ]
        candidate.processed_history_fingerprints = list(dict.fromkeys(fingerprints))[-500:]

    def _cancel_older_pending(
        self,
        account: PaperAccount,
        new_candidate: PaperCandidate,
    ) -> None:
        new_opened = self._iso(new_candidate.opened_at)
        if new_opened is None:
            return
        for candidate in account.candidates.values():
            if candidate.signal_id == new_candidate.signal_id:
                continue
            if candidate.execution_status not in {"PENDING_SL", "PENDING_STRATEGY"}:
                continue
            opened = self._iso(candidate.opened_at)
            if opened is None or opened >= new_opened:
                continue
            candidate.execution_status = "CANCELLED_BY_NEW_SIGNAL"
            candidate.execution_note = (
                f"Pending unfilled setup cancelled by newer XAUUSD signal "
                f"{new_candidate.signal_id}"
            )
            account._audit(
                "pending_signal_cancelled",
                signal_id=candidate.signal_id,
                replacement_signal_id=new_candidate.signal_id,
            )

    def _reject(
        self,
        account: PaperAccount,
        candidate: PaperCandidate,
        status: str,
        note: str,
    ) -> None:
        candidate.execution_status = status
        candidate.execution_note = note
        account._audit(
            "paper_entry_rejected",
            signal_id=candidate.signal_id,
            status=status,
            note=note,
        )

    @staticmethod
    def _position_for(
        account: PaperAccount,
        signal_id: str,
    ) -> PaperPosition | None:
        for position in account.positions.values():
            if position.signal_id == signal_id and position.status == "OPEN":
                return position
        return None

    def _enter_candidate(
        self,
        account: PaperAccount,
        candidate: PaperCandidate,
        quote: dict[str, Any],
        *,
        timestamp: str,
    ) -> bool:
        if candidate.signal_status != "ACTIVE":
            self._reject(
                account,
                candidate,
                "NOT_ENTERED_SIGNAL_INACTIVE",
                f"Signal status is {candidate.signal_status}.",
            )
            return False

        if candidate.current_sl is None:
            candidate.execution_status = "PENDING_SL"
            candidate.execution_note = "Waiting for provider structural stop loss."
            return False

        if account.daily_stop_triggered:
            self._reject(
                account,
                candidate,
                "SKIPPED_DAILY_STOP",
                "Daily equity loss stop already triggered.",
            )
            return False

        direction = candidate.direction
        fill = self._entry_fill(direction, quote)
        stop = float(candidate.current_sl)

        if self._stop_invalid(direction, fill, stop):
            self._reject(
                account,
                candidate,
                "REJECTED_INVALIDATED",
                "Live market has already traded beyond the provider structural stop.",
            )
            return False

        all_tps = dict(candidate.tps)
        eligible_tps = self._future_tp_indices(direction, fill, all_tps)
        if all_tps and not eligible_tps:
            self._reject(
                account,
                candidate,
                "REJECTED_TARGETS_ALREADY_PASSED",
                "All explicit provider targets were already behind the live fill.",
            )
            return False

        budget, reason = self._risk_budget(account, direction)
        if budget <= 0:
            self._reject(
                account,
                candidate,
                "REJECTED_DIRECTIONAL_RISK_CAP",
                reason,
            )
            return False

        lots, quantity_oz, actual_risk = self._size_from_stop(
            risk_budget_usd=budget,
            fill_price=fill,
            stop_loss=stop,
        )
        if lots <= 0 or quantity_oz <= 0:
            self._reject(
                account,
                candidate,
                "REJECTED_SIZE_TOO_SMALL",
                "Risk budget is too small for the configured lot step.",
            )
            return False

        position = account.open_position(
            signal_id=candidate.signal_id,
            fill_price=fill,
            quantity_oz=quantity_oz,
            stop_loss=stop,
            opened_at=timestamp,
            initial_risk_usd=actual_risk,
            lot_size=lots,
            eligible_tp_indices=eligible_tps,
            be_trigger_tp_index=(eligible_tps[0] if eligible_tps else None),
        )
        self._build_tp_schedule(
            position=position,
            candidate=candidate,
            indices=eligible_tps,
        )
        self._mark_existing_history_processed(candidate)
        candidate.execution_status = "OPEN"
        candidate.execution_note = (
            f"Market filled {lots:.2f} lots at {fill:.2f}; "
            f"initial risk ${actual_risk:,.2f}."
        )
        return True

    def _provider_stop(
        self,
        account: PaperAccount,
        position: PaperPosition,
        requested_stop: float,
        *,
        timestamp: str,
        reason: str,
    ) -> None:
        stop = float(requested_stop)
        if position.breakeven_locked:
            if position.direction == "BUY":
                stop = max(stop, position.entry_price)
            else:
                stop = min(stop, position.entry_price)
        account.set_stop(
            position_id=position.position_id,
            stop_loss=stop,
            reason=reason,
            timestamp=timestamp,
        )

    def _rebuild_remaining_targets(
        self,
        position: PaperPosition,
        candidate: PaperCandidate,
    ) -> None:
        remaining_indices = [
            index
            for index in sorted(position.eligible_tp_indices)
            if index not in position.tp_hits
        ]
        self._build_tp_schedule(
            position=position,
            candidate=candidate,
            indices=remaining_indices,
        )

    def _handle_tp(
        self,
        account: PaperAccount,
        candidate: PaperCandidate,
        position: PaperPosition,
        tp_index: int,
        quote: dict[str, Any],
        *,
        timestamp: str,
        reason_prefix: str,
    ) -> bool:
        if position.status != "OPEN" or tp_index in position.tp_hits:
            return False
        if tp_index not in position.eligible_tp_indices:
            return False

        remaining_indices = [
            idx
            for idx in sorted(position.eligible_tp_indices)
            if idx not in position.tp_hits
        ]
        if tp_index not in remaining_indices:
            return False

        exit_price = self._exit_fill(position.direction, quote)
        is_last = tp_index == remaining_indices[-1]
        quantity = (
            position.remaining_quantity_oz
            if is_last
            else min(
                position.remaining_quantity_oz,
                float(position.tp_close_quantities_oz.get(str(tp_index), 0.0)),
            )
        )
        if quantity <= 0:
            position.tp_hits.append(tp_index)
            position.tp_hits.sort()
            return False

        account.close_quantity(
            position_id=position.position_id,
            quantity_oz=quantity,
            fill_price=exit_price,
            reason=f"{reason_prefix}_tp{tp_index}",
            closed_at=timestamp,
        )
        position.tp_hits.append(tp_index)
        position.tp_hits.sort()

        if (
            position.status == "OPEN"
            and position.be_trigger_tp_index is not None
            and tp_index == position.be_trigger_tp_index
        ):
            position.breakeven_locked = True
            account.set_stop(
                position_id=position.position_id,
                stop_loss=position.entry_price,
                reason=f"{reason_prefix}_tp{tp_index}_breakeven",
                timestamp=timestamp,
            )
        return True

    def _try_layer(
        self,
        account: PaperAccount,
        candidate: PaperCandidate,
        position: PaperPosition,
        quote: dict[str, Any],
        *,
        timestamp: str,
        reason: str,
    ) -> bool:
        if account.daily_stop_triggered or position.stop_loss is None:
            return False

        fill = self._entry_fill(position.direction, quote)
        stop = float(position.stop_loss)
        if self._stop_invalid(position.direction, fill, stop):
            return False

        budget, _ = self._risk_budget(account, position.direction)
        if budget <= 0:
            return False

        lots, quantity_oz, actual_risk = self._size_from_stop(
            risk_budget_usd=budget,
            fill_price=fill,
            stop_loss=stop,
        )
        if lots <= 0:
            return False

        account.add_quantity(
            position_id=position.position_id,
            fill_price=fill,
            quantity_oz=quantity_oz,
            lot_size=lots,
            risk_usd=actual_risk,
            reason=reason,
            timestamp=timestamp,
        )
        if position.breakeven_locked:
            account.set_stop(
                position_id=position.position_id,
                stop_loss=position.entry_price,
                reason="maintain_weighted_breakeven_after_layer",
                timestamp=timestamp,
            )
        self._rebuild_remaining_targets(position, candidate)
        return True

    def _process_provider_history(
        self,
        account: PaperAccount,
        candidate: PaperCandidate,
        position: PaperPosition,
        quote: dict[str, Any],
    ) -> bool:
        changed = False
        processed = set(candidate.processed_history_fingerprints)

        for item in candidate.signal_history:
            fingerprint = self._event_fingerprint(item)
            if fingerprint in processed:
                continue

            kind = str(item.get("kind") or "")
            timestamp = str(item.get("timestamp") or utc_now())
            event_quote = self._event_quote(item, quote)

            if position.status != "OPEN":
                processed.add(fingerprint)
                continue

            if kind == "partial_close":
                pct = float(item.get("partial_percent") or 0.0)
                fraction = max(0.0, min(1.0, pct / 100.0))
                quantity = position.remaining_quantity_oz * fraction
                if quantity > 0:
                    account.close_quantity(
                        position_id=position.position_id,
                        quantity_oz=quantity,
                        fill_price=self._exit_fill(position.direction, event_quote),
                        reason="provider_partial",
                        closed_at=timestamp,
                    )
                    changed = True
                    if position.status == "OPEN":
                        self._rebuild_remaining_targets(position, candidate)

            elif kind == "trade_update":
                if item.get("move_sl_to_be"):
                    position.breakeven_locked = True
                    account.set_stop(
                        position_id=position.position_id,
                        stop_loss=position.entry_price,
                        reason="provider_breakeven",
                        timestamp=timestamp,
                    )
                    changed = True
                elif item.get("sl_after") is not None:
                    self._provider_stop(
                        account,
                        position,
                        float(item["sl_after"]),
                        timestamp=timestamp,
                        reason="provider_sl_update",
                    )
                    changed = True

                if item.get("tps"):
                    candidate.tps = {
                        str(k): float(v)
                        for k, v in dict(item["tps"]).items()
                    }
                    current_exit = self._exit_fill(position.direction, event_quote)
                    future = self._future_tp_indices(
                        position.direction,
                        current_exit,
                        candidate.tps,
                    )
                    position.eligible_tp_indices = sorted(
                        set(position.tp_hits).union(future)
                    )
                    if not position.tp_hits and future:
                        position.be_trigger_tp_index = future[0]
                    self._build_tp_schedule(
                        position=position,
                        candidate=candidate,
                        indices=future,
                    )
                    changed = True

                for raw_tp in item.get("tp_hits") or []:
                    try:
                        tp_index = int(raw_tp)
                    except (TypeError, ValueError):
                        continue
                    changed = self._handle_tp(
                        account,
                        candidate,
                        position,
                        tp_index,
                        event_quote,
                        timestamp=timestamp,
                        reason_prefix="provider",
                    ) or changed

            elif kind in {"close", "cancel", "stop_loss", "setup_failed", "breakeven_close"}:
                if position.remaining_quantity_oz > 0:
                    account.close_quantity(
                        position_id=position.position_id,
                        quantity_oz=position.remaining_quantity_oz,
                        fill_price=self._exit_fill(position.direction, event_quote),
                        reason=f"provider_{kind}",
                        closed_at=timestamp,
                    )
                    changed = True

            elif kind in {"add_layer", "reenter"}:
                changed = self._try_layer(
                    account,
                    candidate,
                    position,
                    event_quote,
                    timestamp=timestamp,
                    reason=f"provider_{kind}",
                ) or changed

            processed.add(fingerprint)

        candidate.processed_history_fingerprints = list(processed)[-500:]
        return changed

    def _process_market_management(
        self,
        account: PaperAccount,
        candidate: PaperCandidate,
        position: PaperPosition,
        quote: dict[str, Any],
        *,
        timestamp: str,
    ) -> bool:
        if position.status != "OPEN":
            return False

        changed = False
        exit_price = self._exit_fill(position.direction, quote)

        if position.stop_loss is not None:
            stop_hit = (
                exit_price <= position.stop_loss
                if position.direction == "BUY"
                else exit_price >= position.stop_loss
            )
            if stop_hit:
                account.close_quantity(
                    position_id=position.position_id,
                    quantity_oz=position.remaining_quantity_oz,
                    fill_price=exit_price,
                    reason="strategy_stop",
                    closed_at=timestamp,
                )
                return True

        for tp_index in sorted(position.eligible_tp_indices):
            if position.status != "OPEN":
                break
            if tp_index in position.tp_hits:
                continue
            target = position.tp_prices.get(str(tp_index))
            if target is None:
                target = candidate.tps.get(str(tp_index))
            if target is None:
                continue
            hit = (
                exit_price >= float(target)
                if position.direction == "BUY"
                else exit_price <= float(target)
            )
            if not hit:
                continue
            changed = self._handle_tp(
                account,
                candidate,
                position,
                tp_index,
                quote,
                timestamp=timestamp,
                reason_prefix="strategy",
            ) or changed

        return changed

    def process(
        self,
        account: PaperAccount,
        quote: dict[str, Any],
        *,
        timestamp: str | None = None,
    ) -> bool:
        timestamp = timestamp or str(quote.get("computed_at") or utc_now())
        bid, ask, price = self._quote_prices(quote)
        account.mark_quote(
            bid=bid,
            ask=ask,
            price=price,
            marked_at=timestamp,
        )
        account.ensure_trading_day(
            self._local_date(timestamp),
            daily_loss_pct=self.config.daily_loss_pct,
        )
        daily_stop_before = account.daily_stop_triggered
        account.check_daily_stop(timestamp=timestamp)
        changed = account.daily_stop_triggered != daily_stop_before

        activated = self._iso(account.strategy_activated_at)
        candidates = sorted(
            account.candidates.values(),
            key=lambda item: (item.opened_at, item.signal_id),
        )

        for candidate in candidates:
            opened = self._iso(candidate.opened_at)

            if (
                activated is not None
                and opened is not None
                and opened < activated
                and candidate.execution_status in {"PENDING_STRATEGY", "PENDING_SL"}
            ):
                candidate.execution_status = "SKIPPED_PRE_STRATEGY"
                candidate.execution_note = "Signal predates paper-strategy activation."
                changed = True
                continue

            if candidate.execution_status in {"PENDING_STRATEGY", "PENDING_SL"}:
                self._cancel_older_pending(account, candidate)

            position = self._position_for(account, candidate.signal_id)
            if position is None:
                if candidate.execution_status in {"PENDING_STRATEGY", "PENDING_SL"}:
                    changed = self._enter_candidate(
                        account,
                        candidate,
                        quote,
                        timestamp=timestamp,
                    ) or changed
                    position = self._position_for(account, candidate.signal_id)
            else:
                candidate.execution_status = "OPEN"

            if position is None:
                continue

            changed = self._process_provider_history(
                account,
                candidate,
                position,
                quote,
            ) or changed

            if position.status == "OPEN":
                changed = self._process_market_management(
                    account,
                    candidate,
                    position,
                    quote,
                    timestamp=timestamp,
                ) or changed

        daily_stop_before = account.daily_stop_triggered
        account.check_daily_stop(timestamp=timestamp)
        if account.daily_stop_triggered != daily_stop_before:
            changed = True
        return changed
