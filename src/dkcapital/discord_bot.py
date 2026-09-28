from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from dkcapital.config import Settings
from dkcapital.logging_setup import configure_logging

logger = logging.getLogger("dkcapital.discord")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def _price(value: Any) -> str:
    if value is None:
        return "Not set"
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _dashboard_embed(state: dict[str, Any]) -> discord.Embed:
    signals = list(state.get("signals") or [])
    active = [signal for signal in signals if signal.get("status") == "ACTIVE"]
    recent = [signal for signal in signals if signal.get("status") != "ACTIVE"][-5:]

    embed = discord.Embed(
        title="DK Capital | Live Trade Dashboard",
        description="Automatically maintained from the DK Capital source feed.",
    )

    if not active:
        embed.add_field(
            name="Active trades",
            value="No active tracked trades.",
            inline=False,
        )
    else:
        for signal in active[-12:]:
            low = _price(signal.get("entry_low"))
            high = _price(signal.get("entry_high"))
            entry = low if low == high else f"{low} - {high}"
            direction = str(signal.get("direction") or "?")
            symbol = str(signal.get("symbol") or "?")

            tps = signal.get("tps") or {}
            hits = {int(value) for value in signal.get("tp_hits") or []}
            tp_lines: list[str] = []
            for raw_index, target in sorted(tps.items(), key=lambda item: int(item[0])):
                index = int(raw_index)
                marker = "✅" if index in hits else "⬜"
                tp_lines.append(f"{marker} TP{index}: {_price(target)}")

            if signal.get("sl_mode") == "BREAKEVEN":
                sl_text = "BE"
            elif signal.get("sl_mode") == "UNSET":
                sl_text = "Not set"
            else:
                sl_text = _price(signal.get("current_sl"))

            details = tp_lines or ["Targets not supplied"]
            details.append(f"SL: **{sl_text}**")
            details.append(
                f"Layers: {signal.get('layers', 1)} | Re-entries: {signal.get('reentries', 0)}"
            )
            partials = int(signal.get("partial_close_count", 0) or 0)
            if partials:
                remaining = float(signal.get("remaining_fraction", 1.0) or 0.0) * 100
                details.append(
                    f"Partials: {partials} | Remaining: {remaining:.2f}%"
                )
            if signal.get("layer_max") is not None:
                details.append(f"Layer max: {_price(signal.get('layer_max'))}")

            embed.add_field(
                name=f"{direction} {symbol} | {entry}",
                value="\n".join(details),
                inline=False,
            )

    if recent:
        lines = []
        for signal in reversed(recent):
            lines.append(
                f"`{signal.get('status')}` {signal.get('direction')} {signal.get('symbol')} "
                f"{_price(signal.get('entry_low'))}-{_price(signal.get('entry_high'))}"
            )
        embed.add_field(name="Recent completed/closed", value="\n".join(lines), inline=False)

    unresolved = len(state.get("unresolved_actions") or [])
    updated = state.get("updated_at")
    footer = f"Parser exceptions awaiting review: {unresolved}"
    if updated:
        footer += f" | State updated {updated}"
    embed.set_footer(text=footer)
    return embed


def _provider_name(signal: dict[str, Any]) -> str:
    style = str(signal.get("source_style") or "").lower()
    if style == "elite":
        return "Elite Portfolios"
    if style == "gws":
        return "GWS"
    return "Telegram Provider"


def _signal_fingerprint(signal: dict[str, Any]) -> str:
    relevant = {
        "direction": signal.get("direction"),
        "symbol": signal.get("symbol"),
        "entry_low": signal.get("entry_low"),
        "entry_high": signal.get("entry_high"),
        "tps": signal.get("tps"),
        "current_sl": signal.get("current_sl"),
        "sl_mode": signal.get("sl_mode"),
        "tp_hits": signal.get("tp_hits"),
        "layers": signal.get("layers"),
        "reentries": signal.get("reentries"),
        "layer_max": signal.get("layer_max"),
        "remaining_fraction": signal.get("remaining_fraction"),
        "partial_close_count": signal.get("partial_close_count"),
        "status": signal.get("status"),
        "last_update_at": signal.get("last_update_at"),
    }
    return json.dumps(relevant, sort_keys=True, separators=(",", ":"))


def _signal_embed(signal: dict[str, Any]) -> discord.Embed:
    direction = str(signal.get("direction") or "?")
    symbol = str(signal.get("symbol") or "?")
    provider = _provider_name(signal)
    status = str(signal.get("status") or "UNKNOWN")

    low = _price(signal.get("entry_low"))
    high = _price(signal.get("entry_high"))
    entry = low if low == high else f"{low} - {high}"

    embed = discord.Embed(
        title=f"{direction} {symbol}",
        description=f"**Internal parsed signal**\nSource: **{provider}**",
    )
    embed.add_field(name="Entry", value=entry, inline=True)

    if signal.get("sl_mode") == "BREAKEVEN":
        sl_text = "BE"
    elif signal.get("sl_mode") == "UNSET":
        sl_text = "Not set"
    else:
        sl_text = _price(signal.get("current_sl"))
    embed.add_field(name="Stop Loss", value=sl_text, inline=True)
    embed.add_field(name="Status", value=status, inline=True)

    tps = signal.get("tps") or {}
    hits = {int(value) for value in signal.get("tp_hits") or []}
    if tps:
        tp_lines: list[str] = []
        for raw_index, target in sorted(tps.items(), key=lambda item: int(item[0])):
            index = int(raw_index)
            marker = "✅" if index in hits else "⬜"
            tp_lines.append(f"{marker} TP{index}: {_price(target)}")
        embed.add_field(name="Targets", value="\n".join(tp_lines), inline=False)
    else:
        embed.add_field(name="Take Profit", value="Open", inline=False)

    partials = int(signal.get("partial_close_count", 0) or 0)
    if partials:
        remaining = float(signal.get("remaining_fraction", 1.0) or 0.0) * 100
        embed.add_field(
            name="Position Management",
            value=f"Partials taken: {partials}\nRemaining: {remaining:.2f}%",
            inline=False,
        )

    extra: list[str] = []
    layers = int(signal.get("layers", 1) or 1)
    reentries = int(signal.get("reentries", 0) or 0)
    if layers > 1:
        extra.append(f"Layers: {layers}")
    if reentries:
        extra.append(f"Re-entries: {reentries}")
    if signal.get("layer_max") is not None:
        extra.append(f"Layer max: {_price(signal.get('layer_max'))}")
    if extra:
        embed.add_field(name="Setup", value="\n".join(extra), inline=False)

    opened_at = signal.get("opened_at")
    root_message_id = signal.get("root_message_id")
    footer = "INTERNAL ONLY"
    if root_message_id is not None:
        footer += f" | Telegram message {root_message_id}"
    if opened_at:
        footer += f" | {opened_at}"
    embed.set_footer(text=footer)
    return embed


def build_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    async def update_dashboard() -> bool:
        config = _load_json(settings.discord_dashboard_config_path)
        channel_id = config.get("channel_id")
        message_id = config.get("message_id")
        if not channel_id or not message_id:
            return False

        state = _load_json(settings.signal_state_path)
        if not state:
            return False

        try:
            channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            await message.edit(embed=_dashboard_embed(state))
            return True
        except discord.NotFound:
            logger.warning("Configured dashboard message no longer exists")
        except discord.Forbidden:
            logger.exception("Discord bot cannot edit the configured dashboard")
        except discord.HTTPException:
            logger.exception("Discord dashboard update failed")
        return False

    async def sync_internal_signals() -> None:
        state = _load_json(settings.signal_state_path)
        signals = list(state.get("signals") or [])
        registry = _load_json(settings.discord_internal_signals_state_path)

        if not registry.get("initialized"):
            registry = {
                "initialized": True,
                "watermark": datetime.now(UTC).isoformat(),
                "messages": {},
            }
            _write_json(settings.discord_internal_signals_state_path, registry)
            logger.info(
                "Internal signal feed initialized channel=%s watermark=%s",
                settings.discord_internal_signals_channel_id,
                registry["watermark"],
            )
            return

        channel_id = settings.discord_internal_signals_channel_id
        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception("Unable to access internal signal channel %s", channel_id)
            return

        if not hasattr(channel, "send"):
            logger.error("Configured internal signal channel %s is not messageable", channel_id)
            return

        messages = registry.setdefault("messages", {})
        watermark = str(registry.get("watermark") or "")
        changed = False

        # First update messages we have already published.
        for signal in signals:
            signal_id = str(signal.get("signal_id") or "")
            if not signal_id or signal_id not in messages:
                continue

            record = messages.get(signal_id)
            if isinstance(record, int):
                record = {"message_id": record, "fingerprint": ""}
            elif not isinstance(record, dict):
                record = {}

            message_id = record.get("message_id")
            fingerprint = _signal_fingerprint(signal)
            if message_id and record.get("fingerprint") == fingerprint:
                continue

            try:
                if message_id:
                    message = await channel.fetch_message(int(message_id))
                    await message.edit(embed=_signal_embed(signal))
                else:
                    message = await channel.send(embed=_signal_embed(signal))
                messages[signal_id] = {
                    "message_id": message.id,
                    "fingerprint": fingerprint,
                }
                changed = True
            except discord.NotFound:
                message = await channel.send(embed=_signal_embed(signal))
                messages[signal_id] = {
                    "message_id": message.id,
                    "fingerprint": fingerprint,
                }
                changed = True
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Failed updating internal signal %s", signal_id)

        # Then publish new signals after the persisted watermark. Historical
        # backfill from before the feed was enabled is intentionally not dumped.
        for signal in sorted(signals, key=lambda item: str(item.get("opened_at") or "")):
            signal_id = str(signal.get("signal_id") or "")
            opened_at = str(signal.get("opened_at") or "")
            if not signal_id or signal_id in messages:
                continue
            if not opened_at or (watermark and opened_at <= watermark):
                continue

            try:
                message = await channel.send(embed=_signal_embed(signal))
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Failed publishing internal signal %s", signal_id)
                break

            messages[signal_id] = {
                "message_id": message.id,
                "fingerprint": _signal_fingerprint(signal),
            }
            watermark = opened_at
            registry["watermark"] = watermark
            changed = True
            logger.info(
                "Published internal signal signal_id=%s channel=%s message=%s",
                signal_id,
                channel_id,
                message.id,
            )

        if changed:
            _write_json(settings.discord_internal_signals_state_path, registry)

    @tasks.loop(seconds=2)
    async def internal_signal_loop() -> None:
        await sync_internal_signals()

    @internal_signal_loop.before_loop
    async def before_internal_signal_loop() -> None:
        await bot.wait_until_ready()

    @tasks.loop(seconds=5)
    async def dashboard_loop() -> None:
        await update_dashboard()

    @dashboard_loop.before_loop
    async def before_dashboard_loop() -> None:
        await bot.wait_until_ready()

    @bot.event
    async def on_ready() -> None:
        assert bot.user is not None
        logger.info("Discord connected user=%s user_id=%s", bot.user, bot.user.id)

        if settings.discord_guild_id:
            guild = discord.Object(id=settings.discord_guild_id)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logger.info("Synced %s Discord command(s) to guild %s", len(synced), guild.id)
        else:
            synced = await bot.tree.sync()
            logger.info("Synced %s global Discord command(s)", len(synced))

        if not dashboard_loop.is_running():
            dashboard_loop.start()
        if not internal_signal_loop.is_running():
            internal_signal_loop.start()

    @bot.tree.command(name="ping", description="Check whether the DK Capital bot is online.")
    async def ping(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("DK Capital bot is online.", ephemeral=True)

    @bot.tree.command(
        name="dashboard_create",
        description="Create the auto-updating DK Capital live trade dashboard in this channel.",
    )
    @app_commands.default_permissions(administrator=True)
    async def dashboard_create(interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Run this command inside the DK Capital server.",
                ephemeral=True,
            )
            return

        state = _load_json(settings.signal_state_path)
        await interaction.response.send_message(embed=_dashboard_embed(state))
        message = await interaction.original_response()

        _write_json(
            settings.discord_dashboard_config_path,
            {
                "guild_id": interaction.guild.id,
                "channel_id": interaction.channel.id,
                "message_id": message.id,
            },
        )
        logger.info(
            "Dashboard configured guild=%s channel=%s message=%s",
            interaction.guild.id,
            interaction.channel.id,
            message.id,
        )

    @bot.tree.command(
        name="dashboard_refresh",
        description="Force an immediate refresh of the DK Capital dashboard.",
    )
    @app_commands.default_permissions(administrator=True)
    async def dashboard_refresh(interaction: discord.Interaction) -> None:
        updated = await update_dashboard()
        await interaction.response.send_message(
            "Dashboard refreshed." if updated else "No configured dashboard was found.",
            ephemeral=True,
        )

    return bot


def main() -> None:
    settings = Settings.from_env()
    settings.validate_discord()
    configure_logging(settings.log_level)

    bot = build_bot(settings)
    bot.run(settings.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
