from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from dkcapital.config import Settings
from dkcapital.logging_setup import configure_logging

logger = logging.getLogger("dkcapital.discord")


def build_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

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

    @bot.tree.command(name="ping", description="Check whether the DK Capital bot is online.")
    async def ping(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("DK Capital bot is online.", ephemeral=True)

    return bot


def main() -> None:
    settings = Settings.from_env()
    settings.validate_discord()
    configure_logging(settings.log_level)

    bot = build_bot(settings)
    bot.run(settings.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
