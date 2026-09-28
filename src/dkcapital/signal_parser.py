from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

ActionKind = Literal[
    "new_signal",
    "trade_update",
    "add_layer",
    "reenter",
    "cancel",
    "close",
    "stop_loss",
    "setup_failed",
    "breakeven_close",
    "commentary",
]


def _norm(text: str) -> str:
    text = text.upper().replace("–", "-").replace("—", "-")
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^\S\r\n]+", " ", text)
    return text.strip()


def _number(value: str) -> float:
    return float(value.replace(",", ""))


def _symbol(text: str) -> str | None:
    if re.search(r"\b(?:XAU\s*/?\s*USD|XAUUSD|XAUUSDT|XAU|GOLD)\b", text):
        return "XAUUSD"
    match = re.search(r"\b([A-Z]{3,10}USDT?)\b", text)
    return match.group(1) if match else None


def _direction(text: str) -> str | None:
    if re.search(r"\bBUY(?:ER|ERS)?\b", text):
        return "BUY"
    if re.search(r"\bSELL(?:ER|ERS)?\b", text):
        return "SELL"
    return None


def _extract_tps(text: str) -> dict[int, float]:
    """Extract targets in message order and preserve repeated sequential labels."""
    result: dict[int, float] = {}
    for raw_index, value in re.findall(
        r"\bTP\s*(\d+)\s*[:=@-]?\s*(\d+(?:\.\d+)?)\b",
        text,
    ):
        index = int(raw_index)
        while index in result:
            index += 1
        result[index] = _number(value)
    return result


def _extract_tp_hits(text: str) -> list[int]:
    positive_result = bool(
        re.search(r"\b(?:HIT|HITS|DONE|SECURED)\b", text)
        or re.search(r"\+\s*\d+(?:\.\d+)?\s*PIPS?\b", text)
    )
    if not positive_result:
        return []
    return sorted({int(value) for value in re.findall(r"\bTP\s*(\d+)\b", text)})


def _explicit_sl(text: str) -> float | None:
    patterns = (
        r"\b(?:BOTH\s+|ALL\s+(?:GOLD|XAU(?:USD|USDT)?)\s+)?"
        r"SL(?:'S)?(?:\s+MOVE(?:D)?(?:\s+AGAIN)?|\s+ONE\s+MORE\s+TIME)?"
        r"\s+TO\s+(\d+(?:\.\d+)?)\b",
        r"\bSL\s+ALSO\s+(\d+(?:\.\d+)?)\b",
        r"\bSL\s*[:=@-]?\s*(\d+(?:\.\d+)?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _number(match.group(1))
    return None


def _layer_max(text: str) -> float | None:
    match = re.search(
        r"\b(?:ALLOWED\s+TO\s+)?LAYER(?:ING)?\s+(?:UNTIL\s+)?MAX\s+(\d+(?:\.\d+)?)\b",
        text,
    )
    return _number(match.group(1)) if match else None


def _move_to_be(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:SL\s+(?:TO\s+)?(?:BE|BREAKEVEN|BREAK\s*EVEN|ENTRY)|"
            r"(?:MOVE|SET|ADJUST)\s+(?:THE\s+)?SL\s+(?:TO\s+)?"
            r"(?:BE|BREAKEVEN|BREAK\s*EVEN|ENTRY))\b",
            text,
        )
    )


def _scope(text: str, reply_to_message_id: int | None) -> str:
    if reply_to_message_id is not None:
        return "reply"
    if re.search(r"\bALL\s+(?:GOLD|XAU(?:USD|USDT)?)\b", text):
        return "all_symbol"
    return "current_group"


@dataclass(frozen=True)
class ParsedAction:
    kind: ActionKind
    symbol: str | None = None
    direction: str | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    tps: dict[int, float] = field(default_factory=dict)
    sl: float | None = None
    tp_hits: tuple[int, ...] = ()
    move_sl_to_be: bool = False
    layer_max: float | None = None
    scope: str = "none"
    reply_to_message_id: int | None = None
    raw_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_low": self.entry_low,
            "entry_high": self.entry_high,
            "tps": self.tps,
            "sl": self.sl,
            "tp_hits": list(self.tp_hits),
            "move_sl_to_be": self.move_sl_to_be,
            "layer_max": self.layer_max,
            "scope": self.scope,
            "reply_to_message_id": self.reply_to_message_id,
            "raw_text": self.raw_text,
        }


def parse_actions(text: str, reply_to_message_id: int | None = None) -> list[ParsedAction]:
    normalized = _norm(text)
    symbol = _symbol(normalized)
    direction = _direction(normalized)
    actions: list[ParsedAction] = []

    entry = re.search(
        r"\b(?:BUY|SELL)\s+"
        r"(?:XAU\s*/?\s*USD|XAUUSD|XAUUSDT|XAU|GOLD|[A-Z]{3,10}USDT?)"
        r"\s+(?:AT|@)\s*(\d+(?:\.\d+)?)"
        r"(?:\s*-\s*(\d+(?:\.\d+)?))?",
        normalized,
    )
    if entry and symbol and direction:
        first = _number(entry.group(1))
        second = _number(entry.group(2)) if entry.group(2) else first
        return [
            ParsedAction(
                kind="new_signal",
                symbol=symbol,
                direction=direction,
                entry_low=min(first, second),
                entry_high=max(first, second),
                tps=_extract_tps(normalized),
                sl=_explicit_sl(normalized),
                layer_max=_layer_max(normalized),
                scope="new",
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        ]

    scope = _scope(normalized, reply_to_message_id)

    if re.search(
        r"\b(?:BOTH\s+)?(?:OUT\s+ON\s+SL|SL(?:'S)?\s+(?:HIT|HITTED|TAGGED|TAKEN|OUT))\b",
        normalized,
    ):
        actions.append(
            ParsedAction(
                kind="stop_loss",
                symbol=symbol,
                direction=direction,
                scope=scope,
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    if re.search(
        r"\b(?:BE|BREAKEVEN|BREAK\s*EVEN)\s+(?:HIT|HITTED|TAGGED|TAKEN|OUT)\b",
        normalized,
    ):
        actions.append(
            ParsedAction(
                kind="breakeven_close",
                symbol=symbol,
                direction=direction,
                scope=scope,
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    if re.search(r"\bSETUP\s+FAILED\b", normalized):
        actions.append(
            ParsedAction(
                kind="setup_failed",
                symbol=symbol,
                direction=direction,
                scope=scope,
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    explicit_cancel = bool(
        re.search(
            r"\b(?:CANCEL(?:LED)?(?:\s+(?:HERE|THIS|IT))?|"
            r"DON'?T\s+TAKE(?:\s+THIS)?|IGNORE\s+THIS|LEAVE\s+IT)\b",
            normalized,
        )
    )
    contextual_cancel = bool(
        reply_to_message_id is not None
        and re.search(
            r"\b(?:MARKET\s+(?:IS\s+)?NOT\s+GOOD(?:\s+NOW)?|"
            r"WAIT\s+I\s+WILL\s+UPDATE\s+(?:A\s+)?GOOD\s+SIGNAL\s+LATER)\b",
            normalized,
        )
    )
    if explicit_cancel or contextual_cancel:
        actions.append(
            ParsedAction(
                kind="cancel",
                symbol=symbol,
                direction=direction,
                scope=scope,
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    if re.search(
        r"(?:^|\n)\s*(?:CLOSE(?:D)?|EXIT(?:ED)?|GET\s+OUT)\b"
        r"|\b(?:CLOSE|EXIT)\s+(?:THIS|IT|NOW|ALL|GOLD|XAUUSD|TRADE|POSITION)\b",
        normalized,
    ):
        actions.append(
            ParsedAction(
                kind="close",
                symbol=symbol,
                direction=direction,
                scope=scope,
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    tp_hits = _extract_tp_hits(normalized)
    move_to_be = _move_to_be(normalized)
    sl = None if move_to_be else _explicit_sl(normalized)
    tps = _extract_tps(normalized)
    layer_max = _layer_max(normalized)

    terminal_result = any(
        action.kind in {"stop_loss", "setup_failed", "breakeven_close"}
        for action in actions
    )
    if tps or tp_hits or move_to_be or sl is not None or layer_max is not None:
        if not terminal_result or tp_hits:
            actions.append(
                ParsedAction(
                    kind="trade_update",
                    symbol=symbol,
                    direction=direction,
                    tps=tps,
                    sl=sl,
                    tp_hits=tuple(tp_hits),
                    move_sl_to_be=move_to_be,
                    layer_max=layer_max,
                    scope=scope,
                    reply_to_message_id=reply_to_message_id,
                    raw_text=text,
                )
            )

    if re.search(
        r"^(?:\+?LAYERS?|ADD(?:ING|S)?|ASDING)(?:\s+NOW)?(?:\s+SAME\s+SL)?[.! ]*$",
        normalized,
    ):
        actions.append(
            ParsedAction(
                kind="add_layer",
                symbol=symbol,
                direction=direction,
                scope="reply" if reply_to_message_id is not None else "latest_position",
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    if re.search(r"\b(?:WE\s+)?RE-?ENTER(?:\s+SAME)?\b|\bREENTRY\b", normalized):
        actions.append(
            ParsedAction(
                kind="reenter",
                symbol=symbol,
                direction=direction,
                scope="reply" if reply_to_message_id is not None else "latest_position",
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    if actions:
        kinds = {action.kind for action in actions}
        if "stop_loss" in kinds and "setup_failed" in kinds:
            actions = [action for action in actions if action.kind != "setup_failed"]
        return actions

    return [
        ParsedAction(
            kind="commentary",
            symbol=symbol,
            direction=direction,
            reply_to_message_id=reply_to_message_id,
            raw_text=text,
        )
    ]
