import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from adacord.persistence import save_player_state
from adacord.player import (
    MissingVoicePermissions,
    add_tracks,
    disconnect_player,
    ensure_player,
    queue_items,
    validate_voice_channel_permissions,
)
from adacord.sources import load_tracks
from adacord.state import get_guild_state
from adacord.ui import (
    acknowledge,
    create_or_update_display,
    player_for_interaction,
    respond,
    update_display_for_guild,
)

logger = logging.getLogger(__name__)


async def refresh_display_with_recommendations(guild_id: int, player) -> None:
    try:
        await update_display_for_guild(guild_id, player)
    except Exception:
        logger.exception("Failed to refresh player display with recommendations")


def user_voice_channel(interaction: discord.Interaction) -> discord.VoiceChannel | discord.StageChannel | None:
    if not isinstance(interaction.user, discord.Member):
        return None
    voice = interaction.user.voice
    return voice.channel if voice else None


async def play_impl(interaction: discord.Interaction, query: str) -> None:
    if not interaction.guild:
        await respond(interaction, "This command can only be used in a server.", ephemeral=True)
        return

    channel = user_voice_channel(interaction)
    if not channel:
        await respond(interaction, "Join a voice channel first.", ephemeral=True)
        return

    try:
        validate_voice_channel_permissions(interaction.guild, channel)
    except MissingVoicePermissions as exc:
        await respond(interaction, str(exc), ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        player = await ensure_player(interaction.guild, channel)
    except MissingVoicePermissions as exc:
        await respond(interaction, str(exc), ephemeral=True)
        return
    except Exception as exc:
        logger.exception("Failed to connect Lavalink player to voice")
        await respond(interaction, f"Could not connect to voice: {exc}", ephemeral=True)
        return

    state = get_guild_state(interaction.guild.id)
    state.text_channel = interaction.channel

    try:
        tracks, summary = await load_tracks(query, str(interaction.user))
    except Exception as exc:
        logger.exception("Failed to load query %r", query)
        await respond(interaction, f"Could not load that request: {exc}", ephemeral=True)
        return

    if not tracks:
        await respond(interaction, "No playable tracks were found.", ephemeral=True)
        return

    was_idle = not player.current and player.queue.is_empty
    try:
        await add_tracks(player, tracks)
    except Exception as exc:
        logger.exception("Failed to start playback for query %r", query)
        await respond(interaction, f"Could not start playback: {exc}", ephemeral=True)
        return

    if was_idle and interaction.channel:
        await create_or_update_display(interaction.guild.id, interaction.channel, player, manage_refresh=False)
    else:
        await update_display_for_guild(interaction.guild.id, player, manage_refresh=False)
    await save_player_state(player)

    await acknowledge(interaction)
    asyncio.create_task(refresh_display_with_recommendations(interaction.guild.id, player))


async def disconnect_impl(interaction: discord.Interaction) -> None:
    player = player_for_interaction(interaction)
    if not player:
        await respond(interaction, "Not connected.", ephemeral=True)
        return
    guild_id = player.guild.id
    await disconnect_player(player)
    await acknowledge(interaction)
    await update_display_for_guild(guild_id, None)


async def remove_impl(interaction: discord.Interaction, position: int) -> None:
    player = player_for_interaction(interaction)
    tracks = queue_items(player) if player else []
    if not tracks:
        await respond(interaction, "Queue is empty.", ephemeral=True)
        return
    if position < 1:
        await respond(interaction, "Queue positions start at 1.", ephemeral=True)
        return
    if position > len(tracks):
        await respond(interaction, f"Queue only has {len(tracks)} tracks.", ephemeral=True)
        return
    removed = tracks[position - 1]
    del player.queue[position - 1]
    await save_player_state(player)
    await acknowledge(interaction)
    await update_display_for_guild(player.guild.id, player)


async def move_impl(interaction: discord.Interaction, from_pos: int, to_pos: int) -> None:
    player = player_for_interaction(interaction)
    tracks = queue_items(player) if player else []
    if not tracks:
        await respond(interaction, "Queue is empty.", ephemeral=True)
        return
    if from_pos < 1 or to_pos < 1:
        await respond(interaction, "Queue positions start at 1.", ephemeral=True)
        return
    if from_pos > len(tracks) or to_pos > len(tracks):
        await respond(interaction, f"Queue only has {len(tracks)} tracks.", ephemeral=True)
        return
    track = tracks[from_pos - 1]
    del player.queue[from_pos - 1]
    player.queue.put_at(to_pos - 1, track)
    await save_player_state(player)
    await acknowledge(interaction)
    await update_display_for_guild(player.guild.id, player)


def setup_all_commands(bot: commands.Bot) -> None:
    @bot.tree.command(name="play", description="Play a YouTube URL/search or Spotify playlist link")
    @app_commands.describe(query="YouTube URL, search terms, or Spotify playlist URL")
    async def play(interaction: discord.Interaction, query: str) -> None:
        await play_impl(interaction, query)

    @bot.tree.command(name="p", description="Short alias for /play")
    @app_commands.describe(query="YouTube URL, search terms, or Spotify playlist URL")
    async def play_alias(interaction: discord.Interaction, query: str) -> None:
        await play_impl(interaction, query)

    @bot.tree.command(name="disconnect", description="Disconnect from voice")
    async def disconnect(interaction: discord.Interaction) -> None:
        await disconnect_impl(interaction)

    @bot.tree.command(name="dc", description="Short alias for /disconnect")
    async def disconnect_alias(interaction: discord.Interaction) -> None:
        await disconnect_impl(interaction)

    @bot.tree.command(name="remove", description="Remove a queued track by position")
    @app_commands.describe(position="1-based queue position")
    async def remove(interaction: discord.Interaction, position: app_commands.Range[int, 1, 500]) -> None:
        await remove_impl(interaction, int(position))

    @bot.tree.command(name="move", description="Move a queued track")
    async def move(
        interaction: discord.Interaction,
        from_pos: app_commands.Range[int, 1, 500],
        to_pos: app_commands.Range[int, 1, 500],
    ) -> None:
        await move_impl(interaction, int(from_pos), int(to_pos))

