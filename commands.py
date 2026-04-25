import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
import wavelink

from audio import (
    add_tracks,
    clear_player,
    default_volume,
    disconnect_player,
    ensure_player,
    format_duration,
    get_guild_state,
    get_player,
    load_tracks,
    play_next,
    queue_items,
    set_loop_mode,
    set_volume,
)

logger = logging.getLogger(__name__)


def message_delete_after() -> float:
    raw_value = os.getenv("MESSAGE_DELETE_AFTER", "5").strip()
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return 5.0


async def delete_message_later(message: discord.Message, delay: float) -> None:
    if delay <= 0:
        return
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except discord.HTTPException:
        pass


def schedule_delete(message: discord.Message | None, delay: float) -> None:
    if message and delay > 0:
        asyncio.create_task(delete_message_later(message, delay))


async def send_transient(
    channel: discord.abc.Messageable,
    message: str,
) -> discord.Message | None:
    delay = message_delete_after()
    sent = await channel.send(message)
    schedule_delete(sent, delay)
    return sent


def track_requester(track: wavelink.Playable) -> str | None:
    try:
        return track.extras.requester
    except AttributeError:
        return None


def track_display_title(track: wavelink.Playable) -> str:
    try:
        return track.extras.display_title
    except AttributeError:
        return track.title


def player_for_interaction(interaction: discord.Interaction) -> wavelink.Player | None:
    if not interaction.guild:
        return None
    return get_player(interaction.guild)


async def respond(
    interaction: discord.Interaction,
    message: str,
    *,
    ephemeral: bool = False,
) -> None:
    ephemeral = False
    delay = message_delete_after()
    if interaction.response.is_done():
        sent = await interaction.followup.send(
            message,
            ephemeral=ephemeral,
            wait=delay > 0,
        )
        schedule_delete(sent, delay)
    else:
        await interaction.response.send_message(
            message,
            ephemeral=ephemeral,
            delete_after=delay or None,
        )


def build_player_embed(player: wavelink.Player | None, guild_id: int) -> discord.Embed:
    state = get_guild_state(guild_id)
    current = player.current if player else None
    paused = bool(player and player.paused)
    color = discord.Color.dark_grey()
    if current:
        color = discord.Color.yellow() if paused else discord.Color.green()

    embed = discord.Embed(title="Music Player", color=color, timestamp=datetime.now(timezone.utc))

    if current:
        duration = format_duration(current.length)
        title = f"**{track_display_title(current)}**"
        if duration:
            title += f" `[{duration}]`"
        embed.add_field(name="Now Playing", value=title, inline=False)
        requester = track_requester(current)
        if requester:
            embed.add_field(name="Requested by", value=requester, inline=True)
    else:
        embed.add_field(name="Now Playing", value="Nothing playing", inline=False)

    volume = player.volume if player else default_volume()
    embed.add_field(name="Volume", value=f"{volume}%", inline=True)
    if state.loop_mode != "none":
        embed.add_field(name="Loop", value=state.loop_mode.title(), inline=True)

    tracks = queue_items(player) if player else []
    if tracks:
        preview = []
        for index, track in enumerate(tracks[:5], start=1):
            duration = format_duration(track.length)
            suffix = f" `[{duration}]`" if duration else ""
            preview.append(f"`{index}.` {track_display_title(track)}{suffix}")
        if len(tracks) > 5:
            preview.append(f"...and {len(tracks) - 5} more")
        embed.add_field(name=f"Queue ({len(tracks)})", value="\n".join(preview), inline=False)
    else:
        embed.add_field(name="Queue", value="Empty", inline=False)

    return embed


class PlayerControls(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    async def refresh(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        await update_display_for_guild(self.guild_id, player)

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0)
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player or not player.current:
            await respond(interaction, "Nothing to restart.", ephemeral=True)
            return
        await player.seek(0)
        await respond(interaction, "Restarted.", ephemeral=True)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player or not player.current:
            await respond(interaction, "Nothing to pause or resume.", ephemeral=True)
            return
        should_pause = not player.paused
        await player.pause(should_pause)
        await respond(interaction, "Paused." if should_pause else "Resumed.", ephemeral=True)
        await self.refresh(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player or not player.current:
            await respond(interaction, "Nothing to skip.", ephemeral=True)
            return
        await player.skip(force=True)
        await respond(interaction, "Skipped.", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        await clear_player(player)
        await respond(interaction, "Stopped and cleared the queue.", ephemeral=True)
        await self.refresh(interaction)

    @discord.ui.button(label="-10%", style=discord.ButtonStyle.secondary, row=1)
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        new_volume = max(0, player.volume - 10)
        await set_volume(player, new_volume)
        await respond(interaction, f"Volume: {new_volume}%", ephemeral=True)
        await self.refresh(interaction)

    @discord.ui.button(label="+10%", style=discord.ButtonStyle.secondary, row=1)
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        new_volume = min(200, player.volume + 10)
        await set_volume(player, new_volume)
        await respond(interaction, f"Volume: {new_volume}%", ephemeral=True)
        await self.refresh(interaction)

    @discord.ui.button(label="Mute", style=discord.ButtonStyle.secondary, row=1)
    async def mute(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        state = get_guild_state(self.guild_id)
        if player.volume > 0:
            state.previous_volume = player.volume
            await set_volume(player, 0)
            await respond(interaction, "Muted.", ephemeral=True)
        else:
            volume = state.previous_volume or default_volume()
            await set_volume(player, volume)
            await respond(interaction, f"Volume: {volume}%", ephemeral=True)
        await self.refresh(interaction)

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.secondary, row=2)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player or player.queue.is_empty:
            await respond(interaction, "Queue is empty.", ephemeral=True)
            return
        player.queue.shuffle()
        await respond(interaction, f"Shuffled {len(player.queue)} tracks.", ephemeral=True)
        await self.refresh(interaction)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.secondary, row=2)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        state = get_guild_state(self.guild_id)
        modes = ["none", "track", "queue"]
        mode = modes[(modes.index(state.loop_mode) + 1) % len(modes)]
        set_loop_mode(player, mode)
        await respond(interaction, f"Loop: {mode}", ephemeral=True)
        await self.refresh(interaction)

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary, row=2)
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        await interaction.response.send_message(
            embed=build_queue_embed(player, page=0),
            view=QueueView(self.guild_id, player),
            ephemeral=True,
        )


class QueueView(discord.ui.View):
    def __init__(self, guild_id: int, player: wavelink.Player | None):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.page = 0
        self.total_pages = queue_page_count(player)

    async def edit(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        self.total_pages = queue_page_count(player)
        self.page = min(self.page, self.total_pages - 1)
        await interaction.response.edit_message(
            embed=build_queue_embed(player, self.page),
            view=self,
        )

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        await self.edit(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.total_pages - 1, self.page + 1)
        await self.edit(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.success)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.edit(interaction)


def queue_page_count(player: wavelink.Player | None) -> int:
    count = len(queue_items(player)) if player else 0
    return max(1, (count + 9) // 10)


def build_queue_embed(player: wavelink.Player | None, page: int = 0) -> discord.Embed:
    embed = discord.Embed(title="Music Queue", color=discord.Color.blurple())
    if player and player.current:
        embed.add_field(name="Now Playing", value=f"**{track_display_title(player.current)}**", inline=False)

    tracks = queue_items(player) if player else []
    if not tracks:
        embed.add_field(name="Up Next", value="Empty", inline=False)
        return embed

    start = page * 10
    visible = tracks[start : start + 10]
    lines = []
    for offset, track in enumerate(visible, start=1):
        duration = format_duration(track.length)
        suffix = f" `[{duration}]`" if duration else ""
        lines.append(f"`{start + offset}.` **{track_display_title(track)}**{suffix}")
    embed.add_field(name=f"Up Next ({len(tracks)})", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Page {page + 1} of {queue_page_count(player)}")
    return embed


async def create_or_update_display(
    guild_id: int,
    channel: discord.abc.Messageable,
    player: wavelink.Player | None,
) -> discord.Message | None:
    state = get_guild_state(guild_id)
    embed = build_player_embed(player, guild_id)
    view = PlayerControls(guild_id)

    try:
        if state.display_message:
            try:
                await state.display_message.edit(embed=embed, view=view)
                return state.display_message
            except (discord.NotFound, discord.HTTPException):
                state.display_message = None

        message = await channel.send(embed=embed, view=view)
        state.display_message = message
        state.display_channel = channel
        return message
    except Exception:
        logger.exception("Failed to create or update music display")
        return None


async def update_display_for_guild(
    guild_id: int,
    player: wavelink.Player | None = None,
) -> None:
    state = get_guild_state(guild_id)
    has_music = bool(player and (player.current or not player.queue.is_empty))
    if state.display_channel and has_music:
        await create_or_update_display(guild_id, state.display_channel, player)
    elif state.display_message and not has_music:
        try:
            await state.display_message.delete()
        except discord.HTTPException:
            pass
        state.display_message = None
        state.display_channel = None


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
        player = player_for_interaction(interaction)
        if not player or not player.current:
            await respond(interaction, "Nothing is playing.", ephemeral=True)
            return
        await player.pause(True)
        await respond(interaction, "Paused.")
        await update_display_for_guild(player.guild.id, player)

    @bot.tree.command(name="resume", description="Resume the paused track")
    async def resume(interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player or not player.paused:
            await respond(interaction, "Nothing is paused.", ephemeral=True)
            return
        await player.pause(False)
        await respond(interaction, "Resumed.")
        await update_display_for_guild(player.guild.id, player)

    @bot.tree.command(name="queue", description="Show the music queue")
    async def queue(interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        await interaction.response.send_message(
            embed=build_queue_embed(player),
            view=QueueView(interaction.guild_id or 0, player),
            ephemeral=True,
        )

    @bot.tree.command(name="q", description="Short alias for /queue")
    async def queue_alias(interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        await interaction.response.send_message(
            embed=build_queue_embed(player),
            view=QueueView(interaction.guild_id or 0, player),
            ephemeral=True,
        )

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
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        await set_volume(player, int(level))
        await respond(interaction, f"Volume: {int(level)}%")
        await update_display_for_guild(player.guild.id, player)

    @bot.tree.command(name="shuffle", description="Shuffle the queue")
    async def shuffle(interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player or player.queue.is_empty:
            await respond(interaction, "Queue is empty.", ephemeral=True)
            return
        player.queue.shuffle()
        await respond(interaction, f"Shuffled {len(player.queue)} tracks.")
        await update_display_for_guild(player.guild.id, player)

    @bot.tree.command(name="remove", description="Remove a queued track by position")
    @app_commands.describe(position="1-based queue position")
    async def remove(interaction: discord.Interaction, position: app_commands.Range[int, 1, 500]) -> None:
        player = player_for_interaction(interaction)
        tracks = queue_items(player) if player else []
        if not tracks:
            await respond(interaction, "Queue is empty.", ephemeral=True)
            return
        if position > len(tracks):
            await respond(interaction, f"Queue only has {len(tracks)} tracks.", ephemeral=True)
            return
        removed = tracks[position - 1]
        del player.queue[position - 1]
        await respond(interaction, f"Removed **{track_display_title(removed)}**.")
        await update_display_for_guild(player.guild.id, player)

    @bot.tree.command(name="move", description="Move a queued track")
    async def move(
        interaction: discord.Interaction,
        from_pos: app_commands.Range[int, 1, 500],
        to_pos: app_commands.Range[int, 1, 500],
    ) -> None:
        player = player_for_interaction(interaction)
        tracks = queue_items(player) if player else []
        if not tracks:
            await respond(interaction, "Queue is empty.", ephemeral=True)
            return
        if from_pos > len(tracks) or to_pos > len(tracks):
            await respond(interaction, f"Queue only has {len(tracks)} tracks.", ephemeral=True)
            return
        track = tracks[from_pos - 1]
        del player.queue[from_pos - 1]
        player.queue.put_at(to_pos - 1, track)
        await respond(interaction, f"Moved **{track_display_title(track)}** to position {to_pos}.")
        await update_display_for_guild(player.guild.id, player)

    @bot.tree.command(name="loop", description="Set loop mode")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="none", value="none"),
            app_commands.Choice(name="track", value="track"),
            app_commands.Choice(name="queue", value="queue"),
        ]
    )
    async def loop(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        set_loop_mode(player, mode.value)
        await respond(interaction, f"Loop: {mode.value}")
        await update_display_for_guild(player.guild.id, player)

    @bot.tree.command(name="nowplaying", description="Show the current player")
    async def nowplaying(interaction: discord.Interaction) -> None:
        await nowplaying_impl(interaction)

    @bot.tree.command(name="np", description="Short alias for /nowplaying")
    async def nowplaying_alias(interaction: discord.Interaction) -> None:
        await nowplaying_impl(interaction)


async def handle_track_end(payload: wavelink.TrackEndEventPayload) -> None:
    player = payload.player
    if not player:
        return
    if not player.queue.is_empty:
        await play_next(player)
    await update_display_for_guild(player.guild.id, player)


async def handle_track_start(payload: wavelink.TrackStartEventPayload) -> None:
    if payload.player:
        await update_display_for_guild(payload.player.guild.id, payload.player)


async def handle_inactive_player(player: wavelink.Player) -> None:
    state = get_guild_state(player.guild.id)
    if state.display_channel:
        await send_transient(state.display_channel, "Disconnected after being idle.")
    await update_display_for_guild(player.guild.id, player)
