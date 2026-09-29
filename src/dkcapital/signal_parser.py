from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

ActionKind = Literal[
    "new_signal",
    "trade_update",
    "partial_close",
    "add_layer",
    "reenter",
    "cancel",
    "close",
    "stop_loss",
    "setup_failed",
    "breakeven_close",
    "unparsed_new_signal",
    "commentary",
]


def _norm(text: str) -> str:
    text = text.upper().replace("–", "-").replace("—", "-")
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^\S\r\n]+", " ", text)
    return text.strip()


def _number(value: str) -> float:
    return float(value.replace(",", ""))


def _labelled_price(text: str, label_pattern: str) -> float | None:
    """Extract a provider-labelled price without requiring whitespace after it.

    Telegram providers sometimes send values such as "Entry: 4157low lot".
    Stopping the numeric capture when the digits end is deliberate: a trailing
    word boundary would reject that otherwise valid price because both the
    final digit and the following letter are regex word characters.
    """
    match = re.search(
        rf"\b(?:{label_pattern})\s*(?::|=|@|-)?\s*(\d[\d,]*(?:\.\d+)?)",
        text,
    )
    return _number(match.group(1)) if match else None


def _symbol(text: str) -> str | None:
    if re.search(r"\b(?:XAU\s*/?\s*USD|XAUUSD|XAUUSDT|XAU|GOLD)\b", text):
        return "XAUUSD"
    if re.search(r"\b(?:BTCUSDT|BTCUSD|BTC|BITCOIN)\b", text):
        return "BTCUSDT"
    match = re.search(r"\b([A-Z]{3,10}USDT?)\b", text)
    return match.group(1) if match else None


def _direction(text: str) -> str | None:
    if re.search(r"\b(?:BUY(?:ER|ERS)?|LONGS?)\b", text):
        return "BUY"
    if re.search(r"\b(?:SELL(?:ER|ERS)?|SHORTS?)\b", text):
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
        r"\s+TO\s+(?:THIS\s+)?(\d+(?:\.\d+)?)\b",
        r"\bSL\s+ALSO\s+(\d+(?:\.\d+)?)\b",
        r"\b(?:MOVE|SET|ADJUST|TRAIL)\s+(?:YOUR\s+|THE\s+)?"
        r"(?:STOP\s*LOSS|STOP|SL)\s+(?:TO|AT|@)\s+(?:THIS\s+)?(\d+(?:\.\d+)?)\b",
        r"\b(?:STOP\s*LOSS|STOP|SL)\s+(?:TO|AT|@)\s+(?:THIS\s+)?(\d+(?:\.\d+)?)\b",
        r"\b(?:STOP\s*LOSS|SL)\s*:\s*(\d+(?:\.\d+)?)\b",
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


def _partial_percent(text: str) -> float | None:
    percent_patterns = (
        r"\b(?:TAKE\s+)?(?:FURTHER\s+)?(\d{1,3}(?:\.\d+)?)\s*%\s+PARTIAL\b",
        r"\b(?:PARTIAL|CLOSE)\s+(\d{1,3}(?:\.\d+)?)\s*%\b",
    )
    for pattern in percent_patterns:
        match = re.search(pattern, text)
        if match:
            value = _number(match.group(1))
            if 0 < value < 100:
                return value

    if re.search(
        r"\b(?:TP|TAKE|CLOSE|PARTIAL)\s+(?:A\s+)?HALF\b|"
        r"\bHALF\s+OFF\b|\bTAKE\s+HALF\s+OFF\b",
        text,
    ):
        return 50.0

    return None


def _move_to_be(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:SL\s+(?:TO\s+)?(?:BE|BREAKEVEN|BREAK\s*EVEN|ENTRY)|"
            r"(?:MOVE|SET|ADJUST)\s+(?:THE\s+|YOUR\s+)?"
            r"(?:SL|STOP(?:\s+LOSS)?)\s+(?:TO\s+)?"
            r"(?:BE|BREAKEVEN|BREAK\s*EVEN|ENTRY))\b",
            text,
        )
    )


def _scope(
    text: str,
    reply_to_message_id: int | None,
    symbol: str | None,
) -> str:
    if reply_to_message_id is not None:
        return "reply"
    if re.search(r"\bALL\s+(?:GOLD|XAU(?:USD|USDT)?|BTC(?:USD|USDT)?)\b", text):
        return "all_symbol"
    if re.search(r"\bBOTH\b", text):
        return "current_group"
    if symbol is not None:
        return "latest_symbol"
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
    partial_percent: float | None = None
    source_style: str | None = None
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
            "partial_percent": self.partial_percent,
            "source_style": self.source_style,
            "scope": self.scope,
            "reply_to_message_id": self.reply_to_message_id,
            "raw_text": self.raw_text,
        }


def parse_actions(text: str, reply_to_message_id: int | None = None) -> list[ParsedAction]:
    normalized = _norm(text)
    symbol = _symbol(normalized)
    direction = _direction(normalized)
    actions: list[ParsedAction] = []

    # GWS-style one-line/range signal.
    entry = re.search(
        r"\b(?:BUY|SELL)\s+"
        r"(?:XAU\s*/?\s*USD|XAUUSD|XAUUSDT|XAU|GOLD|BTC(?:USD|USDT)?|BITCOIN|[A-Z]{3,10}USDT?)"
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
                source_style="gws",
                scope="new",
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        ]

    # Elite Portfolios-style structured signal:
    # "I am personally entering ... Gold buy ... Entry: 4281 ... Stop loss: 4275 ... Take profit: Open"
    #
    # Be deliberately tolerant around labelled prices. Elite sometimes omits
    # whitespace after the number ("Entry: 4157low lot") or varies separators.
    # An SL may also arrive on a subsequent Telegram edit, so a valid entry is
    # enough to create the signal with an UNSET stop until the edit arrives.
    elite_signal_hint = bool(
        symbol
        and direction
        and re.search(r"\bENTRY\b", normalized)
        and (
            "I AM PERSONALLY ENTERING" in normalized
            or re.search(r"\bTAKE\s+PROFIT\b", normalized)
        )
    )
    if elite_signal_hint:
        structured_entry = _labelled_price(normalized, r"ENTRY")
        structured_sl = _labelled_price(normalized, r"STOP\s*LOSS|SL")

        if structured_entry is not None:
            return [
                ParsedAction(
                    kind="new_signal",
                    symbol=symbol,
                    direction=direction,
                    entry_low=structured_entry,
                    entry_high=structured_entry,
                    tps=_extract_tps(normalized),
                    sl=structured_sl,
                    source_style="elite",
                    scope="new",
                    reply_to_message_id=reply_to_message_id,
                    raw_text=text,
                )
            ]

        # A message that is clearly presenting itself as a fresh Elite signal
        # must never fall through and become a management update for an older
        # position. Preserve it as unresolved instead.
        return [
            ParsedAction(
                kind="unparsed_new_signal",
                symbol=symbol,
                direction=direction,
                source_style="elite",
                scope="none",
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        ]

    scope = _scope(normalized, reply_to_message_id, symbol)

    # Provider-wide side exits such as "Take profits all longs now".
    # These deliberately do not require a ticker or reply target. The state
    # resolver still scopes them to the originating Telegram chat/provider.
    side_close = re.search(
        r"\b(?:TAKE\s+PROFITS?|CLOSE|EXIT|SECURE\s+PROFITS?)\s+"
        r"(?:ON\s+)?ALL\s+(LONGS?|SHORTS?|BUYS?|SELLS?)\b",
        normalized,
    )
    if side_close:
        side_word = side_close.group(1)
        side_direction = "BUY" if side_word.startswith(("LONG", "BUY")) else "SELL"
        return [
            ParsedAction(
                kind="close",
                direction=side_direction,
                scope="all_direction",
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        ]

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
            r"DON'?T\s+TAKE(?:\s+THIS)?|DO\s+NOT\s+ENTER|"
            r"IGNORE\s+(?:THIS|GOLD|BTC)|LEAVE\s+IT)\b",
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
        r"(?:^|\n)\s*(?:CLOSE(?:D)?|EXIT(?:ED)?|GET\s+OUT|I'?M\s+OUT)\b"
        r"|\b(?:CLOSE|EXIT|CUT)\s+(?:THIS|IT|NOW|ALL|GOLD|BTC|XAUUSD|TRADE|POSITION)\b"
        r"|\bOUT\s+OF\s+(?:GOLD|BTC)\b",
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

    partial_percent = _partial_percent(normalized)
    if partial_percent is not None:
        actions.append(
            ParsedAction(
                kind="partial_close",
                symbol=symbol,
                direction=direction,
                partial_percent=partial_percent,
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

    if re.search(r"\b(?:WE\s+)?RE-?ENTER(?:ING)?(?:\s+SAME)?\b|\bREENTRY\b", normalized):
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
