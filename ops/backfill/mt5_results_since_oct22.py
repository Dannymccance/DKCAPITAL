#!/usr/bin/env python3
"""Broker-verified, idempotent #results backfill from 22 October 2026.

Read-only preview by default. Before --publish, stop the normal Discord bot
to prevent publication-ledger races; restart it immediately afterward.
Only complete broker-confirmed flat groups qualify, never Telegram claims.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from dkcapital.mt5_trade_results import (
    completed_results, result_embed, verified_snapshot,
)

TARGET_CHANNEL = "1561095999139877046"
BACKFILL_START = date(2026, 10, 22)
TIMEZONE = ZoneInfo("Europe/Isle_of_Man")
STATE_PATH = Path("/app/data/mt5-results-published.json")
TELEMETRY_PATH = Path(os.getenv("MT5_ACCOUNT1_TELEMETRY_PATH", "/app/data/mt5-account1.json"))
SIGNALS_PATH = Path(os.getenv("SIGNAL_STATE_PATH", "/app/data/signal-state.json"))
BACKUP_PREFIX = "mt5-results-published.pre-oct22-backfill-"
BASE_URL = "https://discord.com/api/v10"
MAX_HISTORY_MESSAGES = 2000


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def select_results(snapshot: dict, signals: list, state: dict, *,
                   since: date = BACKFILL_START,
                   through: date | None = None) -> list[tuple[str, dict]]:
    if not verified_snapshot(snapshot):
        raise ValueError("broker snapshot missing, unverified or stale; nothing published")
    if state.get("initialized") is not True or not state.get("cutover"):
        raise ValueError("original Discord publication state is not initialized")
    published = state.get("published")
    if not isinstance(published, dict):
        raise ValueError("invalid publication ledger")
    through = through or datetime.now(TIMEZONE).date()
    if through < since:
        raise ValueError("backfill end date precedes start date")
    selected = []
    for root, result in completed_results(snapshot, signals).items():
        closed = datetime.fromisoformat(result["closed_at"]).astimezone(TIMEZONE)
        if since <= closed.date() <= through:
            if result["currency"] != "GBP":
                raise ValueError(f"unexpected currency for {root!r}")
            existing = published.get(root)
            if existing is not None and existing != "pre-cutover" and not (
                isinstance(existing, str) and existing.isdecimal()
            ):
                raise ValueError(f"unexpected publication state for {root!r}: {existing!r}")
            selected.append((root, result))
    return sorted(selected, key=lambda pair: (pair[1]["closed_at"], pair[0]))


def marker(root: str) -> str:
    return "DKC-RESULT-" + hashlib.sha256(root.encode("utf-8")).hexdigest()[:24]


def scan_message_marker(message: dict) -> str | None:
    for embed in message.get("embeds") or []:
        footer = embed.get("footer") or {}
        text = str(footer.get("text") or "")
        if "DKC-RESULT-" in text:
            return text.split("DKC-RESULT-", 1)[1][:24].split()[0].strip("|")
    return None


def api_request(token: str, method: str, path: str, payload: dict | None = None):
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        BASE_URL + path, data=body, method=method,
        headers={
            "Authorization": "Bot " + token,
            "Content-Type": "application/json",
            "User-Agent": "DKCapitalResultsBackfill/1.0",
        },
    )
    for attempt in range(5):
        try:
            with urlopen(request, timeout=25) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            if exc.code == 429 and attempt < 4:
                try:
                    wait = min(60.0, float(json.loads(exc.read()).get("retry_after", 2.0)))
                except (ValueError, TypeError):
                    wait = 2.0
                time.sleep(max(1.0, wait))
                continue
            raise RuntimeError(f"Discord API {method} returned HTTP {exc.code}") from exc
        except (OSError, URLError) as exc:
            # NEVER automatically retry an ambiguous POST response: the card may
            # already have been delivered. Re-run to scan for the stable marker.
            raise RuntimeError(f"Discord API {method} failed; inspect channel and re-run: {exc}") from exc
    raise RuntimeError("Discord API rate limit did not clear")


def recent_messages(token: str, channel_id: str) -> list[dict]:
    messages: list[dict] = []
    before = None
    while len(messages) < MAX_HISTORY_MESSAGES:
        args = {"limit": 100}
        if before is not None:
            args["before"] = before
        page = api_request(token, "GET", f"/channels/{channel_id}/messages?{urlencode(args)}")
        if not isinstance(page, list):
            raise ValueError("unexpected Discord channel history format")
        messages.extend(page)
        if len(page) < 100:
            return messages
        before = str(page[-1]["id"])
    # Fail closed instead of claiming that the entire channel was searched.
    raise ValueError("channel exceeds 2000 recent messages; historical duplicate scan incomplete")


def save_state(state: dict) -> None:
    # Preserve the bot's full ledger (including active_mt5_groups and cutover).
    # Atomic swap avoids a partially-written file if interrupted.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=STATE_PATH.parent,
        prefix=".mt5-results-backfill-", delete=False,
    ) as tmp:
        json.dump(state, tmp, separators=(",", ":"), sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(STATE_PATH)


def add_message_id(root: str, message_id: str) -> None:
    state = load_json(STATE_PATH)
    previous = state["published"].get(root)
    if previous not in (None, "pre-cutover", message_id):
        raise ValueError(f"publication ledger changed for {root!r}, please investigate")
    state["published"][root] = message_id
    save_state(state)


def possible_legacy_duplicate(messages: list[dict], result: dict) -> bool:
    # Existing cards without our stable marker might already represent the same
    # trade. Stop for manual review rather than knowingly creating duplicates.
    for message in messages:
        for embed in message.get("embeds") or []:
            fields = {f.get("name"): str(f.get("value") or "") for f in embed.get("fields") or []}
            pnl = fields.get("TOTAL REALISED P&L", "").replace("*", "").replace(",", "")
            if (
                fields.get("PROVIDER") == result["provider"]
                and fields.get("SIDE") == result["side"]
                and pnl == ("£%+.2f" % float(result["pnl"]))
            ):
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Send broker-confirmed historical cards")
    parser.add_argument("--since", type=date.fromisoformat, default=BACKFILL_START)
    parser.add_argument("--through", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    snapshot = load_json(TELEMETRY_PATH)
    source = load_json(SIGNALS_PATH)
    signals = source.get("signals") or []
    if not isinstance(signals, list):
        raise ValueError("invalid signal state")
    state = load_json(STATE_PATH)
    selected = select_results(snapshot, signals, state, since=args.since, through=args.through)
    print("=== VERIFIED HISTORICAL RESULTS ===", flush=True)
    for root, result in selected:
        local = datetime.fromisoformat(result["closed_at"]).astimezone(TIMEZONE)
        print(local.strftime("%Y-%m-%d %H:%M"), result["provider"], result["side"],
              "GBP%+.2f" % result["pnl"], result["exit_type"],
              f"legs={len(result['legs'])}",
              "source=" + str(result.get("source") or "not recorded"),
              "state=" + str(state["published"].get(root, "unrecorded")),
              "key=" + marker(root), flush=True)
    missing = [(root, result) for root, result in selected
               if state["published"].get(root) in (None, "pre-cutover")]
    net = sum(float(result["pnl"]) for _, result in selected)
    print(f"RECONCILED: {len(selected)} groups | GBP{net:+,.2f}; "
          f"POTENTIALLY UNPOSTED: {len(missing)}", flush=True)
    if not selected:
        print("No complete broker-reconciled groups in the requested window; "
              "confirm MT5 telemetry contains historical IN and OUT deals.", flush=True)
    if not args.publish:
        print("DRY RUN ONLY — NOTHING SENT", flush=True)
        return 0

    configured_channel = os.getenv("MT5_RESULTS_CHANNEL_ID", TARGET_CHANNEL).strip() or TARGET_CHANNEL
    if configured_channel != TARGET_CHANNEL:
        raise ValueError("configured MT5 results channel does not match approved target")
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("DISCORD_BOT_TOKEN missing")
    messages = recent_messages(token, configured_channel)
    print(f"Checked {len(messages)} existing Discord messages for duplicates", flush=True)
    existing_markers: dict[str, str] = {}
    for msg in messages:
        short = scan_message_marker(msg)
        if short:
            existing_markers[short] = str(msg["id"])
    pending = [(root, result) for root, result in selected
               if state["published"].get(root) in (None, "pre-cutover")]
    for root, result in pending:
        if marker(root).split("DKC-RESULT-", 1)[1] not in existing_markers:
            if possible_legacy_duplicate(messages, result):
                raise ValueError(
                    f"Possible previously posted broker result for {root!r}; "
                    "review channel before posting any cards"
                )
    backup = STATE_PATH.parent / (BACKUP_PREFIX + datetime.now(TIMEZONE).strftime("%Y%m%dT%H%M%S") + ".json")
    shutil.copy2(STATE_PATH, backup)
    print("Original publication ledger backed up to", backup, flush=True)

    for root, result in selected:
        if state["published"].get(root) not in (None, "pre-cutover"):
            print("SKIP already recorded", marker(root), flush=True)
            continue
        key = marker(root).split("DKC-RESULT-", 1)[1]
        if key in existing_markers:
            add_message_id(root, existing_markers[key])
            print("RECOVER existing message", existing_markers[key], marker(root), flush=True)
            continue
        embed = result_embed(result)
        local = datetime.fromisoformat(result["closed_at"]).astimezone(TIMEZONE)
        embed.add_field(name="HISTORICAL BROKER CLOSE (ISLE OF MAN)",
                        value=local.strftime("%d %B %Y at %H:%M"), inline=False)
        embed.set_footer(text=(
            "MT5 broker deals only | GBP DEMO | historical backfill | " + marker(root)
        ))
        print("POSTING", marker(root), flush=True)
        response = api_request(token, "POST", f"/channels/{configured_channel}/messages",
                               {"embeds": [embed.to_dict()]})
        message_id = str(response.get("id") or "")
        if not message_id.isdecimal():
            raise ValueError(f"Discord sent an unexpected message ID; check channel for {marker(root)}")
        add_message_id(root, message_id)
        print("POSTED", message_id, marker(root), flush=True)
    print("BACKFILL COMPLETE — verified message IDs recorded in existing ledger", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"BACKFILL STOPPED: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
