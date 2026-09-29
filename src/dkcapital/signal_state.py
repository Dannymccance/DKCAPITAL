from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from dkcapital.signal_parser import ParsedAction, parse_actions

OPEN_STATUSES = {"ACTIVE"}
GROUP_WINDOW_MINUTES = 180


def _timestamp(event: dict[str, Any]) -> str:
    return str(event.get("date") or event.get("observed_at") or "")


@dataclass
class Signal:
    signal_id: str
    group_id: str
    chat_id: int
    root_message_id: int
    opened_at: str
    symbol: str
    direction: str
    entry_low: float
    entry_high: float
    tps: dict[int, float] = field(default_factory=dict)
    original_sl: float | None = None
    current_sl: float | None = None
    sl_mode: str = "PRICE"
    tp_hits: list[int] = field(default_factory=list)
    layers: int = 1
    reentries: int = 0
    layer_max: float | None = None
    source_style: str | None = None
    remaining_fraction: float = 1.0
    partial_close_count: int = 0
    status: str = "ACTIVE"
    last_update_at: str | None = None
    last_update_text: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    source_message_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tps"] = {str(k): v for k, v in sorted(self.tps.items())}
        return data


class SignalState:
    def __init__(self) -> None:
        self.messages: dict[tuple[int, int], dict[str, Any]] = {}
        self.signals: dict[str, Signal] = {}
        self.message_targets: dict[tuple[int, int], list[str]] = {}
        self.unresolved_actions: list[dict[str, Any]] = []

    def ingest_event(self, event: dict[str, Any], *, rebuild: bool = True) -> None:
        event_type = event.get("event_type")
        chat_id = event.get("chat_id")
        if chat_id is None:
            return

        if event_type in {"new_message", "historical_message", "message_edited"}:
            message_id = event.get("message_id")
            if message_id is not None:
                self.messages[(int(chat_id), int(message_id))] = event
        elif event_type == "message_deleted":
            for message_id in event.get("message_ids", []):
                self.messages.pop((int(chat_id), int(message_id)), None)

        if rebuild:
            self.rebuild()

    def rebuild(self) -> None:
        self.signals = {}
        self.message_targets = {}
        self.unresolved_actions = []

        ordered = sorted(
            self.messages.values(),
            key=lambda event: (
                _timestamp(event),
                int(event.get("chat_id") or 0),
                int(event.get("message_id") or 0),
            ),
        )

        for event in ordered:
            chat_id = int(event["chat_id"])
            message_id = int(event["message_id"])
            text = str(event.get("text") or "")
            reply_to = event.get("reply_to_message_id")
            actions = parse_actions(text, int(reply_to) if reply_to is not None else None)
            timestamp = _timestamp(event)
            market = event.get("market") if isinstance(event.get("market"), dict) else None

            inherited_targets: list[str] = []
            if reply_to is not None:
                inherited_targets = list(
                    self.message_targets.get((chat_id, int(reply_to)), [])
                )

            message_targets: list[str] = []
            for action in actions:
                if action.kind == "commentary":
                    continue

                if action.kind == "new_signal":
                    assert action.symbol is not None
                    assert action.direction is not None
                    assert action.entry_low is not None
                    assert action.entry_high is not None

                    signal_id = f"{chat_id}:{message_id}"
                    group_id = self._group_for_new_signal(
                        chat_id=chat_id,
                        timestamp=timestamp,
                        symbol=action.symbol,
                        direction=action.direction,
                        source_style=action.source_style,
                        fallback=signal_id,
                    )
                    signal = Signal(
                        signal_id=signal_id,
                        group_id=group_id,
                        chat_id=chat_id,
                        root_message_id=message_id,
                        opened_at=timestamp,
                        symbol=action.symbol,
                        direction=action.direction,
                        entry_low=action.entry_low,
                        entry_high=action.entry_high,
                        tps=self._validated_tps(
                            action.tps,
                            action.direction,
                            action.entry_low,
                            action.entry_high,
                        ),
                        original_sl=action.sl,
                        current_sl=action.sl,
                        sl_mode="PRICE" if action.sl is not None else "UNSET",
                        layer_max=action.layer_max,
                        source_style=action.source_style,
                        last_update_at=timestamp,
                        last_update_text=text,
                        history=[
                            {
                                "timestamp": timestamp,
                                "message_id": message_id,
                                "kind": "opened",
                                "entry_low": action.entry_low,
                                "entry_high": action.entry_high,
                                "sl": action.sl,
                                "market": market,
                            }
                        ],
                        source_message_ids=[message_id],
                    )
                    self.signals[signal_id] = signal
                    message_targets = [signal_id]
                    continue

                targets = self._resolve_targets(
                    action=action,
                    chat_id=chat_id,
                    inherited_targets=inherited_targets,
                )
                if not targets:
                    self.unresolved_actions.append(
                        {
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "timestamp": timestamp,
                            "action": action.as_dict(),
                        }
                    )
                    continue

                for signal_id in targets:
                    signal = self.signals.get(signal_id)
                    if signal is not None:
                        self._apply(
                            signal,
                            action,
                            message_id,
                            timestamp,
                            text,
                            market,
                        )

                for signal_id in targets:
                    if signal_id not in message_targets:
                        message_targets.append(signal_id)

            if message_targets:
                self.message_targets[(chat_id, message_id)] = message_targets
            elif inherited_targets:
                self.message_targets[(chat_id, message_id)] = inherited_targets

    def _group_for_new_signal(
        self,
        *,
        chat_id: int,
        timestamp: str,
        symbol: str,
        direction: str,
        source_style: str | None,
        fallback: str,
    ) -> str:
        if source_style == "elite":
            return fallback
        compatible = [
            signal
            for signal in self.signals.values()
            if signal.chat_id == chat_id
            and signal.status in OPEN_STATUSES
            and signal.symbol == symbol
            and signal.direction == direction
        ]
        if not compatible:
            return fallback

        latest = compatible[-1]
        if self._within_minutes(latest.opened_at, timestamp, GROUP_WINDOW_MINUTES):
            return latest.group_id
        return fallback

    def _resolve_targets(
        self,
        *,
        action: ParsedAction,
        chat_id: int,
        inherited_targets: list[str],
    ) -> list[str]:
        active = [
            signal
            for signal in self.signals.values()
            if signal.chat_id == chat_id and signal.status in OPEN_STATUSES
        ]

        def matches(signal: Signal) -> bool:
            if action.symbol and signal.symbol != action.symbol:
                return False
            if action.direction and signal.direction != action.direction:
                return False
            return True

        if action.scope == "reply" and action.reply_to_message_id is not None:
            # A Telegram reply is an explicit target. Do not let descriptive
            # words in the management message (for example "cancel mubarak long")
            # override or filter the replied-to signal.
            return [
                signal_id
                for signal_id in inherited_targets
                if signal_id in self.signals
                and self.signals[signal_id].status in OPEN_STATUSES
            ]

        matching = [signal for signal in active if matches(signal)]
        if not matching:
            return []

        if action.scope in {"all_symbol", "all_direction"}:
            return [signal.signal_id for signal in matching]

        if action.scope == "latest_symbol":
            return [matching[-1].signal_id]

        if action.scope == "latest_position":
            return [matching[-1].signal_id]

        if action.scope == "current_group":
            latest = matching[-1]
            return [
                signal.signal_id
                for signal in matching
                if signal.group_id == latest.group_id
            ]

        return []

    @staticmethod
    def _within_minutes(earlier: str, later: str, minutes: int) -> bool:
        try:
            a = datetime.fromisoformat(earlier.replace("Z", "+00:00"))
            b = datetime.fromisoformat(later.replace("Z", "+00:00"))
            return 0 <= (b - a).total_seconds() <= minutes * 60
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _validated_tps(
        tps: dict[int, float],
        direction: str,
        entry_low: float,
        entry_high: float,
    ) -> dict[int, float]:
        if not tps:
            return {}

        ordered = [value for _, value in sorted(tps.items())]
        anchor = entry_high if direction == "BUY" else entry_low
        if direction == "BUY":
            valid = all(value > anchor for value in ordered) and all(
                left < right for left, right in zip(ordered, ordered[1:])
            )
        else:
            valid = all(value < anchor for value in ordered) and all(
                left > right for left, right in zip(ordered, ordered[1:])
            )

        # Keep explicit provider values even if they fail monotonic validation.
        # The validation result exists to prevent us from inventing targets, not
        # to silently discard an explicitly sent correction.
        return dict(sorted(tps.items())) if valid else dict(sorted(tps.items()))

    def _apply(
        self,
        signal: Signal,
        action: ParsedAction,
        message_id: int,
        timestamp: str,
        text: str,
        market: dict[str, Any] | None,
    ) -> None:
        if message_id not in signal.source_message_ids:
            signal.source_message_ids.append(message_id)

        history_event: dict[str, Any] = {
            "timestamp": timestamp,
            "message_id": message_id,
            "kind": action.kind,
        }
        if market is not None:
            history_event["market"] = market

        if action.kind == "trade_update":
            history_event["sl_before"] = signal.current_sl
            history_event["sl_mode_before"] = signal.sl_mode
            if action.tps:
                history_event["tps"] = dict(action.tps)
                signal.tps = self._validated_tps(
                    action.tps,
                    signal.direction,
                    signal.entry_low,
                    signal.entry_high,
                )

            if action.tp_hits:
                history_event["tp_hits"] = list(action.tp_hits)
            for tp in action.tp_hits:
                if tp not in signal.tp_hits:
                    signal.tp_hits.append(tp)
            signal.tp_hits.sort()

            if action.move_sl_to_be:
                signal.sl_mode = "BREAKEVEN"
                signal.current_sl = None
                history_event["move_sl_to_be"] = True
            elif action.sl is not None:
                signal.sl_mode = "PRICE"
                signal.current_sl = action.sl
                history_event["sl_after"] = action.sl

            if action.layer_max is not None:
                signal.layer_max = action.layer_max
                history_event["layer_max"] = action.layer_max

            if signal.tps and set(signal.tps).issubset(set(signal.tp_hits)):
                signal.status = "COMPLETED"

        elif action.kind == "partial_close":
            if action.partial_percent is not None:
                history_event["partial_percent"] = action.partial_percent
                fraction = max(0.0, min(1.0, action.partial_percent / 100.0))
                signal.remaining_fraction *= 1.0 - fraction
                signal.partial_close_count += 1
                if signal.remaining_fraction <= 1e-9:
                    signal.remaining_fraction = 0.0
                    signal.status = "CLOSED"
                history_event["remaining_fraction"] = signal.remaining_fraction

        elif action.kind == "add_layer":
            signal.layers += 1
            history_event["layers"] = signal.layers

        elif action.kind == "reenter":
            signal.reentries += 1
            history_event["reentries"] = signal.reentries

        elif action.kind == "cancel":
            signal.status = "CANCELLED"
            history_event["status"] = signal.status

        elif action.kind == "close":
            signal.status = "CLOSED"
            history_event["status"] = signal.status

        elif action.kind == "stop_loss":
            signal.status = "STOPPED"
            history_event["status"] = signal.status

        elif action.kind == "setup_failed":
            signal.status = "FAILED"
            history_event["status"] = signal.status

        elif action.kind == "breakeven_close":
            signal.status = "BREAKEVEN"
            history_event["status"] = signal.status

        signal.history.append(history_event)
        signal.last_update_at = timestamp
        signal.last_update_text = text

    def snapshot(self) -> dict[str, Any]:
        ordered = sorted(self.signals.values(), key=lambda s: (s.opened_at, s.signal_id))
        groups: dict[str, dict[str, Any]] = {}
        for signal in ordered:
            group = groups.setdefault(
                signal.group_id,
                {
                    "group_id": signal.group_id,
                    "chat_id": signal.chat_id,
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "signal_ids": [],
                    "active_signal_ids": [],
                },
            )
            group["signal_ids"].append(signal.signal_id)
            if signal.status in OPEN_STATUSES:
                group["active_signal_ids"].append(signal.signal_id)

        return {
            "signals": [signal.as_dict() for signal in ordered],
            "groups": list(groups.values()),
            "unresolved_actions": self.unresolved_actions,
        }
