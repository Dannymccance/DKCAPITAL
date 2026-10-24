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



def _explicit_gold_entry(text: str) -> tuple[float, float] | None:
    """Read explicit provider entries in gold-first or direction-first format.

    In shorthand such as 'XAUUSD 4084-85 BUY', 85 means 4085, not 85.
    Nearby hundred candidates are considered so 4099-01 means 4101.
    A shortened suffix must be within $20 of its first price; otherwise
    fail closed rather than guessing the intended entry zone.
    """
    gold = r"(?:XAU\s*/?\s*USD|XAUUSDT|XAU|GOLD)"
    first = r"(\d{3,5}(?:[.,]\d{1,2})?)"
    second = r"(\d{1,5}(?:[.,]\d{1,2})?)"
    zone = rf"{first}(?:\s*-\s*{second})?"
    patterns = (
        rf"\b{gold}\s+(?:BUY|SELL)(?:\s+NOW)?\s+{zone}",
        rf"\b(?:BUY|SELL)\s+{gold}(?:\s+NOW)?\s+{zone}",
        rf"\b{gold}\s*:?\s*{zone}\s*:?\s*(?:BUY|SELL)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        raw_first, raw_second = match.groups()
        base = float(raw_first.replace(",", "."))
        if raw_second is None:
            return base, base
        value = float(raw_second.replace(",", "."))
        first_digits = raw_first.split(".")[0].split(",")[0]
        suffix_digits = raw_second.split(".")[0].split(",")[0]
        if len(suffix_digits) < len(first_digits):
            modulus = 10 ** len(suffix_digits)
            century = int(base // modulus) * modulus
            choices = [century - modulus + value, century + value,
                       century + modulus + value]
            value = min(choices, key=lambda candidate: abs(candidate - base))
            if abs(value - base) > 20:
                return None
        return min(base, value), max(base, value)
    return None


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
    # GOLD 4049BUY is a real provider format, with no space between
    # the last price digit and the direction.
    if re.search(r"\b(?:BUY(?:ER|ERS)?|LONGS?)\b|\d{3,5}(?:[.,]\d{1,2})?BUY\b", text):
        return "BUY"
    if re.search(r"\b(?:SELL(?:ER|ERS)?|SHORTS?)\b|\d{3,5}(?:[.,]\d{1,2})?SELL\b", text):
        return "SELL"
    return None


def _extract_tps(text: str) -> dict[int, float]:
    """Extract targets in message order and preserve repeated sequential labels."""
    result: dict[int, float] = {}
    # Numbered labels take precedence. Keep explicit duplicate numbering
    # (e.g. TP3 repeated) as the next sequential target.
    for raw_index, value in re.findall(
        r"\bTP\s*(\d{1,2})(?:\s*[:=@-]\s*|\s+)(\d{3,6}(?:\.\d+)?)\b",
        text,
    ):
        index = int(raw_index)
        while index in result:
            index += 1
        result[index] = _number(value)
    if result:
        return result
    # STORM and MONEY HOUSE send consecutive unnumbered lines:
    # TP 4135.5 / TP 4137.5 / TP 4140. Support decimal-comma prices too.
    for value in re.findall(
        r"(?m)^\s*TP\s*[:=@-]?\s*(\d{3,6}(?:[.,]\d{1,2})?)(?!\d)",
        text,
    ):
        result[len(result) + 1] = float(value.replace(",", "."))
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
        r"\b(?:MOVE|SET|ADJUST|TRAIL)\s+(?:(?:BOTH|ALL|YOUR|THE)\s+)?"
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


def _defer_breakeven(text: str) -> bool:
    """Provider explicitly says TP1 does not authorize a BE move yet."""
    return bool(re.search(
        r"\b(?:NO|NOT|DON'?T\s+(?:SET|MOVE)\s+(?:TO\s+)?)\s*"
        r"(?:BREAKEVEN|BREAK\s*EVEN|BE)\s+YET\b",
        text,
    ))


def _tp3_open(text: str) -> bool:
    return bool(re.search(r"\bTP\s*3\s*[:=@-]?\s*OPEN\b", text))


def _move_to_be(text: str) -> bool:
    if _defer_breakeven(text):
        return False
    if re.search(r"\b(?:SET|SETTY|MOVE)\s+(?:ALL\s+)?(?:TO\s+)?BREAKEVEN\b", text):
        return True
    return bool(
        re.search(
            r"\b(?:SL\s+(?:TO\s+)?(?:BE|BREAKEVEN|BREAK\s*EVEN|ENTRY)|"
            r"(?:MOVE|SET|ADJUST)\s+(?:THE\s+|YOUR\s+)?"
            r"(?:SL|STOP(?:\s+LOSS)?)\s+(?:TO\s+)?"
            r"(?:BE|BREAKEVEN|BREAK\s*EVEN|ENTRY)|"
            r"\b(?:SET|MOVE\s+TO)\s+(?:BE|BREAKEVEN|BREAK\s*EVEN))\b",
            text,
        )
    )


def _scope(
    text: str,
    reply_to_message_id: int | None,
    symbol: str | None,
) -> str:
    # An explicit ALL-provider breakeven command applies across that
    # provider's active positions; replying to one post doesn't reduce ALL.
    if re.search(
        r"\b(?:SET|SETTY|MOVE)\s+ALL\s+(?:TO\s+)?(?:BE|BREAKEVEN|BREAK\s*EVEN)\b"
        r"|\bALL\s+(?:SL|STOP(?:\s+LOSS)?)\s+TO\s+(?:BE|BREAKEVEN|BREAK\s*EVEN)\b",
        text,
    ):
        return "all_direction"
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
    defer_breakeven: bool = False
    tp3_open: bool = False
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
            "defer_breakeven": self.defer_breakeven,
            "tp3_open": self.tp3_open,
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
        r"\b(?:BUY|SELL)\s+(?:MORE\s+)?"
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
                tp3_open=_tp3_open(normalized),
                sl=_explicit_sl(normalized),
                layer_max=_layer_max(normalized),
                source_style="gws",
                scope="new",
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        ]

    # GWS STORM / CAPITAL / MONEY HOUSE: XAU USD BUY NOW 4129-4125,
    # XAUUSD 4084-85 BUY, BUY GOLD NOW 4132-4126, XAUUSD 4049BUY.
    # These are new entries only when a direction and an explicit gold price
    # appear together; trade results such as 'XAUUSD Target Price 1' are ignored.
    gold_zone = _explicit_gold_entry(normalized) if symbol == "XAUUSD" and direction else None
    if gold_zone is not None:
        return [
            ParsedAction(
                kind="new_signal",
                symbol="XAUUSD",
                direction=direction,
                entry_low=gold_zone[0],
                entry_high=gold_zone[1],
                tps=_extract_tps(normalized),
                tp3_open=_tp3_open(normalized),
                sl=_explicit_sl(normalized),
                source_style="gws",
                scope="new",
                reply_to_message_id=reply_to_message_id,
                raw_text=text,
            )
        ]

    # A clearly formatted but malformed GOLD entry must not fall through
    # into an update and change the stop/target of an unrelated old trade.
    if gold_zone is None and symbol == "XAUUSD" and direction and re.search(
        r"\b(?:XAU\s*/?\s*USD|XAUUSDT|XAU|GOLD)\s*:?\s*"
        r"\d{3,5}(?:[.,]\d{1,2})?\s*-\s*"
        r"\d{1,5}(?:[.,]\d{1,2})?\s*:?\s*(?:BUY|SELL)\b",
        normalized,
    ):
        return [
            ParsedAction(
                kind="unparsed_new_signal",
                symbol="XAUUSD",
                direction=direction,
                source_style="gws",
                scope="none",
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
                    tp3_open=_tp3_open(normalized),
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

    # "Close partials, set BE" is not an instruction to liquidate
    # the full position. Unspecified partial sizes remain non-executable.
    partial_only = bool(re.search(
        r"(?:^|\n)\s*CLOSE\s+(?:PARTIALS?|HALF|\d{1,3}(?:\.\d+)?\s*%)\b",
        normalized,
    ))
    if not partial_only and re.search(
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
    defer_be = _defer_breakeven(normalized)
    tp3_open = _tp3_open(normalized)
    sl = None if move_to_be else _explicit_sl(normalized)
    tps = _extract_tps(normalized)
    layer_max = _layer_max(normalized)

    terminal_result = any(
        action.kind in {"stop_loss", "setup_failed", "breakeven_close"}
        for action in actions
    )
    if tps or tp_hits or move_to_be or defer_be or tp3_open or sl is not None or layer_max is not None:
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
                    defer_breakeven=defer_be,
                    tp3_open=tp3_open,
                    layer_max=layer_max,
                    scope=scope,
                    reply_to_message_id=reply_to_message_id,
                    raw_text=text,
                )
            )

    if re.search(
        r"^(?:\+?LAYERS?|ADD(?:ING|ED|S)?|ASDING)(?:\s+NOW)?(?:\s+SAME\s+SL)?[.! ]*$"
        r"|\b(?:BUY\s+MORE|SELL\s+MORE|MORE\s+BUYS?|MORE\s+SELLS?|"
        r"ANOTHER\s+ENTRY|SECOND\s+ENTRY|EXTRA\s+ENTRY|SCALE\s*-?\s*IN)\b",
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
