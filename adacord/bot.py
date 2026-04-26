import logging

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import wavelink

from adacord.commands import setup_all_commands
from adacord.config import discord_guild_id, discord_token, log_level
from adacord.events import handle_inactive_player, handle_track_end, handle_track_start
from adacord.player import connect_lavalink
from adacord.recovery import restore_playback_state
from adacord.ui import PlayerControls

load_dotenv()

logging.basicConfig(
    level=getattr(logging, log_level(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("wavelink").setLevel(logging.INFO)
logger = logging.getLogger(__name__)


class AdacordBot(commands.Bot):
    playback_restored: bool = False

    async def setup_hook(self) -> None:
        controls = PlayerControls()
        self.add_view(controls)
        logger.info(
            "Registered persistent player controls: %s",
            ", ".join(str(item.custom_id) for item in controls.children),
        )

        await connect_lavalink(self)

        guild_id = discord_guild_id()
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s slash commands to guild %s", len(synced), guild_id)

            self.tree.clear_commands(guild=None)
            synced_global = await self.tree.sync()
            logger.info("Cleared %s global slash commands", len(synced_global))
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global slash commands", len(synced))


def create_bot() -> AdacordBot:
    intents = discord.Intents.default()
    intents.guilds = True
    intents.voice_states = True

    bot = AdacordBot(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
    setup_all_commands(bot)
    register_events(bot)
    return bot


def register_events(bot: AdacordBot) -> None:
    @bot.event
    async def on_ready() -> None:
        assert bot.user is not None
        activity = discord.CustomActivity(name="kassu's music bot")
        await bot.change_presence(activity=activity, status=discord.Status.online)
        logger.info("Logged in as %s (%s)", bot.user, bot.user.id)
        if not bot.playback_restored:
            bot.playback_restored = True
            await restore_playback_state(bot)

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
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except (discord.NotFound, discord.HTTPException):
            logger.exception("Failed to send application command error response")

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


bot = create_bot()


def run() -> None:
    token = discord_token()
    if not token:
        logger.error("DISCORD_TOKEN is not set.")
        raise SystemExit(1)
    bot.run(token)
