import logging

import discord
from discord import app_commands
from discord.ext import commands

from adacord.persistence import save_player_state
from adacord.player import (
    add_tracks,
    clear_player,
    disconnect_player,
    ensure_player,
    queue_items,
    set_loop_mode,
    set_volume,
)
from adacord.sources import load_tracks
from adacord.state import get_guild_state
from adacord.ui import (
    PlayerControls,
    QueueView,
    build_player_embed,
    build_queue_embed,
    create_or_update_display,
    player_for_interaction,
    respond,
    update_display_for_guild,
)
from adacord.utils import track_display_title

logger = logging.getLogger(__name__)


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

    await interaction.response.defer(thinking=True)
    try:
        player = await ensure_player(interaction.guild, channel)
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
        await create_or_update_display(interaction.guild.id, interaction.channel, player)
    else:
        await update_display_for_guild(interaction.guild.id, player)
    await save_player_state(player)

    if summary.added == 1:
        await respond(interaction, f"Added: **{summary.title}**")
    else:
        await respond(interaction, f"Added {summary.added} tracks from **{summary.title}**.")


async def skip_impl(interaction: discord.Interaction) -> None:
    player = player_for_interaction(interaction)
    if not player or not player.current:
        await respond(interaction, "Nothing is playing.", ephemeral=True)
        return
    await player.skip(force=True)
    await respond(interaction, "Skipped.")


async def clear_impl(interaction: discord.Interaction) -> None:
    player = player_for_interaction(interaction)
    if not player:
        await respond(interaction, "Not connected.", ephemeral=True)
        return
    await clear_player(player)
    await respond(interaction, "Cleared the queue.")
    await update_display_for_guild(player.guild.id, player)


async def disconnect_impl(interaction: discord.Interaction) -> None:
    player = player_for_interaction(interaction)
    if not player:
        await respond(interaction, "Not connected.", ephemeral=True)
        return
    guild_id = player.guild.id
    await disconnect_player(player)
    await respond(interaction, "Disconnected.")
    await update_display_for_guild(guild_id, None)


async def nowplaying_impl(interaction: discord.Interaction) -> None:
    player = player_for_interaction(interaction)
    await interaction.response.send_message(
        embed=build_player_embed(player, interaction.guild_id or 0),
        view=PlayerControls(interaction.guild_id or 0),
        ephemeral=True,
    )


async def pause_impl(interaction: discord.Interaction) -> None:
    player = player_for_interaction(interaction)
    if not player or not player.current:
        await respond(interaction, "Nothing is playing.", ephemeral=True)
        return
    await player.pause(True)
    await save_player_state(player)
    await respond(interaction, "Paused.")
    await update_display_for_guild(player.guild.id, player)


async def resume_impl(interaction: discord.Interaction) -> None:
    player = player_for_interaction(interaction)
    if not player or not player.paused:
        await respond(interaction, "Nothing is paused.", ephemeral=True)
        return
    await player.pause(False)
    await save_player_state(player)
    await respond(interaction, "Resumed.")
    await update_display_for_guild(player.guild.id, player)


async def queue_impl(interaction: discord.Interaction) -> None:
    player = player_for_interaction(interaction)
    await interaction.response.send_message(
        embed=build_queue_embed(player),
        view=QueueView(interaction.guild_id or 0, player),
        ephemeral=True,
    )


async def volume_impl(interaction: discord.Interaction, level: int) -> None:
    player = player_for_interaction(interaction)
    if not player:
        await respond(interaction, "Not connected.", ephemeral=True)
        return
    volume = max(0, min(200, int(level)))
    await set_volume(player, volume)
    await save_player_state(player)
    await respond(interaction, f"Volume: {volume}%")
    await update_display_for_guild(player.guild.id, player)


async def shuffle_impl(interaction: discord.Interaction) -> None:
    player = player_for_interaction(interaction)
    if not player or player.queue.is_empty:
        await respond(interaction, "Queue is empty.", ephemeral=True)
        return
    player.queue.shuffle()
    await save_player_state(player)
    await respond(interaction, f"Shuffled {len(player.queue)} tracks.")
    await update_display_for_guild(player.guild.id, player)


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
    await respond(interaction, f"Removed **{track_display_title(removed)}**.")
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
    await respond(interaction, f"Moved **{track_display_title(track)}** to position {to_pos}.")
    await update_display_for_guild(player.guild.id, player)


async def loop_impl(interaction: discord.Interaction, mode: app_commands.Choice[str] | str) -> None:
    player = player_for_interaction(interaction)
    if not player:
        await respond(interaction, "Not connected.", ephemeral=True)
        return
    mode_value = mode.value if isinstance(mode, app_commands.Choice) else str(mode)
    set_loop_mode(player, mode_value)
    await save_player_state(player)
    await respond(interaction, f"Loop: {mode_value}")
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

    @bot.tree.command(name="skip", description="Skip the current track")
    async def skip(interaction: discord.Interaction) -> None:
        await skip_impl(interaction)

    @bot.tree.command(name="s", description="Short alias for /skip")
    async def skip_alias(interaction: discord.Interaction) -> None:
        await skip_impl(interaction)

    @bot.tree.command(name="pause", description="Pause the current track")
    async def pause(interaction: discord.Interaction) -> None:
        await pause_impl(interaction)

    @bot.tree.command(name="resume", description="Resume the paused track")
    async def resume(interaction: discord.Interaction) -> None:
        await resume_impl(interaction)

    @bot.tree.command(name="queue", description="Show the music queue")
    async def queue(interaction: discord.Interaction) -> None:
        await queue_impl(interaction)

    @bot.tree.command(name="q", description="Short alias for /queue")
    async def queue_alias(interaction: discord.Interaction) -> None:
        await queue_impl(interaction)

    @bot.tree.command(name="clear", description="Clear the queue and stop playback")
    async def clear(interaction: discord.Interaction) -> None:
        await clear_impl(interaction)

    @bot.tree.command(name="c", description="Short alias for /clear")
    async def clear_alias(interaction: discord.Interaction) -> None:
        await clear_impl(interaction)

    @bot.tree.command(name="disconnect", description="Disconnect from voice")
    async def disconnect(interaction: discord.Interaction) -> None:
        await disconnect_impl(interaction)

    @bot.tree.command(name="dc", description="Short alias for /disconnect")
    async def disconnect_alias(interaction: discord.Interaction) -> None:
        await disconnect_impl(interaction)

    @bot.tree.command(name="volume", description="Set playback volume from 0 to 200 percent")
    @app_commands.describe(level="Volume from 0 to 200")
    async def volume(interaction: discord.Interaction, level: app_commands.Range[int, 0, 200]) -> None:
        await volume_impl(interaction, int(level))

    @bot.tree.command(name="shuffle", description="Shuffle the queue")
    async def shuffle(interaction: discord.Interaction) -> None:
        await shuffle_impl(interaction)

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

    @bot.tree.command(name="loop", description="Set loop mode")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="none", value="none"),
            app_commands.Choice(name="track", value="track"),
            app_commands.Choice(name="queue", value="queue"),
        ]
    )
    async def loop(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        await loop_impl(interaction, mode)

    @bot.tree.command(name="nowplaying", description="Show the current player")
    async def nowplaying(interaction: discord.Interaction) -> None:
        await nowplaying_impl(interaction)

    @bot.tree.command(name="np", description="Short alias for /nowplaying")
    async def nowplaying_alias(interaction: discord.Interaction) -> None:
        await nowplaying_impl(interaction)

