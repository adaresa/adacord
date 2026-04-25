import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import wavelink

from audio import connect_lavalink
from commands import (
    handle_inactive_player,
    handle_track_end,
    handle_track_start,
    setup_all_commands,
)

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
GUILD_ID = os.getenv("DISCORD_GUILD_ID")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("wavelink").setLevel(logging.INFO)
logger = logging.getLogger(__name__)


class AdacordBot(commands.Bot):
    async def setup_hook(self) -> None:
        await connect_lavalink(self)

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s slash commands to guild %s", len(synced), GUILD_ID)
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global slash commands", len(synced))


intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

bot = AdacordBot(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
setup_all_commands(bot)


@bot.event
async def on_ready() -> None:
    assert bot.user is not None
    activity = discord.CustomActivity(name="kassu's music bot")
    await bot.change_presence(activity=activity, status=discord.Status.online)
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    logger.error(
        "Unhandled application command error",
        exc_info=(type(error), error, error.__traceback__),
    )
    message = "An unexpected error occurred. Check the bot logs for details."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.event
async def on_wavelink_node_ready(payload: wavelink.NodeReadyEventPayload) -> None:
    logger.info("Lavalink node %s is ready", payload.node.identifier)


@bot.event
async def on_wavelink_track_start(payload: wavelink.TrackStartEventPayload) -> None:
    await handle_track_start(payload)


@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload) -> None:
    await handle_track_end(payload)


@bot.event
async def on_wavelink_track_exception(payload: wavelink.TrackExceptionEventPayload) -> None:
    logger.error("Lavalink track exception: %s", payload.exception)
    if payload.player and not payload.player.queue.is_empty:
        await handle_track_end(payload)


@bot.event
async def on_wavelink_track_stuck(payload: wavelink.TrackStuckEventPayload) -> None:
    logger.warning("Lavalink track stuck: %s", payload.track)
    if payload.player and not payload.player.queue.is_empty:
        await handle_track_end(payload)


@bot.event
async def on_wavelink_inactive_player(player: wavelink.Player) -> None:
    await handle_inactive_player(player)
    await player.disconnect()


if __name__ == "__main__":
    if not TOKEN:
        logger.error("DISCORD_TOKEN is not set.")
        raise SystemExit(1)
    bot.run(TOKEN)
