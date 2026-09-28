from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from dkcapital.signal_parser import ParsedAction, parse_actions

OPEN_STATUSES = {"ACTIVE"}


def _timestamp(event: dict[str, Any]) -> str:
    return str(event.get("date") or event.get("observed_at") or "")


@dataclass
class Signal:
    signal_id: str
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
    status: str = "ACTIVE"
    last_update_at: str | None = None
    last_update_text: str = ""
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
        last_targets: dict[int, tuple[str, list[str]]] = {}

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

            for action in actions:
                if action.kind == "commentary":
                    continue

                if action.kind == "new_signal":
                    assert action.symbol is not None
                    assert action.direction is not None
                    assert action.entry_low is not None
                    assert action.entry_high is not None

                    signal_id = f"{chat_id}:{message_id}"
                    signal = Signal(
                        signal_id=signal_id,
                        chat_id=chat_id,
                        root_message_id=message_id,
                        opened_at=timestamp,
                        symbol=action.symbol,
                        direction=action.direction,
                        entry_low=action.entry_low,
                        entry_high=action.entry_high,
                        tps=dict(action.tps),
                        original_sl=action.sl,
                        current_sl=action.sl,
                        sl_mode="PRICE" if action.sl is not None else "UNSET",
                        last_update_at=timestamp,
                        last_update_text=text,
                        source_message_ids=[message_id],
                    )
                    self.signals[signal_id] = signal
                    self.message_targets[(chat_id, message_id)] = [signal_id]
                    last_targets[chat_id] = (timestamp, [signal_id])
                    continue

                targets = self._resolve_targets(
                    action=action,
                    chat_id=chat_id,
                    timestamp=timestamp,
                    last_targets=last_targets,
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

                self.message_targets[(chat_id, message_id)] = targets
                last_targets[chat_id] = (timestamp, targets)
                for signal_id in targets:
                    signal = self.signals.get(signal_id)
                    if signal is not None:
                        self._apply(signal, action, message_id, timestamp, text)

    def _resolve_targets(
        self,
        action: ParsedAction,
        chat_id: int,
        timestamp: str,
        last_targets: dict[int, tuple[str, list[str]]],
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
            return list(self.message_targets.get((chat_id, action.reply_to_message_id), []))

        matching = [signal for signal in active if matches(signal)]

        if action.scope == "all_matching":
            return [signal.signal_id for signal in matching]

        if action.scope == "latest_matching":
            if matching:
                return [matching[-1].signal_id]
            return []

        if action.scope == "latest_active":
            if len(active) == 1:
                return [active[0].signal_id]

            sticky = last_targets.get(chat_id)
            if sticky and self._within_minutes(sticky[0], timestamp, 45):
                still_active = [
                    signal_id
                    for signal_id in sticky[1]
                    if signal_id in self.signals
                    and self.signals[signal_id].status in OPEN_STATUSES
                ]
                if still_active:
                    return still_active

            if active:
                return [active[-1].signal_id]

        return []

    @staticmethod
    def _within_minutes(earlier: str, later: str, minutes: int) -> bool:
        try:
            a = datetime.fromisoformat(earlier.replace("Z", "+00:00"))
            b = datetime.fromisoformat(later.replace("Z", "+00:00"))
            return 0 <= (b - a).total_seconds() <= minutes * 60
        except (TypeError, ValueError):
            return False

    def _apply(
        self,
        signal: Signal,
        action: ParsedAction,
        message_id: int,
        timestamp: str,
        text: str,
    ) -> None:
        if message_id not in signal.source_message_ids:
            signal.source_message_ids.append(message_id)

        if action.kind == "trade_update":
            for tp in action.tp_hits:
                if tp not in signal.tp_hits:
                    signal.tp_hits.append(tp)
            signal.tp_hits.sort()

            if action.move_sl_to_be:
                signal.sl_mode = "BREAKEVEN"
                signal.current_sl = None
            elif action.sl is not None:
                signal.sl_mode = "PRICE"
                signal.current_sl = action.sl

            if signal.tps and set(signal.tps).issubset(set(signal.tp_hits)):
                signal.status = "COMPLETED"

        elif action.kind == "add_layer":
            signal.layers += 1

        elif action.kind == "reenter":
            signal.reentries += 1
            if signal.status == "COMPLETED":
                signal.status = "ACTIVE"

        elif action.kind == "cancel":
            signal.status = "CANCELLED"

        elif action.kind == "close":
            signal.status = "CLOSED"

        signal.last_update_at = timestamp
        signal.last_update_text = text

    def snapshot(self) -> dict[str, Any]:
        ordered = sorted(self.signals.values(), key=lambda s: (s.opened_at, s.signal_id))
        return {
            "signals": [signal.as_dict() for signal in ordered],
            "unresolved_actions": self.unresolved_actions,
        }
