"""Independent experimental MT5 V2.07-style PAPER simulator.

No broker execution, no reused historical v1 ledger, and no fictitious fills
from Telegram TP/SL result posts. Only observed delayed bid/ask tape can fill.
The model intentionally omits broker margin, swaps and intratick movement.
"""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from dkcapital.paper_trading import PaperAccount, PaperCandidate, PaperPosition, utc_now
from dkcapital.xau_strategy import XauSignalFollowingStrategy


STORM_CHAT_ID = -1004334637102


class V2PaperStrategy(XauSignalFollowingStrategy):
    """Conservative, separately persisted V2 paper execution experiment."""

    def _is_storm(self, candidate: PaperCandidate) -> bool:
        return candidate.chat_id == STORM_CHAT_ID

    def _risk_at_stop(self, position: PaperPosition) -> float:
        if position.status != "OPEN" or position.stop_loss is None:
            return 0.0
        distance = (
            position.entry_price - position.stop_loss
            if position.direction == "BUY"
            else position.stop_loss - position.entry_price
        )
        return max(0.0, distance) * position.remaining_quantity_oz

    def _portfolio_risk(self, account: PaperAccount) -> float:
        return sum(self._risk_at_stop(p) for p in account.positions.values())

    def _signal_risk(self, account: PaperAccount, candidate: PaperCandidate) -> float:
        return sum(
            self._risk_at_stop(p)
            for p in account.positions.values()
            if p.signal_id == candidate.signal_id
        )

    def _entry_budget(self, account: PaperAccount) -> float:
        reference = min(account.balance_usd, account.equity_usd)
        # Demo GBP policy amounts are *illustratively scaled in USD* for the
        # independent $100k simulator; this is not a broker currency conversion.
        return max(0.0, min(
            0.04 * reference,
            0.85 * (75.0 * reference / 1000.0),
            (150.0 * reference / 1000.0) - self._portfolio_risk(account),
        ))

    def _entry_window(self, candidate: PaperCandidate) -> int:
        return 300 if self._is_storm(candidate) else 180

    def _freshly_armed(self, candidate: PaperCandidate) -> bool:
        if not self._is_storm(candidate):
            return True
        message = self._iso(candidate.opened_at)
        observed = self._iso(candidate.first_seen_at)
        return bool(
            message is not None and observed is not None
            and 0 <= (observed - message).total_seconds() <= 45
        )

    def _enter_candidate(
        self,
        account: PaperAccount,
        candidate: PaperCandidate,
        quote: dict,
        *,
        timestamp: str,
    ) -> bool:
        if candidate.signal_status != "ACTIVE":
            self._reject(account, candidate, "V2_INACTIVE", "Provider signal not active.")
            return False

        when = self._iso(timestamp)
        opened = self._iso(candidate.opened_at)
        seen = self._iso(candidate.first_seen_at)
        if when is None or opened is None or seen is None or when < seen:
            return False
        age = (when - opened).total_seconds()
        if age < 0:
            return False
        if not self._freshly_armed(candidate):
            self._reject(account, candidate, "V2_STALE_ON_RECEIPT", "STORM arrived too late to arm.")
            return False
        if age > self._entry_window(candidate):
            self._reject(account, candidate, "V2_ZONE_TIMEOUT", "Entry zone never reached in time.")
            return False

        bid, ask, _ = self._quote_prices(quote)
        if ask < bid or ask - bid > 1.5:
            return False
        fill = ask if candidate.direction == "BUY" else bid
        low, high = sorted((candidate.entry_low, candidate.entry_high))
        tolerance = 0.0 if self._is_storm(candidate) else 1.0
        if not low - tolerance <= fill <= high + tolerance:
            candidate.execution_status = "PENDING_STRATEGY"
            candidate.execution_note = "Armed: waiting for actual executable price in provider zone."
            return False

        stop = candidate.current_sl
        if stop is None or self._structural_stop_wrong_side(candidate) or self._stop_invalid(candidate.direction, fill, float(stop)):
            self._reject(account, candidate, "V2_PROVIDER_SL_INVALID", "Missing/invalid provider SL; no invented stop.")
            return False

        tps = candidate.tps
        require = (1, 2) if candidate.tp3_open else (1, 2, 3)
        if not all(str(i) in tps for i in require):
            self._reject(account, candidate, "V2_REQUIRED_TP_MISSING", "Required native TP missing.")
            return False
        values = [float(tps[str(i)]) for i in require]
        ordered = (
            all(p > fill for p in values) and all(a < b for a, b in zip(values, values[1:]))
            if candidate.direction == "BUY"
            else all(p < fill for p in values) and all(a > b for a, b in zip(values, values[1:]))
        )
        if not ordered:
            self._reject(account, candidate, "V2_REQUIRED_TP_INVALID", "TP behind executable quote or out of order.")
            return False

        budget = self._entry_budget(account)
        reference = min(account.balance_usd, account.equity_usd)
        target_lots = math.floor(account.balance_usd / 100.0) * 0.01
        lots = math.floor(min(
            target_lots,
            budget / ((abs(fill - float(stop)) + 0.25) * self.config.contract_oz_per_lot),
        ) / self.config.lot_step + 1e-9) * self.config.lot_step
        # Three separate 0.01 minimum-lot equivalents are required.
        if lots < 3 * self.config.lot_step - 1e-9:
            self._reject(account, candidate, "V2_MIN_THREE_LEG_RISK", "No safe three-leg volume at provider SL.")
            return False

        steps = int(round(lots / self.config.lot_step))
        leg1, leg2 = math.floor(steps * 0.3), math.floor(steps * 0.3)
        leg3 = steps - leg1 - leg2
        if min(leg1, leg2, leg3) < 1:
            self._reject(account, candidate, "V2_MIN_THREE_LEG_RISK", "Too few lots for three positive legs.")
            return False
        oz_step = self.config.lot_step * self.config.contract_oz_per_lot
        quantity = (leg1 + leg2 + leg3) * oz_step
        actual_risk = quantity * abs(fill - float(stop))

        position = account.open_position(
            signal_id=candidate.signal_id,
            fill_price=fill,
            quantity_oz=quantity,
            stop_loss=float(stop),
            opened_at=timestamp,
            initial_risk_usd=actual_risk,
            lot_size=lots,
            tp_prices={
                "1": float(tps["2"]) if self._is_storm(candidate) else float(tps["1"]),
                "2": float(tps["2"]),
                **({"3": float(tps["3"])} if not candidate.tp3_open else {}),
            },
            tp_close_quantities_oz={"1": leg1 * oz_step, "2": leg2 * oz_step, "3": leg3 * oz_step},
            eligible_tp_indices=[1, 2, 3],
            be_trigger_tp_index=1,
            stop_source="PROVIDER",
        )
        position.v2_storm_policy = self._is_storm(candidate)
        position.v2_open_runner = candidate.tp3_open
        position.v2_original_lots = lots
        self._mark_existing_history_processed(candidate)
        candidate.execution_note = f"V2 paper fill {lots:.2f} lots at {fill:.2f}; provider SL {float(stop):.2f}."
        account._audit("v2_entry", signal_id=candidate.signal_id, lots=lots,
                       fill_price=fill, provider_sl=float(stop), tp3_open=candidate.tp3_open)
        return True

    def _close_target(self, account: PaperAccount, candidate: PaperCandidate,
                      position: PaperPosition, number: int, quote: dict,
                      timestamp: str) -> bool:
        if number in position.tp_hits or position.status != "OPEN":
            return False
        if number == 3 and position.v2_open_runner:
            return False
        target = position.tp_prices.get(str(number))
        if target is None:
            return False
        fill = self._exit_fill(position.direction, quote)
        hit = fill >= target if position.direction == "BUY" else fill <= target
        if not hit:
            return False
        qty = min(position.remaining_quantity_oz, position.tp_close_quantities_oz.get(str(number), 0.0))
        if qty <= 0:
            return False
        account.close_quantity(
            position_id=position.position_id, quantity_oz=qty,
            fill_price=fill, reason=f"v2_quote_tp{number}", closed_at=timestamp,
        )
        position.tp_hits.append(number)
        if number == 1 and position.status == "OPEN" and not candidate.be_deferred:
            position.breakeven_locked = True
            account.set_stop(
                position_id=position.position_id, stop_loss=position.entry_price,
                reason="v2_tp1_simulated_fill_breakeven", timestamp=timestamp,
                stop_source="BREAKEVEN",
            )
        return True

    def _try_layer_v2(self, account: PaperAccount, candidate: PaperCandidate,
                      position: PaperPosition, quote: dict, timestamp: str,
                      message: str, boundary: float | None) -> bool:
        # A report like '+LAYERS', a permission or a re-entry is NOT a
        # time-specific order. Only unambiguous explicit addition instructions.
        explicit = bool(re.search(
            r"\b(?:ADDING\s+NOW|ADD\s+NOW|LAYER\s+NOW|ADDS\s+NOW)\b",
            message.upper(),
        ))
        if not explicit or position.v2_layer_attempted or position.tp_hits:
            return False
        if any(p.v2_layer_attempted and p.status == "OPEN" and
               account.candidates.get(p.signal_id) is not None and
               account.candidates[p.signal_id].group_id == candidate.group_id
               for p in account.positions.values() if p.signal_id != candidate.signal_id):
            return False
        # Do not allow repeated attempts after failed broker-equivalent sizing.
        position.v2_layer_attempted = True
        fill = self._entry_fill(position.direction, quote)
        better = fill < position.entry_price if position.direction == "BUY" else fill > position.entry_price
        boundary_ok = (boundary is None or
                       (fill <= boundary if position.direction == "BUY" else fill >= boundary))
        if not better or not boundary_ok or position.stop_loss is None or self._stop_invalid(
            position.direction, fill, position.stop_loss
        ):
            return False
        step = self.config.lot_step
        original = position.v2_original_lots or position.lot_size
        layer_lots = math.floor(original * 0.1 / step + 1e-9) * step
        if layer_lots < step:
            return False
        risk = layer_lots * self.config.contract_oz_per_lot * (abs(fill - position.stop_loss) + 0.25)
        ref = min(account.balance_usd, account.equity_usd)
        if (self._signal_risk(account, candidate) + risk > ref * 0.04 + 1e-8 or
            self._portfolio_risk(account) + risk > 150 * ref / 1000.0 + 1e-8):
            return False
        account.add_quantity(
            position_id=position.position_id, fill_price=fill,
            quantity_oz=layer_lots * self.config.contract_oz_per_lot,
            lot_size=layer_lots, risk_usd=risk,
            reason="v2_explicit_provider_layer", timestamp=timestamp,
        )
        # This aggregate ledger does NOT perfectly model MT5's separate
        # fourth leg/TP2; report layer fills separately in audit, never claim
        # broker equivalence.
        account._audit("v2_layer_approximation", signal_id=candidate.signal_id,
                       fill=fill, lots=layer_lots, tp2=candidate.tps.get("2"))
        return True

    def _process_provider_history(self, account: PaperAccount,
                                  candidate: PaperCandidate,
                                  position: PaperPosition, quote: dict) -> bool:
        changed = False
        processed = set(candidate.processed_history_fingerprints)
        for item in candidate.signal_history:
            fp = self._event_fingerprint(item)
            if fp in processed:
                continue
            processed.add(fp)
            if position.status != "OPEN":
                continue
            kind = str(item.get("kind") or "")
            timestamp = str(item.get("timestamp") or utc_now())
            # An explicit numerical close/partial is simulated at the delayed
            # current quote, NEVER inferred from provider TP/SL result posts.
            if kind == "close":
                account.close_quantity(
                    position_id=position.position_id, quantity_oz=position.remaining_quantity_oz,
                    fill_price=self._exit_fill(position.direction, quote),
                    reason="v2_explicit_provider_close", closed_at=timestamp,
                )
                changed = True
            elif kind == "partial_close":
                percent = float(item.get("partial_percent") or 0)
                if 0 < percent <= 100:
                    account.close_quantity(
                        position_id=position.position_id,
                        quantity_oz=position.remaining_quantity_oz * percent / 100,
                        fill_price=self._exit_fill(position.direction, quote),
                        reason="v2_explicit_provider_partial", closed_at=timestamp,
                    )
                    changed = True
            elif kind == "trade_update":
                if item.get("move_sl_to_be"):
                    position.breakeven_locked = True
                    changed = self._apply_protective_stop(
                        account, position, position.entry_price, quote,
                        timestamp=timestamp, reason="v2_provider_breakeven",
                        stop_source="BREAKEVEN", temporary=False,
                    ) or changed
                elif item.get("sl_after") is not None:
                    changed = self._provider_stop(
                        account, position, float(item["sl_after"]), quote,
                        timestamp=timestamp, reason="v2_provider_stop",
                    ) or changed
                # A numeric amendment to TP2/TP3 changes a simulated
                # unfilled target; TP1 is never treated as a broker fill.
                for index, raw_price in dict(item.get("tps") or {}).items():
                    if str(index) in position.tp_prices and int(index) not in position.tp_hits:
                        price = float(raw_price)
                        if ((position.direction == "BUY" and price > self._exit_fill(position.direction, quote)) or
                            (position.direction == "SELL" and price < self._exit_fill(position.direction, quote))):
                            position.tp_prices[str(index)] = price
                            changed = True
                if item.get("tp3_open") and 3 not in position.tp_hits:
                    position.v2_open_runner = True
                    position.tp_prices.pop("3", None)
                    changed = True
            elif kind == "add_layer":
                changed = self._try_layer_v2(
                    account, candidate, position, quote, timestamp,
                    str(item.get("raw_text") or ""),
                    float(item["layer_max"]) if item.get("layer_max") is not None else None,
                ) or changed
            # provider result notifications are *not* simulated fills
        candidate.processed_history_fingerprints = list(processed)[-500:]
        return changed

    def _process_market_management(self, account: PaperAccount,
                                   candidate: PaperCandidate,
                                   position: PaperPosition, quote: dict,
                                   *, timestamp: str) -> bool:
        if position.status != "OPEN":
            return False
        fill = self._exit_fill(position.direction, quote)
        stop = position.stop_loss
        if stop is not None and (
            fill <= stop if position.direction == "BUY" else fill >= stop
        ):
            account.close_quantity(
                position_id=position.position_id, quantity_oz=position.remaining_quantity_oz,
                fill_price=fill, reason="v2_quote_stop", closed_at=timestamp,
            )
            return True
        if position.v2_storm_policy and not position.v2_storm_drawdown_seen:
            adverse = position.entry_price - fill if position.direction == "BUY" else fill - position.entry_price
            if adverse >= 20 * 0.10:
                position.v2_storm_drawdown_seen = True
                if 1 not in position.tp_hits:
                    position.tp_prices["1"] = float(candidate.tps.get("1") or position.tp_prices["1"])
                account._audit("v2_storm_tp1_enabled", signal_id=candidate.signal_id,
                               adverse_usd=adverse, timestamp=timestamp)
        changed = False
        for index in (1, 2, 3):
            changed = self._close_target(
                account, candidate, position, index, quote, timestamp,
            ) or changed
        return changed
