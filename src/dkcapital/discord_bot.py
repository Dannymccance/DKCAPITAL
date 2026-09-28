from __future__ import annotations

import json
import logging
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
        return "N/A"
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
