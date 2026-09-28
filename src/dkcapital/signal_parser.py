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
    "breakeven_close",
    "commentary",
]


def _norm(text: str) -> str:
    text = text.upper().replace("–", "-").replace("—", "-")
    text = re.sub(r"[^\S\r\n]+", " ", text)
    return text.strip()


def _number(value: str) -> float:
    return float(value.replace(",", ""))


def _symbol(text: str) -> str | None:
    if re.search(r"\b(?:XAU\s*/?\s*USD|XAUUSD|GOLD)\b", text):
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
    result: dict[int, float] = {}
    for index, value in re.findall(r"\bTP\s*(\d+)\s*[:=@-]?\s*(\d+(?:\.\d+)?)\b", text):
        result[int(index)] = _number(value)
    return result


def _extract_tp_hits(text: str) -> list[int]:
    hits: set[int] = set()

    if re.search(r"\bTP\s*1\s*(?:&|\+|AND)\s*TP\s*2\b", text):
        if re.search(r"\b(?:HIT|HITS|PIPS|DONE|SECURED)\b", text):
            hits.update((1, 2))

    for index in re.findall(
        r"\bTP\s*(\d+)\b(?=[^\n]{0,30}\b(?:HIT|HITS|DONE|SECURED|\+\s*\d+(?:\.\d+)?\s*PIPS)\b)",
        text,
    ):
        hits.add(int(index))

    for index in re.findall(r"\bTP\s*(\d+)\s+HIT\b", text):
        hits.add(int(index))

    return sorted(hits)


def _explicit_sl(text: str) -> float | None:
    patterns = (
        r"\bSL(?:\s+MOVE(?:D)?(?:\s+AGAIN)?|\s+ONE\s+MORE\s+TIME)?\s+TO\s+(\d+(?:\.\d+)?)\b",
        r"\bSL\s*[:=@-]?\s*(\d+(?:\.\d+)?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _number(match.group(1))
    return None


def _move_to_be(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:SL\s+(?:TO\s+)?(?:BE|BREAKEVEN|BREAK\s*EVEN|ENTRY)|"
            r"(?:MOVE|SET|ADJUST)\s+(?:THE\s+)?SL\s+(?:TO\s+)?(?:BE|BREAKEVEN|BREAK\s*EVEN|ENTRY))\b",
            text,
        )
    )


def _scope(text: str, reply_to_message_id: int | None, symbol: str | None) -> str:
    if reply_to_message_id is not None:
        return "reply"
    if re.search(r"\bALL\s+(?:GOLD|XAU(?:USD)?)\b", text):
        return "all_matching"
    if re.search(r"\b(?:ALL|BOTH)\s+(?:SL|TRADES?|POSITIONS?|BUYS?|SELLS?)\b", text):
        return "all_matching"
    return "latest_matching" if symbol else "latest_active"


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
        r"\b(?:BUY|SELL)\s+(?:XAU\s*/?\s*USD|XAUUSD|GOLD|[A-Z]{3,10}USDT?)"
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
                scope="new",
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        ]

    close_match = re.search(
        r"(?:^|\n)\s*(CANCEL(?:LED)?|CLOSE(?:D)?|EXIT(?:ED)?|GET\s+OUT)\b"
        r"|\b(?:CANCEL|CLOSE|EXIT)\s+(?:THIS|IT|NOW|ALL|GOLD|XAUUSD|TRADE|POSITION)\b",
        normalized,
    )
    if close_match:
        matched = close_match.group(0).strip()
        actions.append(
            ParsedAction(
                kind="cancel" if matched.startswith("CANCEL") else "close",
                symbol=symbol,
                direction=direction,
                scope=_scope(normalized, reply_to_message_id, symbol),
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    tp_hits = _extract_tp_hits(normalized)
    move_to_be = _move_to_be(normalized)
    sl = None if move_to_be else _explicit_sl(normalized)
    if tp_hits or move_to_be or sl is not None:
        actions.append(
            ParsedAction(
                kind="trade_update",
                symbol=symbol,
                direction=direction,
                sl=sl,
                tp_hits=tuple(tp_hits),
                move_sl_to_be=move_to_be,
                scope=_scope(normalized, reply_to_message_id, symbol),
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    if re.fullmatch(r"(?:ADD|ADDS|LAYER|LAYER\s+IN|ADD\s+MORE)[.! ]*", normalized):
        actions.append(
            ParsedAction(
                kind="add_layer",
                symbol=symbol,
                direction=direction,
                scope=_scope(normalized, reply_to_message_id, symbol),
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    if re.search(r"\b(?:BACK\s+AT\s+ENTRY.*ENTER\s+AGAIN|RE-?ENTER|REENTRY)\b", normalized):
        actions.append(
            ParsedAction(
                kind="reenter",
                symbol=symbol,
                direction=direction,
                scope=_scope(normalized, reply_to_message_id, symbol),
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        )

    if actions:
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
