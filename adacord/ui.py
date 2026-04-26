import asyncio
import logging
from datetime import datetime, timezone

import discord
import wavelink

from adacord.persistence import save_player_state
from adacord.config import default_volume, message_delete_after
from adacord.player import clear_player, get_player, queue_items, set_loop_mode, set_volume
from adacord.state import get_guild_state
from adacord.utils import format_duration, track_display_title, track_requester

logger = logging.getLogger(__name__)


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
    delay = message_delete_after()
    try:
        if interaction.response.is_done():
            sent = await interaction.followup.send(
                message,
                ephemeral=ephemeral,
                wait=not ephemeral and delay > 0,
            )
            if not ephemeral:
                schedule_delete(sent, delay)
        else:
            await interaction.response.send_message(
                message,
                ephemeral=ephemeral,
                delete_after=None if ephemeral else delay or None,
            )
    except (discord.NotFound, discord.HTTPException):
        logger.exception("Failed to send interaction response")


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
    def __init__(self, guild_id: int | None = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    def guild_id_for(self, interaction: discord.Interaction) -> int:
        return interaction.guild_id or self.guild_id or 0

    async def refresh(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        await update_display_for_guild(self.guild_id_for(interaction), player)

    @discord.ui.button(
        emoji="\u23ee\ufe0f",
        style=discord.ButtonStyle.secondary,
        row=0,
        custom_id="adacord:player:restart",
    )
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player or not player.current:
            await respond(interaction, "Nothing to restart.")
            return
        await player.seek(0)
        await respond(interaction, "Restarted.")

    @discord.ui.button(
        emoji="\u23ef\ufe0f",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="adacord:player:pause_resume",
    )
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player or not player.current:
            await respond(interaction, "Nothing to pause or resume.")
            return
        should_pause = not player.paused
        await player.pause(should_pause)
        save_player_state(player)
        await respond(interaction, "Paused." if should_pause else "Resumed.")
        await self.refresh(interaction)

    @discord.ui.button(
        emoji="\u23ed\ufe0f",
        style=discord.ButtonStyle.secondary,
        row=0,
        custom_id="adacord:player:skip",
    )
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player or not player.current:
            await respond(interaction, "Nothing to skip.")
            return
        await player.skip(force=True)
        await respond(interaction, "Skipped.")

    @discord.ui.button(
        emoji="\u23f9\ufe0f",
        style=discord.ButtonStyle.danger,
        row=0,
        custom_id="adacord:player:stop",
    )
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.")
            return
        await clear_player(player)
        await respond(interaction, "Stopped and cleared the queue.")
        await self.refresh(interaction)

    @discord.ui.button(
        label="-10%",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="adacord:player:volume_down",
    )
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.")
            return
        current_volume = player.volume if player.volume is not None else default_volume()
        new_volume = max(0, current_volume - 10)
        await set_volume(player, new_volume)
        save_player_state(player)
        await respond(interaction, f"Volume: {new_volume}%")
        await self.refresh(interaction)

    @discord.ui.button(
        label="+10%",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="adacord:player:volume_up",
    )
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.")
            return
        current_volume = player.volume if player.volume is not None else default_volume()
        new_volume = min(200, current_volume + 10)
        await set_volume(player, new_volume)
        save_player_state(player)
        await respond(interaction, f"Volume: {new_volume}%")
        await self.refresh(interaction)

    @discord.ui.button(
        label="Mute",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="adacord:player:mute",
    )
    async def mute(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.")
            return
        state = get_guild_state(self.guild_id_for(interaction))
        current_volume = player.volume if player.volume is not None else default_volume()
        if current_volume > 0:
            state.previous_volume = current_volume
            await set_volume(player, 0)
            save_player_state(player)
            await respond(interaction, "Muted.")
        else:
            volume = state.previous_volume or default_volume()
            await set_volume(player, volume)
            save_player_state(player)
            await respond(interaction, f"Volume: {volume}%")
        await self.refresh(interaction)

    @discord.ui.button(
        label="Shuffle",
        style=discord.ButtonStyle.secondary,
        row=2,
        custom_id="adacord:player:shuffle",
    )
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player or player.queue.is_empty:
            await respond(interaction, "Queue is empty.")
            return
        player.queue.shuffle()
        save_player_state(player)
        await respond(interaction, f"Shuffled {len(player.queue)} tracks.")
        await self.refresh(interaction)

    @discord.ui.button(
        label="Loop",
        style=discord.ButtonStyle.secondary,
        row=2,
        custom_id="adacord:player:loop",
    )
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.")
            return
        state = get_guild_state(self.guild_id_for(interaction))
        modes = ["none", "track", "queue"]
        mode = modes[(modes.index(state.loop_mode) + 1) % len(modes)]
        set_loop_mode(player, mode)
        save_player_state(player)
        await respond(interaction, f"Loop: {mode}")
        await self.refresh(interaction)

    @discord.ui.button(
        label="Queue",
        style=discord.ButtonStyle.secondary,
        row=2,
        custom_id="adacord:player:queue",
    )
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = player_for_interaction(interaction)
        await interaction.response.send_message(
            embed=build_queue_embed(player, page=0),
            view=QueueView(self.guild_id_for(interaction), player),
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
                state.display_channel_id = getattr(state.display_channel, "id", None)
                state.display_message_id = getattr(state.display_message, "id", None)
                return state.display_message
            except (discord.NotFound, discord.HTTPException):
                state.display_message = None

        message = await channel.send(embed=embed, view=view)
        state.display_message = message
        state.display_channel = channel
        state.display_channel_id = getattr(channel, "id", None)
        state.display_message_id = getattr(message, "id", None)
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
        state.display_channel_id = None
        state.display_message_id = None
