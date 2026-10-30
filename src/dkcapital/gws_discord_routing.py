"""Presentation-only routing of GWS XAUUSD signal cards by Telegram provider.

No execution, Telegram intake, signal parsing, or EA policy is modified.
The two approved live source peer IDs are reused only for attribution.
Other GWS groups are identified by exact source titles for *display* only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from unicodedata import normalize

from dkcapital.mt5_tp1_policy import STORM_CHAT_ID, CAPITAL_CHAT_ID, source_id

PROVIDER_CHANNEL_NAMES = {
    "STORM": "gws-storm-signals",
    "CAPITAL": "gws-capital-signals",
    "MONEY HOUSE": "gws-money-house-signals",
    "VIP": "gws-vip-signals",
}
PROVIDER_FROM_TITLE = {
    "gws - storm": "STORM",
    "gws - capital": "CAPITAL",
    "gws - money house": "MONEY HOUSE",
    "gws (vip)": "VIP",
    "gws - vip": "VIP",
    "gws vip": "VIP",
    "gws-vip": "VIP",
}
DEFAULT_PATH = "/app/data/gws-provider-channels.json"
SNOWFLAKE = re.compile(r"^[0-9]{15,22}$")


def provider_for_signal(signal: dict) -> str | None:
    """Return GWS source for Discord, never for authorization to trade."""
    if str(signal.get("source_style") or "").lower() != "gws":
        return None
    origin = source_id(signal)
    if origin == STORM_CHAT_ID:
        return "STORM"
    if origin == CAPITAL_CHAT_ID:
        return "CAPITAL"
    # Titles are user-mutable, so do not trust forwarded GWS entries
    # whose origin differs from their receiving Telegram chat.
    try:
        chat_id = int(signal["chat_id"])
    except (KeyError, ValueError, TypeError):
        return None
    if origin is None or origin != chat_id:
        return None
    title = " ".join(normalize("NFKC", str(signal.get("source_chat_title") or "")).split()).casefold()
    return PROVIDER_FROM_TITLE.get(title)


def routing_path() -> Path:
    return Path(os.getenv("GWS_PROVIDER_CHANNELS_PATH", DEFAULT_PATH))


def load_routes(path: Path, *, legacy_channel_id: int) -> dict[str, int] | None:
    """None means not activated. Invalid files fail closed."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != set(PROVIDER_CHANNEL_NAMES):
        raise ValueError("GWS channel mapping must contain all four provider names")
    if any(not isinstance(value, str) or not SNOWFLAKE.fullmatch(value) for value in data.values()):
        raise ValueError("GWS channel mapping has an invalid channel ID")
    if len(set(data.values())) != 4 or str(legacy_channel_id) in data.values():
        raise ValueError("GWS channels must be unique and distinct from legacy channel")
    return {name: int(channel) for name, channel in data.items()}


def destination_for_signal(
    signal: dict, routes: dict[str, int] | None, *, legacy_channel_id: int
) -> int | None:
    """Explicit routes only for GWS. Preserve non-GWS legacy destination."""
    if routes is None or str(signal.get("source_style") or "").lower() != "gws":
        return legacy_channel_id
    source = provider_for_signal(signal)
    return routes.get(source) if source else None


def purge_channel_entries(registry: dict, *, channel_id: int,
                          legacy_channel_id: int, timestamp: str) -> bool:
    """Respect /delall on one provider channel without losing other channels."""
    messages = registry.get("messages")
    if not isinstance(messages, dict):
        return False
    suppressed = registry.setdefault("suppressed", {})
    changed = False
    for sid, raw in list(messages.items()):
        item = raw if isinstance(raw, dict) else {"message_id": raw}
        target = int(item.get("channel_id") or legacy_channel_id)
        if target != channel_id:
            continue
        suppressed[sid] = timestamp
        messages.pop(sid, None)
        changed = True
    return changed


def _discord_request(token: str, method: str, route: str, data: dict | None = None):
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(
        "https://discord.com/api/v10" + route,
        data=payload, method=method,
        headers={
            "Authorization": "Bot " + token,
            "Content-Type": "application/json",
            "User-Agent": "dkcapital-gws-channel-setup/1",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def setup_channels(token: str, *, legacy_channel_id: int, path: Path) -> dict[str, int]:
    """Explicit CLI action: create/reuse GWS channels beside #gws-signals."""
    anchor = _discord_request(token, "GET", f"/channels/{legacy_channel_id}")
    if anchor.get("name") != "gws-signals" or anchor.get("type") != 0:
        raise ValueError("Configured legacy channel is not the expected #gws-signals")
    guild_id = str(anchor.get("guild_id") or "")
    category_id = anchor.get("parent_id")
    if not SNOWFLAKE.fullmatch(guild_id):
        raise ValueError("GWS source channel is not a Discord guild text channel")
    existing = _discord_request(token, "GET", f"/guilds/{guild_id}/channels")
    if not isinstance(existing, list):
        raise ValueError("Discord server channels could not be enumerated")
    mapping: dict[str, str] = {}
    for source, name in PROVIDER_CHANNEL_NAMES.items():
        matches = [ch for ch in existing if ch.get("name") == name
                   and ch.get("parent_id") == category_id and ch.get("type") == 0]
        if len(matches) > 1:
            raise ValueError(f"More than one #{name}; refusing ambiguous channel")
        if len(matches) == 1:
            channel = matches[0]
        else:
            channel = _discord_request(
                token, "POST", f"/guilds/{guild_id}/channels",
                {"name": name, "type": 0, "parent_id": category_id,
                 "topic": f"GWS {source} XAUUSD signals from Telegram"},
            )
        cid = str(channel.get("id") or "")
        if not SNOWFLAKE.fullmatch(cid):
            raise ValueError(f"Invalid Discord channel ID returned for {source}")
        mapping[source] = cid

    if len(set(mapping.values())) != 4 or str(legacy_channel_id) in mapping.values():
        raise ValueError("Discord assigned overlapping GWS destination IDs")

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = load_routes(path, legacy_channel_id=legacy_channel_id)
        if old == {key: int(v) for key, v in mapping.items()}:
            return old
    tmp = path.with_name(path.name + ".pending")
    fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(mapping, handle, sort_keys=True, indent=2)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {key: int(value) for key, value in mapping.items()}


def main() -> None:
    if sys.argv[1:] != ["setup"]:
        raise SystemExit("Usage: python -m dkcapital.gws_discord_routing setup")
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    legacy = int(os.getenv("DISCORD_INTERNAL_SIGNALS_CHANNEL_ID", "1554231371013169235"))
    if not token or legacy <= 0:
        raise SystemExit("Missing DK Capital bot credentials or GWS channel configuration")
    channels = setup_channels(token, legacy_channel_id=legacy, path=routing_path())
    for name, channel_id in channels.items():
        print(f"{name}: {channel_id}")
    print("GWS channel mapping saved. The existing Discord bot service must be restarted to activate routing.")


if __name__ == "__main__":
    main()
